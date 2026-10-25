import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from urllib.parse import parse_qs, urlsplit

from PIL import Image

import fetch_qr


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


class FetchQRTest(unittest.TestCase):
    def test_loads_latest_qr_session_and_skips_unrelated_captures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture = root / 'https-001'
            capture.mkdir()
            (capture / 'flows.har').write_text(json.dumps({'log': {'entries': [{
                'request': {
                    'method': 'GET',
                    'url': fetch_qr.ENDPOINT + '?cardNo=12345678901&time=1000',
                    'headers': [{'name': 'Costco-Authorization', 'value': 'test-token'},
                                {'name': 'Cookie', 'value': 'test-cookie'}],
                }
            }]}}))
            other = root / 'https-002'
            other.mkdir()
            (other / 'flows.har').write_text('{"log":{"entries":[]}}')
            source, card, headers = fetch_qr.load_session(root)
            self.assertEqual(source, capture / 'flows.har')
            self.assertEqual(card, '12345678901')
            self.assertEqual(headers['costco-authorization'], 'test-token')

    def test_download_uses_fresh_timestamp_and_returns_png(self):
        payload = {'code': 0, 'data': 'data:image/png;base64,' + base64.b64encode(PNG).decode()}
        opener = Mock()
        opener.open.return_value = io.BytesIO(json.dumps(payload).encode())
        with patch('fetch_qr.build_opener', return_value=opener), patch('fetch_qr.time.time_ns', return_value=1234567890000000):
            image = fetch_qr.download_qr('12345678901', {'costco-authorization': 'test-token'})
        self.assertEqual(image, PNG)
        request = opener.open.call_args.args[0]
        url = urlsplit(request.full_url)
        self.assertEqual(url.scheme + '://' + url.netloc + url.path, fetch_qr.ENDPOINT)
        self.assertEqual(parse_qs(url.query), {'cardNo': ['12345678901'], 'time': ['1234567890']})

    def test_rejected_session_does_not_produce_an_image(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'{"code":401,"msg":"sensitive server text"}')
        with patch('fetch_qr.build_opener', return_value=opener):
            with self.assertRaisesRegex(ValueError, 'record a new session') as error:
                fetch_qr.download_qr('12345678901', {'costco-authorization': 'test-token'})
        self.assertNotIn('sensitive', str(error.exception))

    def test_api_rejection_has_safe_diagnostic_code(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'{"code":401,"msg":"private-token-and-card"}')
        with patch('fetch_qr.build_opener', return_value=opener):
            with self.assertRaises(ValueError) as error:
                fetch_qr.download_qr('12345678901', {'costco-authorization': 'test-token'})
        self.assertIn('code=401', error.exception.safe_message)
        self.assertNotIn('private', error.exception.safe_message)

    def test_invalid_json_is_reported_without_response_body(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'private invalid response')
        with patch('fetch_qr.build_opener', return_value=opener):
            with self.assertRaises(ValueError) as error:
                fetch_qr.download_qr('12345678901', {'costco-authorization': 'test-token'})
        self.assertIn('JSON', error.exception.safe_message)
        self.assertNotIn('private', error.exception.safe_message)

    def test_non_png_response_is_rejected(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'{"code":0,"data":"data:image/png;base64,bm90LXBuZw=="}')
        with patch('fetch_qr.build_opener', return_value=opener):
            with self.assertRaisesRegex(ValueError, 'PNG'):
                fetch_qr.download_qr('12345678901', {'costco-authorization': 'test-token'})

    def test_fetch_and_save_returns_private_png_in_images(self):
        original = Image.new('RGB', (3, 2), 'black')
        original.putpixel((1, 0), (10, 80, 150))
        buffer = io.BytesIO()
        original.save(buffer, format='PNG')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch('fetch_qr.ROOT', root), patch('fetch_qr.load_session', return_value=(root / 'capture.har', '12345678901', {})), patch('fetch_qr.download_qr', return_value=buffer.getvalue()), patch('builtins.print'):
                output = fetch_qr.fetch_and_save()
            image, = (root / 'images').glob('*.png')
            self.assertEqual(output, image)
            with Image.open(image) as padded:
                self.assertEqual(padded.size, (23, 22))
                self.assertEqual(padded.crop((10, 10, 13, 12)).tobytes(), original.tobytes())
                for box in [(0, 0, 23, 10), (0, 12, 23, 22), (0, 10, 10, 12), (13, 10, 23, 12)]:
                    self.assertEqual(padded.crop(box).getextrema(), ((255, 255),) * 3)
            self.assertEqual(image.stat().st_mode & 0o777, 0o600)

    def test_login_rejection_renews_session_and_retries_once(self):
        old = {'costco-authorization': 'old-token'}
        new = {'costco-authorization': 'new-token'}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = [(root / 'old.har', '12345678901', old),
                        (root / 'old.har', '12345678901', old),
                        (root / 'state/costco_session.json', '12345678901', new)]
            with patch('fetch_qr.ROOT', root), patch('fetch_qr.load_session', side_effect=sessions), patch('fetch_qr.download_qr', side_effect=[fetch_qr.QRFetchError('Rejected', code=501), PNG]) as download, patch('refresh_session.renew_session') as renew, patch('builtins.print'):
                result = fetch_qr.fetch_and_save()
            self.assertTrue(result.is_file())
            renew.assert_called_once_with(old, root / 'state/costco_session.json')
            self.assertEqual(download.call_args_list[1].args[1], new)

    def test_session_updated_by_collector_does_not_need_debugger(self):
        old = {'costco-authorization': 'old-token'}
        new = {'costco-authorization': 'new-token'}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = [(root / 'old.har', '12345678901', old),
                        (root / 'state/costco_session.json', '12345678901', new),
                        (root / 'state/costco_session.json', '12345678901', new)]
            with patch('fetch_qr.ROOT', root), patch('fetch_qr.load_session', side_effect=sessions), patch('fetch_qr.download_qr', side_effect=[fetch_qr.QRFetchError('Rejected', code=501), PNG]), patch('refresh_session.renew_session') as renew, patch('builtins.print'):
                self.assertTrue(fetch_qr.fetch_and_save().is_file())
            renew.assert_not_called()

    def test_retry_is_limited_to_one_after_renewal(self):
        old = {'costco-authorization': 'old-token'}
        new = {'costco-authorization': 'new-token'}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = [(root / 'old.har', '12345678901', old),
                        (root / 'old.har', '12345678901', old),
                        (root / 'state/costco_session.json', '12345678901', new)]
            with patch('fetch_qr.ROOT', root), patch('fetch_qr.load_session', side_effect=sessions), patch('fetch_qr.download_qr', side_effect=fetch_qr.QRFetchError('Rejected', code=501)) as download, patch('refresh_session.renew_session') as renew, patch('builtins.print'):
                with self.assertRaises(fetch_qr.QRFetchError):
                    fetch_qr.fetch_and_save()
            self.assertEqual(download.call_count, 2)
            renew.assert_called_once()
            self.assertFalse((root / 'images').exists())
