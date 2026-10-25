#!/usr/bin/env python3
"""Fetch a QR every 10 seconds and log successes and safe failure details."""

import logging
from logging.handlers import RotatingFileHandler
import os
import time

from fetch_qr import ROOT, QRFetchError, fetch_and_save


logger = logging.getLogger('qr_watch')


def run():
    attempt = 0
    while True:
        attempt += 1
        started = time.monotonic()
        try:
            image = fetch_and_save()
            level, result = logging.INFO, f'Saved {image}'
        except QRFetchError as error:
            level, result = logging.ERROR, error.safe_message
        except Exception as error:
            # Exception text may contain credentials or server response data.
            level, result = logging.ERROR, f'Unexpected {type(error).__name__}'
        elapsed = time.monotonic() - started
        logger.log(level, 'Attempt %d (%.2fs): %s', attempt, elapsed, result)
        time.sleep(max(0, 10 - elapsed))


def main():
    os.umask(0o077)
    directory = ROOT / 'logs'
    directory.mkdir(mode=0o700, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        handlers=[
            RotatingFileHandler(directory / 'qr_watch.log', maxBytes=1_000_000, backupCount=3),
            logging.StreamHandler(),
        ],
    )
    logger.info('QR watcher started: fetch immediately, then every 10 seconds; Ctrl+C to stop.')
    try:
        run()
    except KeyboardInterrupt:
        logger.info('QR watcher stopped.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
