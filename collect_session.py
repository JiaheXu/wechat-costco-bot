"""Mitmproxy addon: collect successful Costco logins as they happen."""

import logging

from costco_session import LOGIN_PATH, SESSION_FILE, from_login, save


logger = logging.getLogger('costco_collector')


def response(flow):
    request = flow.request
    if (request.host != 'www.costco.com.cn' or request.method != 'GET'
            or request.path.split('?', 1)[0] != LOGIN_PATH
            or flow.response is None or flow.response.status_code != 200):
        return
    try:
        session = from_login(flow.response.json(), dict(request.headers))
        if session is not None:
            save(session, SESSION_FILE)
            logger.info('Costco login session updated automatically; credentials kept private.')
    except (OSError, ValueError, TypeError) as error:
        logger.warning('Could not collect login session: %s', type(error).__name__)
