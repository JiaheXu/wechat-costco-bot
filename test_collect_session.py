from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import collect_session
import fetch_qr
from costco_session import LOGIN_PATH


class CollectorTest(unittest.TestCase):
    def flow(self, host='www.costco.com.cn', code=0):
        body = {'code': code, 'data': {'token': 'new-token', 'sessionId': 'new-session',
                                     'memberInfo': {'cardNo': '12345678901'}}}
        return SimpleNamespace(
            request=SimpleNamespace(host=host, path=LOGIN_PATH + '?code=private-code',
                                    method='GET', headers={'lang': 'cn'}),
            response=SimpleNamespace(status_code=200, json=lambda: body),
        )

    def test_login_is_available_to_fetcher_before_recording_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'state/costco_session.json'
            with patch('collect_session.SESSION_FILE', target), self.assertLogs('costco_collector') as log:
                collect_session.response(self.flow())
            source, card, headers = fetch_qr.load_session(root / 'captures')
            self.assertEqual(source, target)
            self.assertEqual(card, '12345678901')
            self.assertEqual(headers['costco-authorization'], 'new-token')
            self.assertNotIn('new-token', '\n'.join(log.output))

    def test_unrelated_or_failed_login_does_not_replace_session(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'session.json'
            with patch('collect_session.SESSION_FILE', target):
                collect_session.response(self.flow(host='www.costco.com.cn.evil.example'))
                collect_session.response(self.flow(code=501))
            self.assertFalse(target.exists())
