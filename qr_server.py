#!/usr/bin/env python3
"""Serve fresh membership QR images to local UDP clients."""

import base64
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import socketserver
import time

from fetch_qr import ROOT, QRFetchError, fetch_and_save


logger = logging.getLogger('qr_server')


class QRHandler(socketserver.BaseRequestHandler):
    def handle(self):
        packet, sock = self.request
        request_id = None
        started = time.monotonic()
        try:
            request = json.loads(packet)
            if not isinstance(request, dict):
                raise QRFetchError('Invalid QR request.')
            candidate = request.get('request_id')
            if not isinstance(candidate, str) or len(candidate) != 32 or any(c not in '0123456789abcdef' for c in candidate):
                raise QRFetchError('Invalid request ID.')
            request_id = candidate
            if request.get('type') != 'costco_qr':
                raise QRFetchError('Unknown request type.')
            image = fetch_and_save()
            response = {'request_id': request_id, 'ok': True,
                        'image': base64.b64encode(image.read_bytes()).decode('ascii')}
        except QRFetchError as error:
            response = {'request_id': request_id, 'ok': False, 'error': error.safe_message}
        except Exception as error:
            response = {'request_id': request_id, 'ok': False,
                        'error': f'Unexpected {type(error).__name__} in QR server.'}
        wire = json.dumps(response).encode('utf-8')
        # shortcut: UDP replies are limited to 60 KB; use HTTP if QR images grow larger.
        if len(wire) > 60_000:
            response = {'request_id': request_id, 'ok': False, 'error': 'QR image is too large for a UDP reply.'}
            wire = json.dumps(response).encode('utf-8')
        logger.log(
            logging.INFO if response['ok'] else logging.WARNING,
            'request=%s duration=%.2fs result=%s', request_id, time.monotonic() - started,
            'QR ready' if response['ok'] else response['error'],
        )
        sock.sendto(wire, self.client_address)


def main():
    os.umask(0o077)
    directory = ROOT / 'logs'
    directory.mkdir(mode=0o700, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        handlers=[
            RotatingFileHandler(directory / 'qr_server.log', maxBytes=1_000_000, backupCount=3),
            logging.StreamHandler(),
        ],
    )
    port = int(os.getenv('COSTCO_QR_SERVER_PORT', '18888'))
    with socketserver.ThreadingUDPServer(('127.0.0.1', port), QRHandler) as server:
        logger.info('Startup QR check: fetching a fresh image.')
        try:
            image = fetch_and_save()
            logger.info('Startup QR check succeeded; saved image: %s', image)
        except QRFetchError as error:
            logger.warning('Startup QR check failed: %s; continuing to serve requests.', error.safe_message)
        except Exception as error:
            logger.warning('Startup QR check failed: %s; continuing to serve requests.', type(error).__name__)
        logger.info('QR server listening on UDP 127.0.0.1:%s; Ctrl+C to stop.', port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logger.info('QR server stopping; waiting for active requests.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
