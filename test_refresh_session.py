import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from websocket import WebSocketTimeoutException

import refresh_session
from fetch_qr import QRFetchError


class FakeDebugger:
    def __init__(self, app_id):
        self.app_id = app_id
        self.pending = []
        self.expressions = []
        self.closed = False
        self.targets = []
        self.native_code = 'native-wechat-code'
        self.commands = []

    def send(self, wire):
        request = json.loads(wire)
        self.commands.append(request)
        if request['method'] == 'Runtime.enable':
            self.pending.append({'method': 'Runtime.executionContextCreated', 'params': {'context': {'id': 7}}})
            result = {}
        elif request['method'] == 'Target.getTargets':
            result = {'targetInfos': self.targets}
        elif request['method'] == 'Target.attachToTarget':
            result = {'sessionId': 'costco-page-session'}
        elif request['method'] == 'Target.detachFromTarget':
            result = {}
        else:
            expression = request['params']['expression']
            self.expressions.append(expression)
            value = 'fresh-wechat-code' if 'new Promise' in expression else self.app_id
            if 'WeixinJSBridge.invoke' in expression:
                assert request.get('sessionId') == 'costco-page-session'
                value = self.native_code
            result = {'result': {'value': value}}
        self.pending.append({'id': request['id'], 'result': result})

    def recv(self):
        return json.dumps(self.pending.pop(0))

    def settimeout(self, timeout):
        pass

    def close(self):
        self.closed = True


