import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

import refresh_session
from fetch_qr import QRFetchError


class FakeDebugger:
    def __init__(self, app_id):
        self.app_id = app_id
        self.pending = []
        self.expressions = []
        self.closed = False

    def send(self, wire):
        request = json.loads(wire)
        if request['method'] == 'Runtime.enable':
            self.pending.append({'method': 'Runtime.executionContextCreated', 'params': {'context': {'id': 7}}})
            result = {}
        else:
            expression = request['params']['expression']
            self.expressions.append(expression)
            result = {'result': {'value': 'fresh-wechat-code' if 'new Promise' in expression else self.app_id}}
        self.pending.append({'id': request['id'], 'result': result})

    def recv(self):
        return json.dumps(self.pending.pop(0))

    def settimeout(self, timeout):
        pass

    def close(self):
        self.closed = True


class RefreshTest(unittest.TestCase):
    def test_fresh_code_is_requested_only_in_costco_context(self):
        debugger = FakeDebugger(refresh_session.APP_ID)
        with patch('refresh_session.create_connection', return_value=debugger):
            self.assertEqual(refresh_session.get_login_code(), 'fresh-wechat-code')
        self.assertTrue(debugger.closed)
        self.assertTrue(any('new Promise' in expression for expression in debugger.expressions))

    def test_other_mini_program_is_not_logged_in(self):
        debugger = FakeDebugger('another-app')
        with patch('refresh_session.create_connection', return_value=debugger):
            with self.assertRaises(QRFetchError):
                refresh_session.get_login_code()
        self.assertFalse(any('new Promise' in expression for expression in debugger.expressions))

    def test_missing_debugger_has_a_safe_actionable_error(self):
        with patch('refresh_session.create_connection', side_effect=OSError('private details')):
            with self.assertRaises(QRFetchError) as error:
                refresh_session.get_login_code()
        self.assertIn('start_login_bridge.sh', error.exception.safe_message)
        self.assertNotIn('private details', error.exception.safe_message)

    def test_new_login_response_replaces_private_session(self):
        body = {'code': 0, 'data': {'token': 'new-token', 'sessionId': 'new-session',
                                  'memberInfo': {'cardNo': '12345678901'}}}
        opener = Mock()
        opener.open.return_value = io.BytesIO(json.dumps(body).encode())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.json'
            with patch('refresh_session.get_login_code', return_value='fresh-wechat-code'), patch('refresh_session.build_opener', return_value=opener):
                refresh_session.renew_session({'costco-authorization': 'old-token', 'cookie': 'JSESSIONID=old'}, path)
            request = opener.open.call_args.args[0]
            self.assertTrue(request.full_url.startswith('https://www.costco.com.cn' + refresh_session.LOGIN_PATH + '?code='))
            self.assertNotIn('Costco-authorization', request.headers)
            session = json.loads(path.read_text())
            self.assertEqual(session['headers']['costco-authorization'], 'new-token')
            self.assertEqual(session['headers']['cookie'], 'JSESSIONID=new-session')
