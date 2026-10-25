"""Obtain a fresh WeChat login code through a local WMPFDebugger bridge."""

import json
import logging
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, ProxyHandler, build_opener

from websocket import create_connection, WebSocketException

from costco_session import LOGIN_PATH, SESSION_FILE, NoRedirect, QRFetchError, from_login, save


APP_ID = 'wxf3ae719fecac9fe9'
logger = logging.getLogger('costco_login')


def get_login_code():
    try:
        ws = create_connection('ws://127.0.0.1:62000', timeout=5, http_no_proxy=['127.0.0.1'])
    except (OSError, WebSocketException):
        raise QRFetchError('Automatic login bridge is unavailable. Run bash start_login_bridge.sh, then reopen the Costco mini program.') from None
    deadline = time.monotonic() + 10
    contexts = []
    sequence = 0

    def command(method, params=None):
        nonlocal sequence
        sequence += 1
        ws.send(json.dumps({'id': sequence, 'method': method, 'params': params or {}}))
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
        raise QRFetchError('Costco mini program context not found. Open it after starting the login bridge.')
    except (OSError, WebSocketException, ValueError) as error:
        if isinstance(error, QRFetchError):
            raise
        raise QRFetchError('Could not obtain a WeChat login code; check the local login bridge.') from None
    finally:
        ws.close()


def renew_session(headers, path=SESSION_FILE):
    code = get_login_code()
    login_headers = {k: v for k, v in headers.items() if k != 'costco-authorization'}
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
