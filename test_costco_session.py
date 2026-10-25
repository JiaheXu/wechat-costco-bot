import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import costco_session
import fetch_qr


LOGIN = {'code': 0, 'data': {'token': 'new-token', 'sessionId': 'new-session',
                           'memberInfo': {'cardNo': '12345678901'}, 'openid': 'private-openid'}}


class SessionTest(unittest.TestCase):
    def test_login_response_supplies_authentication_without_extra_personal_data(self):
        session = costco_session.from_login(LOGIN, {'user-agent': 'WeChat', 'host': 'ignored'})
        self.assertEqual(session['cardNo'], '12345678901')
        self.assertEqual(session['headers']['costco-authorization'], 'new-token')
        self.assertEqual(session['headers']['cookie'], 'JSESSIONID=new-session')
        self.assertNotIn('private-openid', json.dumps(session))
        self.assertNotIn('host', session['headers'])

    def test_failed_or_unsafe_login_is_not_accepted(self):
        for body in [{'code': 501}, {'code': 0, 'data': {}},
                     {'code': 0, 'data': {**LOGIN['data'], 'token': 'bad\r\nheader'}}]:
            with self.subTest(body=body):
                self.assertIsNone(costco_session.from_login(body, {}))

    def test_live_session_is_private_and_selected_without_a_qr_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'state/costco_session.json'
            session = costco_session.from_login(LOGIN, {})
            costco_session.save(session, path)
            source, card, headers = fetch_qr.load_session(root / 'captures')
            self.assertEqual(source, path)
            self.assertEqual(card, '12345678901')
            self.assertEqual(headers['cookie'], 'JSESSIONID=new-session')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_failed_save_preserves_the_previous_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state/costco_session.json'
            first = costco_session.from_login(LOGIN, {})
            costco_session.save(first, path)
            before = path.read_bytes()
            with patch('costco_session.os.replace', side_effect=OSError('disk error')):
                with self.assertRaises(OSError):
                    costco_session.save(first, path)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(path.parent.glob('*.tmp')), [])

    def test_late_old_response_does_not_replace_new_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.json'
            latest = costco_session.from_login(LOGIN, {})
            costco_session.save(latest, path)
            stale = {**latest, 'updated_at': latest['updated_at'] - 1,
                     'headers': {**latest['headers'], 'costco-authorization': 'stale-token'}}
            costco_session.save(stale, path)
            self.assertEqual(json.loads(path.read_text())['headers']['costco-authorization'], 'new-token')
