#!/usr/bin/env python3
"""Create a fresh Costco login session and fetch its membership QR."""

import base64
from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import build_opener, ProxyHandler, Request
from zoneinfo import ZoneInfo

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps
import costco_session
from costco_session import NoRedirect, QRFetchError


ROOT = Path(__file__).resolve().parent
ENDPOINT = 'https://www.costco.com.cn/api/em-bkgd/api/pub/qrcode'
_SESSION_RENEW_LOCK = threading.Lock()


def load_session(captures):
    live = captures.parent / 'state/costco_session.json'
    if live.exists():
        try:
            session = json.loads(live.read_text())
        except ValueError:
            raise QRFetchError('Live login session is invalid; reopen the mini program while the collector runs.') from None
        if not costco_session.valid(session):
            raise QRFetchError('Live login session is invalid; reopen the mini program while the collector runs.')
        return live, session['cardNo'], session['headers']
    files = sorted(captures.glob('https-*/flows.har'), key=lambda p: p.stat().st_mtime, reverse=True)
    for source in files:
        try:
            document = json.loads(source.read_text())
        except json.JSONDecodeError:
            continue  # A recording still in progress may have an unfinished HAR.
        for entry in reversed(document.get('log', {}).get('entries', [])):
            request = entry.get('request', {})
            url = urlsplit(request.get('url', ''))
            if request.get('method') != 'GET' or url.scheme + '://' + url.netloc + url.path != ENDPOINT:
                continue
            params = parse_qs(url.query)
            headers = {h['name'].lower(): h['value'] for h in request.get('headers', [])
                       if h['name'].lower() in ('costco-authorization', 'cookie', 'user-agent', 'lang', 'referer', 'accept')}
            card = params.get('cardNo', [''])[0]
            if card.isascii() and card.isdigit() and headers.get('costco-authorization'):
                if any('\r' in value or '\n' in value for value in headers.values()):
                    raise QRFetchError('Invalid captured headers; record a new session.')
                return source, card, headers
    raise QRFetchError('No captured QR session found. Run bash record_https.sh and open 会员码 first.')


def download_qr(card, headers):
    query = urlencode({'cardNo': card, 'time': str(time.time_ns() // 1_000_000)})
    request = Request(ENDPOINT + '?' + query, headers=headers)
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            body = response.read(2_000_001)
    except HTTPError as error:
        raise QRFetchError(f'Costco returned HTTP {error.code}.') from None
    except URLError:
        raise QRFetchError('Unable to reach Costco over HTTPS.') from None
    if len(body) > 2_000_000:
        raise QRFetchError('QR response is unexpectedly large.')
    try:
        result = json.loads(body)
    except (ValueError, UnicodeError):
        raise QRFetchError('Costco returned an invalid JSON response.') from None
    if not isinstance(result, dict) or result.get('code') != 0:
        code = result.get('code') if isinstance(result, dict) else None
        safe_code = code if isinstance(code, int) else 'unknown'
        raise QRFetchError(f'Costco rejected the request (code={safe_code}); record a new session and try again.', code=code)
    data = result.get('data')
    if not isinstance(data, str) or not data.startswith('data:image/png;base64,'):
        raise QRFetchError('Costco did not return a PNG image.')
    try:
        image = base64.b64decode(data.split(',', 1)[1], validate=True)
    except ValueError:
        raise QRFetchError('Costco returned an invalid PNG encoding.') from None
    if len(image) < 24 or not image.startswith(b'\x89PNG\r\n\x1a\n') or image[12:16] != b'IHDR':
        raise QRFetchError('Costco returned an invalid PNG image.')
    return image


def render_qr(qr):
    qr = ImageOps.expand(qr.convert('RGB'), border=10, fill='white')
    background = ROOT / 'background.png'
    if not background.is_file():
        return qr
    with Image.open(background) as source:
        card = source.convert('RGB').resize((472, 922), Image.Resampling.LANCZOS)
    # Flatten template background noise so the PNG fits in a UDP reply.
    red, green, blue = card.split()
    white = ImageChops.darker(ImageChops.darker(red, green), blue).point(lambda v: 255 if v >= 235 else 0)
    card.paste('white', mask=white)
    header = card.crop((0, 0, 472, 269))
    color = header.getpixel((5, 30))
    red, green, blue = ImageChops.difference(header, Image.new('RGB', header.size, color)).split()
    flat = ImageChops.lighter(ImageChops.lighter(red, green), blue).point(lambda v: 255 if v <= 45 else 0)
    header.paste(color, mask=flat)
    card.paste(header)
    draw = ImageDraw.Draw(card)
    draw.rectangle((112, 500, 360, 798), fill='white')
    qr = ImageOps.contain(qr, (228, 228), Image.Resampling.NEAREST)
    card.paste(qr, (122 + (228 - qr.width) // 2, 510 + (228 - qr.height) // 2))
    font = ImageFont.truetype('DejaVuSans.ttf', 20)
    stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y.%m.%d %H:%M:%S')
    draw.text((236, 773), stamp, fill='black', font=font, anchor='mm')
    # Keep the card within the existing 60 KB base64 UDP reply limit.
    return card.quantize(colors=96, dither=Image.Dither.NONE)


def fetch_and_save():
    from refresh_session import renew_session

    # A concurrent login could replace credentials before this QR is fetched.
    with _SESSION_RENEW_LOCK:
        try:
            _, _, headers = load_session(ROOT / 'captures')
        except QRFetchError:
            headers = {}
        renew_session(headers, ROOT / 'state/costco_session.json')
        _, card, headers = load_session(ROOT / 'captures')
        image = download_qr(card, headers)
    directory = ROOT / 'images'
    directory.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    fd, name = tempfile.mkstemp(prefix=f'costco-qr-{stamp}-', suffix='.png', dir=directory)
    output = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            with Image.open(BytesIO(image)) as qr:
                render_qr(qr).save(stream, format='PNG', optimize=True)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return output


def main():
    output = fetch_and_save()
    print(f'Saved fresh QR: {output}')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except HTTPError as error:
        print(f'Costco returned HTTP {error.code}. If the session expired, record a new one.')
        raise SystemExit(1)
    except URLError:
        print('Unable to reach Costco over HTTPS. Check your network connection.')
        raise SystemExit(1)
    except QRFetchError as error:
        print(f'Fetch failed: {error.safe_message}')
        raise SystemExit(1)
    except (OSError, ValueError):
        # Avoid exposing credentials or personal data from exception messages.
        print('Fetch failed: check the captured session, Costco response, and image directory permissions.')
        raise SystemExit(1)
