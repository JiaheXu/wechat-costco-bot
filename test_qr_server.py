import base64
import io
import json
from pathlib import Path
import socket
import socketserver
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import fetch_qr
import qr_server
from costco_session import LOGIN_PATH
from fetch_qr import QRFetchError


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


class QRStartupTest(unittest.TestCase):
    def test_startup_fetch_logs_result_and_keeps_serving(self):
        cases = [
            (None, 'Startup QR check succeeded'),
            (QRFetchError('Costco rejected the request (code=501)'), 'code=501'),
            (RuntimeError('private-token-and-card'), 'RuntimeError'),
        ]
        for error, expected in cases:
            with self.subTest(error=error), tempfile.TemporaryDirectory() as directory:
                image = Path(directory) / 'qr.png'
                with patch('qr_server.ROOT', Path(directory)), \
                     patch('qr_server.os.umask'), \
                     patch('qr_server.logging.basicConfig'), \
                     patch('qr_server.RotatingFileHandler'), \
                     patch('qr_server.fetch_and_save', return_value=image, side_effect=error) as fetch, \
                     patch('qr_server.socketserver.ThreadingUDPServer') as server, \
                     self.assertLogs('qr_server', level='INFO') as logs:
                    listener = server.return_value.__enter__.return_value

                    def serve():
                        fetch.assert_called_once_with()
                        raise KeyboardInterrupt

                    listener.serve_forever.side_effect = serve
                    self.assertEqual(qr_server.main(), 0)
                    listener.serve_forever.assert_called_once_with()
                output = '\n'.join(logs.output)
                self.assertIn(expected, output)
                self.assertNotIn('private-token-and-card', output)


class QRServerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.image = Path(self.temp.name) / 'qr.png'
        self.image.write_bytes(PNG)
        patcher = patch('qr_server.fetch_and_save', return_value=self.image)
        self.fetch = patcher.start()
        self.addCleanup(patcher.stop)
        self.server = socketserver.ThreadingUDPServer(('127.0.0.1', 0), qr_server.QRHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01})
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def send(self, request):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(2)
            sock.connect(self.server.server_address)
            sock.send(json.dumps(request).encode())
            return json.loads(sock.recv(60_001))

    def test_request_returns_matching_id_and_png(self):
        response = self.send({'type': 'costco_qr', 'request_id': 'a' * 32})
        self.assertTrue(response['ok'])
        self.assertEqual(response['request_id'], 'a' * 32)
        self.assertEqual(base64.b64decode(response['image']), PNG)
        self.fetch.assert_called_once()

    def test_each_request_fetches_again(self):
        self.send({'type': 'costco_qr', 'request_id': 'a' * 32})
        response = self.send({'type': 'costco_qr', 'request_id': 'b' * 32})
        self.assertTrue(response['ok'])
        self.assertEqual(self.fetch.call_count, 2)

    def test_udp_requests_create_distinct_sessions_before_fetching_qr(self):
        events = []
        opener = Mock()

        def upstream(request, timeout):
            url = urlsplit(request.full_url)
            if url.path == LOGIN_PATH:
                code = parse_qs(url.query)['code'][0]
                self.assertNotIn('Cookie', request.headers)
                self.assertNotIn('Costco-authorization', request.headers)
                events.append(('login', code))
                body = {'code': 0, 'data': {'token': 'token-' + code,
                        'sessionId': 'session-' + code, 'memberInfo': {'cardNo': '12345678901'}}}
            else:
                self.assertEqual(url.path, urlsplit(fetch_qr.ENDPOINT).path)
                code = events[-1][1]
                self.assertEqual(request.get_header('Costco-authorization'), 'token-' + code)
                self.assertEqual(request.get_header('Cookie'), 'JSESSIONID=session-' + code)
                events.append(('qr', code))
                body = {'code': 0, 'data': 'data:image/png;base64,' + base64.b64encode(PNG).decode()}
            return io.BytesIO(json.dumps(body).encode())

        opener.open.side_effect = upstream
        with patch('qr_server.fetch_and_save', wraps=fetch_qr.fetch_and_save), \
             patch('fetch_qr.ROOT', Path(self.temp.name)), \
             patch('refresh_session.get_login_code', side_effect=['first', 'second']), \
             patch('refresh_session.build_opener', return_value=opener), \
             patch('fetch_qr.build_opener', return_value=opener):
            for request_id in ('a' * 32, 'b' * 32):
                reply = self.send({'type': 'costco_qr', 'request_id': request_id})
                self.assertTrue(reply['ok'], reply)
                self.assertEqual(reply['request_id'], request_id)
                self.assertTrue(base64.b64decode(reply['image']).startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(events, [('login', 'first'), ('qr', 'first'), ('login', 'second'), ('qr', 'second')])
        self.assertEqual(len(list((Path(self.temp.name) / 'images').glob('*.png'))), 2)

    def test_login_failure_is_returned_without_credentials(self):
        self.fetch.side_effect = QRFetchError('Costco rejected the request (code=501)')
        response = self.send({'type': 'costco_qr', 'request_id': 'a' * 32})
        self.assertFalse(response['ok'])
        self.assertIn('code=501', response['error'])
        self.assertNotIn('image', response)

    def test_unexpected_failure_does_not_return_exception_text(self):
        self.fetch.side_effect = RuntimeError('private-token-and-card')
        response = self.send({'type': 'costco_qr', 'request_id': 'a' * 32})
        self.assertFalse(response['ok'])
        self.assertNotIn('private-token-and-card', json.dumps(response))

    def test_invalid_command_does_not_fetch(self):
        response = self.send({'type': 'other', 'request_id': 'a' * 32})
        self.assertFalse(response['ok'])
        self.fetch.assert_not_called()

    def test_oversized_image_gets_an_error_reply(self):
        self.image.write_bytes(b'x' * 60_000)
        response = self.send({'type': 'costco_qr', 'request_id': 'a' * 32})
        self.assertFalse(response['ok'])
        self.assertIn('too large', response['error'])
