"""Obtain a fresh WeChat login code through a local WMPFDebugger bridge."""

import atexit
import json
import logging
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, ProxyHandler, build_opener

from websocket import create_connection, WebSocketException, WebSocketTimeoutException

from costco_session import LOGIN_PATH, SESSION_FILE, NoRedirect, QRFetchError, from_login, save


APP_ID = 'wxf3ae719fecac9fe9'
logger = logging.getLogger('costco_login')
_bridge_process = None
_bridge_lock = threading.Lock()


def stop_login_bridge(process):
    if process.poll() is not None:
        return
    try:
        subprocess.run(['sudo', '-n', 'kill', '-INT', str(process.pid)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        process.wait(timeout=5)
    except (OSError, subprocess.SubprocessError):
        logger.warning('Could not stop the login bridge automatically; stop it from its terminal.')


def start_login_bridge():
    global _bridge_process
    with _bridge_lock:
        if _bridge_process is not None and _bridge_process.poll() is None:
            return
        interactive = sys.stdin.isatty()
        logger.info('Starting the WeChat login bridge; keep WeChat running. Reopen only the Costco mini program after attachment.')
        try:
            subprocess.run(['sudo', '-v'] if interactive else ['sudo', '-n', '-v'], check=True,
                           stdout=subprocess.DEVNULL, stderr=None if interactive else subprocess.DEVNULL)
            _bridge_process = subprocess.Popen(
                ['bash', str(Path(__file__).with_name('start_login_bridge.sh'))], stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            raise QRFetchError('Could not start the login bridge. Run python3 qr_server.py in a terminal to approve sudo, or run bash start_login_bridge.sh separately.') from None
        atexit.register(stop_login_bridge, _bridge_process)


def get_login_code():
    try:
        ws = create_connection('ws://127.0.0.1:62000', timeout=5, http_no_proxy=['127.0.0.1'])
    except (OSError, WebSocketException):
        start_login_bridge()
        deadline = time.monotonic() + 15
        while True:
            if _bridge_process is not None and _bridge_process.poll() is not None:
                raise QRFetchError('Login bridge exited; check its terminal output and keep WeChat running.')
            try:
                ws = create_connection('ws://127.0.0.1:62000', timeout=1, http_no_proxy=['127.0.0.1'])
                break
            except (OSError, WebSocketException):
                if time.monotonic() >= deadline:
                    raise QRFetchError('Login bridge did not become available. Run bash start_login_bridge.sh and check its terminal output.') from None
                time.sleep(0.2)
    deadline = time.monotonic() + 10
    contexts = []
    sequence = 0

    def command(method, params=None, session_id=None):
        nonlocal sequence
        sequence += 1
        request = {'id': sequence, 'method': method, 'params': params or {}}
        if session_id is not None:
            request['sessionId'] = session_id
        ws.send(json.dumps(request))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise QRFetchError('WeChat login bridge timed out; keep the Costco mini program open.')
            ws.settimeout(remaining)
            message = json.loads(ws.recv())
            if not isinstance(message, dict):
                raise QRFetchError('Invalid debugger response.')
            if message.get('method') == 'Runtime.executionContextCreated':
                context = message.get('params', {}).get('context', {})
                if 'id' in context:
                    contexts.append(context['id'])
            if message.get('id') == sequence:
                if 'error' in message:
                    raise QRFetchError('WeChat debugger rejected the login operation.')
                return message.get('result', {})

    try:
        command('Runtime.enable')
        for context in list(contexts) + [None]:
            params = {'expression': 'typeof wx !== "undefined" && wx.getAccountInfoSync ? wx.getAccountInfoSync().miniProgram.appId : ""',
                      'returnByValue': True}
            if context is not None:
                params['contextId'] = context
            result = command('Runtime.evaluate', params)
            if result.get('result', {}).get('value') != APP_ID:
                continue
            params.update({
                'expression': 'new Promise((resolve, reject) => wx.login({success: r => resolve(r.code), fail: () => reject(new Error("wx.login failed"))}))',
                'awaitPromise': True,
            })
            result = command('Runtime.evaluate', params)
            code = result.get('result', {}).get('value')
            if 'exceptionDetails' not in result and isinstance(code, str) and 0 < len(code) <= 512:
                return code
            raise QRFetchError('WeChat did not issue a fresh login code.')
        # Linux page targets expose the native bridge without the service's wx object.
        targets = command('Target.getTargets').get('targetInfos', [])
        for target in targets:
            url = urlsplit(target.get('url', ''))
            if (url.scheme != 'https' or url.hostname != 'servicewechat.com'
                    or url.path.split('/')[1:2] != [APP_ID] or target.get('type') != 'page'):
                continue
            session_id = command('Target.attachToTarget', {
                'targetId': target['targetId'], 'flatten': True}).get('sessionId')
            if not isinstance(session_id, str) or not session_id:
                raise QRFetchError('Could not attach to the Costco page target.')
            try:
                result = command('Runtime.evaluate', {
                    'expression': 'new Promise((resolve, reject) => WeixinJSBridge.invoke("login", {}, r => r.errMsg === "login:ok" ? resolve(r.code) : reject(new Error("Native login failed"))))',
                    'awaitPromise': True, 'returnByValue': True}, session_id)
                code = result.get('result', {}).get('value')
                if 'exceptionDetails' not in result and isinstance(code, str) and 0 < len(code) <= 512:
                    return code
                raise QRFetchError('Costco native bridge did not issue a fresh login code.')
            finally:
                try:
                    command('Target.detachFromTarget', {'sessionId': session_id})
                except (OSError, WebSocketException, ValueError):
                    logger.warning('Could not detach the Costco debugger target cleanly.')
        raise QRFetchError('Costco mini program context not found. Open it after starting the login bridge.')
    except WebSocketTimeoutException:
        raise QRFetchError('WeChat login bridge did not reply. Keep Costco open; close and reopen only the mini program after attachment and check for miniapp client connected in the bridge terminal.') from None
    except (OSError, WebSocketException, ValueError) as error:
        if isinstance(error, QRFetchError):
            raise
        raise QRFetchError('Could not obtain a WeChat login code; check the local login bridge.') from None
    finally:
        ws.close()


def renew_session(headers, path=SESSION_FILE):
    code = get_login_code()
    login_headers = {k: v for k, v in headers.items() if k.lower() not in ('costco-authorization', 'cookie')}
    login_headers.setdefault('lang', 'cn')
    login_headers.setdefault('accept', '*/*')
    request = Request('https://www.costco.com.cn' + LOGIN_PATH + '?' + urlencode({'code': code}), headers=login_headers)
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=20) as response:
            body = response.read(2_000_001)
    except HTTPError as error:
        raise QRFetchError(f'Fresh Costco login returned HTTP {error.code}.') from None
    except URLError:
        raise QRFetchError('Could not reach Costco to renew login.') from None
    if len(body) > 2_000_000:
        raise QRFetchError('Unexpectedly large login response.')
    try:
        session = from_login(json.loads(body), login_headers)
    except (ValueError, UnicodeError):
        session = None
    if session is None:
        raise QRFetchError('Costco did not accept the fresh WeChat login.')
    save(session, path)
    logger.info('Costco login renewed through WeChat; credentials kept private.')
