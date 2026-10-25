from pathlib import Path
import unittest
from unittest.mock import patch

import watch_qr
from fetch_qr import QRFetchError


class WatchQRTest(unittest.TestCase):
    def test_failure_is_logged_and_next_fetch_still_runs(self):
        with patch('watch_qr.fetch_and_save', side_effect=[QRFetchError('Rejected (code=501)'), Path('/tmp/new-qr.png')]) as fetch, patch('watch_qr.time.monotonic', side_effect=[0, 2, 10, 11]), patch('watch_qr.time.sleep', side_effect=[None, KeyboardInterrupt]) as sleep:
            with self.assertLogs('qr_watch', level='INFO') as log:
                with self.assertRaises(KeyboardInterrupt):
                    watch_qr.run()
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(sleep.call_args_list[0].args, (8,))
        self.assertEqual(sleep.call_args_list[1].args, (9,))
        self.assertIn('code=501', '\n'.join(log.output))
        self.assertIn('/tmp/new-qr.png', '\n'.join(log.output))

    def test_unexpected_exception_does_not_expose_private_details(self):
        with patch('watch_qr.fetch_and_save', side_effect=RuntimeError('private-token-and-card')), patch('watch_qr.time.monotonic', side_effect=[0, 1]), patch('watch_qr.time.sleep', side_effect=KeyboardInterrupt):
            with self.assertLogs('qr_watch', level='ERROR') as log:
                with self.assertRaises(KeyboardInterrupt):
                    watch_qr.run()
        self.assertIn('RuntimeError', '\n'.join(log.output))
        self.assertNotIn('private-token-and-card', '\n'.join(log.output))

    def test_slow_fetch_does_not_request_a_negative_sleep(self):
        with patch('watch_qr.fetch_and_save', return_value=Path('/tmp/new-qr.png')), patch('watch_qr.time.monotonic', side_effect=[0, 35]), patch('watch_qr.time.sleep', side_effect=KeyboardInterrupt) as sleep:
            with self.assertLogs('qr_watch', level='INFO'):
                with self.assertRaises(KeyboardInterrupt):
                    watch_qr.run()
        sleep.assert_called_once_with(0)