class RefreshTest(unittest.TestCase):
    def test_native_page_bridge_logs_in_when_wx_is_unavailable(self):
        debugger = FakeDebugger('not-costco')
        debugger.targets = [{'targetId': 'costco-page', 'type': 'page',
                             'url': f'https://servicewechat.com/{refresh_session.APP_ID}/1/page-frame.html'}]
        with patch('refresh_session.create_connection', return_value=debugger):
            self.assertEqual(refresh_session.get_login_code(), 'native-wechat-code')
        attached = [r for r in debugger.commands if r['method'] == 'Target.attachToTarget']
        self.assertEqual(attached[0]['params']['targetId'], 'costco-page')
        self.assertEqual(debugger.commands[-1]['method'], 'Target.detachFromTarget')
        self.assertTrue(debugger.closed)

    def test_native_login_rejects_other_apps_and_spoofed_target_urls(self):
        app = refresh_session.APP_ID
        for url in [f'https://servicewechat.com.evil.example/{app}/page.html',
                    f'https://evil.example/{app}/page.html',
                    f'https://servicewechat.com/another-app/page.html?appid={app}',
                    f'http://servicewechat.com/{app}/page.html']:
            with self.subTest(url=url):
                debugger = FakeDebugger('not-costco')
                debugger.targets = [{'targetId': 'wrong-app', 'type': 'page', 'url': url}]
                with patch('refresh_session.create_connection', return_value=debugger):
                    with self.assertRaises(QRFetchError):
                        refresh_session.get_login_code()
                self.assertFalse(any('WeixinJSBridge.invoke' in e for e in debugger.expressions))

    def test_native_login_failure_detaches_and_closes_connection(self):
        debugger = FakeDebugger('not-costco')
        debugger.targets = [{'targetId': 'costco-page', 'type': 'page',
                             'url': f'https://servicewechat.com/{refresh_session.APP_ID}/1/page-frame.html'}]
        debugger.native_code = None
        with patch('refresh_session.create_connection', return_value=debugger):
            with self.assertRaises(QRFetchError):
                refresh_session.get_login_code()
        self.assertEqual(debugger.commands[-1]['method'], 'Target.detachFromTarget')
        self.assertTrue(debugger.closed)

    def test_connected_bridge_without_mini_program_has_actionable_timeout(self):
        debugger = FakeDebugger(refresh_session.APP_ID)
        debugger.recv = Mock(side_effect=WebSocketTimeoutException('private details'))
        with patch('refresh_session.create_connection', return_value=debugger):
            with self.assertRaises(QRFetchError) as error:
                refresh_session.get_login_code()
        self.assertIn('miniapp client connected', error.exception.safe_message)
        self.assertIn('reopen', error.exception.safe_message)
        self.assertNotIn('private details', error.exception.safe_message)
        self.assertTrue(debugger.closed)

    def test_fresh_code_is_requested_only_in_costco_context(self):
        debugger = FakeDebugger(refresh_session.APP_ID)
        with patch('refresh_session.create_connection', return_value=debugger), \
             patch('refresh_session.start_login_bridge') as start:
            self.assertEqual(refresh_session.get_login_code(), 'fresh-wechat-code')
        start.assert_not_called()
        self.assertTrue(debugger.closed)
        self.assertTrue(any('new Promise' in expression for expression in debugger.expressions))

    def test_other_mini_program_is_not_logged_in(self):
        debugger = FakeDebugger('another-app')
        with patch('refresh_session.create_connection', return_value=debugger):
            with self.assertRaises(QRFetchError):
                refresh_session.get_login_code()
        self.assertFalse(any('new Promise' in expression for expression in debugger.expressions))

    def test_missing_debugger_has_a_safe_actionable_error(self):
        with patch('refresh_session.create_connection', side_effect=OSError('private details')), \
             patch('refresh_session.start_login_bridge', side_effect=QRFetchError('Run bash start_login_bridge.sh')):
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
            self.assertNotIn('Cookie', request.headers)
            session = json.loads(path.read_text())
            self.assertEqual(session['headers']['costco-authorization'], 'new-token')
            self.assertEqual(session['headers']['cookie'], 'JSESSIONID=new-session')

    def test_missing_bridge_is_started_and_connection_retried(self):
        debugger = FakeDebugger(refresh_session.APP_ID)
        with patch('refresh_session.create_connection', side_effect=[OSError(), debugger]), \
             patch('refresh_session.start_login_bridge') as start:
            self.assertEqual(refresh_session.get_login_code(), 'fresh-wechat-code')
        start.assert_called_once_with()
        self.assertTrue(debugger.closed)

    def test_bridge_starts_with_cached_sudo_and_is_reused(self):
        process = Mock()
        process.poll.return_value = None
        with patch('refresh_session._bridge_process', None), \
             patch('refresh_session.subprocess.run') as run, \
             patch('refresh_session.subprocess.Popen', return_value=process) as spawn, \
             patch('refresh_session.sys.stdin.isatty', return_value=False), \
             patch('refresh_session.atexit.register'):
            refresh_session.start_login_bridge()
            refresh_session.start_login_bridge()
        self.assertEqual(run.call_args.args[0], ['sudo', '-n', '-v'])
        spawn.assert_called_once()

    def test_headless_sudo_failure_is_safe(self):
        import subprocess

        with patch('refresh_session._bridge_process', None), \
             patch('refresh_session.sys.stdin.isatty', return_value=False), \
             patch('refresh_session.subprocess.run', side_effect=subprocess.CalledProcessError(1, 'private')), \
             patch('refresh_session.subprocess.Popen') as spawn:
            with self.assertRaises(QRFetchError) as error:
                refresh_session.start_login_bridge()
        self.assertIn('terminal', error.exception.safe_message)
        self.assertNotIn('private', error.exception.safe_message)
        spawn.assert_not_called()

    def test_bridge_exit_is_reported_without_waiting(self):
        process = Mock()
        process.poll.return_value = 1
        with patch('refresh_session._bridge_process', process), \
             patch('refresh_session.create_connection', side_effect=OSError()), \
             patch('refresh_session.start_login_bridge'), \
             patch('refresh_session.time.sleep'):
            with self.assertRaises(QRFetchError) as error:
                refresh_session.get_login_code()
        self.assertIn('exited', error.exception.safe_message)

    def test_bridge_connection_wait_is_bounded(self):
        with patch('refresh_session._bridge_process', None), \
             patch('refresh_session.create_connection', side_effect=OSError('private details')), \
             patch('refresh_session.start_login_bridge'), \
             patch('refresh_session.time.monotonic', side_effect=[0, 16]):
            with self.assertRaises(QRFetchError) as error:
                refresh_session.get_login_code()
        self.assertIn('did not become available', error.exception.safe_message)
        self.assertNotIn('private', error.exception.safe_message)

    def test_owned_bridge_is_stopped_on_exit(self):
        process = Mock(pid=1234)
        process.poll.return_value = None
        with patch('refresh_session.subprocess.run') as run:
            refresh_session.stop_login_bridge(process)
        self.assertEqual(run.call_args.args[0], ['sudo', '-n', 'kill', '-INT', '1234'])
        process.wait.assert_called_once_with(timeout=5)

    def test_failed_login_keeps_previous_session(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'{"code":501,"msg":"private"}')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.json'
            path.write_text('previous private session')
            with patch('refresh_session.get_login_code', return_value='fresh-wechat-code'), \
                 patch('refresh_session.build_opener', return_value=opener):
                with self.assertRaises(QRFetchError):
                    refresh_session.renew_session({}, path)
            self.assertEqual(path.read_text(), 'previous private session')
