import base64
import json
from pathlib import Path
import socket
import socketserver
import tempfile
import threading
import unittest
from unittest.mock import patch

import qr_server
from fetch_qr import QRFetchError


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


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
