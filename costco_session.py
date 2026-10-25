"""Private session storage shared by the proxy collector and QR fetcher."""

import fcntl
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.request import HTTPRedirectHandler


ROOT = Path(__file__).resolve().parent
SESSION_FILE = ROOT / 'state/costco_session.json'
LOGIN_PATH = '/api/em-bkgd/api/pub/v3/wx/user/login'
HEADER_NAMES = {'costco-authorization', 'cookie', 'user-agent', 'lang', 'referer', 'accept'}


class QRFetchError(ValueError):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.safe_message = message
        self.code = code


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Keep credentials on the verified Costco endpoint.


def valid(session):
    if not isinstance(session, dict):
        return False
    card, headers = session.get('cardNo'), session.get('headers')
    return (
        isinstance(card, str) and card.isascii() and card.isdigit()
        and isinstance(headers, dict) and bool(headers.get('costco-authorization'))
        and all(k in HEADER_NAMES and isinstance(v, str) and '\r' not in v and '\n' not in v
                for k, v in headers.items())
    )


def from_login(body, request_headers):
    if not isinstance(body, dict) or body.get('code') != 0:
        return None
    data = body.get('data')
    if not isinstance(data, dict) or not isinstance(data.get('memberInfo'), dict):
        return None
    token, session_id = data.get('token'), data.get('sessionId')
    if not isinstance(token, str) or not isinstance(session_id, str) or not session_id or ';' in session_id:
        return None
    headers = {k.lower(): v for k, v in request_headers.items() if k.lower() in HEADER_NAMES}
    headers.update({'costco-authorization': token, 'cookie': 'JSESSIONID=' + session_id})
    headers.setdefault('lang', 'cn')
    headers.setdefault('accept', '*/*')
    session = {'cardNo': data['memberInfo'].get('cardNo'), 'headers': headers, 'updated_at': time.time()}
    return session if valid(session) else None


def save(session, path=SESSION_FILE):
    if not valid(session):
        raise ValueError('Invalid session; credentials were not saved.')
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (path.parent / 'session.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            try:
                previous = json.loads(path.read_text())
            except ValueError:
                previous = {}
            if not isinstance(previous, dict):
                previous = {}
            previous_time = previous.get('updated_at', 0)
            if isinstance(previous_time, (int, float)) and previous_time > session.get('updated_at', 0):
                return
        fd, name = tempfile.mkstemp(suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(session, stream)
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
