"""Authorize a personal Outlook mailbox and obtain a delegated refresh token."""

import json
import os
import ssl
import sys
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

SCOPE = 'https://graph.microsoft.com/Mail.Send offline_access'
BASE = 'https://login.microsoftonline.com'
MAX_RESPONSE_BYTES = 1024 * 1024


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise RuntimeError('Microsoft authorization endpoint redirected; verify the official endpoint')


def post_form(url, form):
    request = Request(url, data=urlencode(form).encode(),
                      headers={'Content-Type': 'application/x-www-form-urlencoded'}, method='POST')
    opener = build_opener(NoRedirectHandler(), HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(request, timeout=30) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        data = exc.read(MAX_RESPONSE_BYTES + 1)
    except (URLError, TimeoutError, OSError):
        raise RuntimeError('Microsoft sign-in request failed; check network access') from None
    if len(data) > MAX_RESPONSE_BYTES:
        raise RuntimeError('Microsoft sign-in response exceeds size limit')
    try:
        result = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError('Microsoft sign-in returned an invalid response') from None
    if not isinstance(result, dict):
        raise RuntimeError('Microsoft sign-in returned an invalid response')
    return result


def authorize(client_id, authority='consumers'):
    try:
        uuid.UUID(client_id)
    except ValueError:
        raise RuntimeError('MS_CLIENT_ID must be an application ID in UUID format')
    if authority != 'consumers':
        raise RuntimeError('This helper is for personal Outlook accounts; MS_AUTHORITY must be consumers')
    device = post_form(f'{BASE}/{authority}/oauth2/v2.0/devicecode',
                       {'client_id': client_id, 'scope': SCOPE})
    if not all(device.get(key) for key in ('device_code', 'user_code', 'verification_uri')):
        raise RuntimeError('Could not start sign-in; confirm the app supports personal Microsoft accounts and public client flows')
    print(f"Open {device['verification_uri']} and enter code {device['user_code']} to authorize Mail.Send.", flush=True)
    deadline = time.monotonic() + int(device.get('expires_in', 900))
    interval = max(1, int(device.get('interval', 5)))
    while time.monotonic() < deadline:
        time.sleep(interval)
        result = post_form(f'{BASE}/{authority}/oauth2/v2.0/token', {
            'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
            'client_id': client_id,
            'device_code': device['device_code'],
        })
        if result.get('refresh_token'):
            return result['refresh_token']
        error = result.get('error')
        if error == 'authorization_pending':
            continue
        if error == 'slow_down':
            interval += 5
            continue
        if error:
            raise RuntimeError(f'Microsoft sign-in did not complete ({error}); restart authorization if the code expired')
    raise RuntimeError('Microsoft sign-in code expired; run this command again')


def main():
    client_id = os.environ.get('MS_CLIENT_ID', '').strip()
    if not client_id:
        print('Set MS_CLIENT_ID to your Entra application ID first.', file=sys.stderr)
        return 2
    try:
        refresh_token = authorize(client_id, os.environ.get('MS_AUTHORITY', 'consumers').strip())
    except RuntimeError as exc:
        print(f'Authorization failed: {exc}', file=sys.stderr)
        return 1
    print('\nCopy this refresh token directly into the GitHub secret MS_REFRESH_TOKEN. Do not share it:')
    print(refresh_token)
    return 0


if __name__ == '__main__':
    sys.exit(main())
