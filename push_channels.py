"""Deliver digests through ServerChan Turbo and PushPlus without logging tokens."""
import json
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


class PushError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise PushError('Push endpoint redirected; request stopped')


def article_markdown(rows):
    # Cards read better than a five-column table on mobile push clients.
    def safe(value):
        return re.sub(r'([\\`*_{}\[\]<>#!|])', r'\\\1', value)
    return '\n\n---\n\n'.join(
        f'### {row[0]}. {safe(row[1])}\n\n'
        + '\n\n'.join(safe(point) for point in re.split(r'<br\s*/?>', row[2], flags=re.I))
        + f'\n\n来源：{safe(row[3])}\n\n[查看原文]({row[4]})'
        for row in rows)


def deliver(channel, token, title, content):
    if channel == 'serverchan':
        if not re.fullmatch(r'SCT[A-Za-z0-9_-]+', token):
            raise PushError('SERVERCHAN_SENDKEY must be a ServerChan Turbo SCT SendKey')
        url = f'https://sctapi.ftqq.com/{token}.send'
        payload = {'title': title, 'desp': content}
    elif channel == 'pushplus':
        url = 'https://www.pushplus.plus/send'
        payload = {'token': token, 'title': title, 'content': content, 'template': 'markdown'}
    else:
        raise PushError('Unknown push channel')
    request = Request(url, data=json.dumps(payload, ensure_ascii=False).encode(),
                      headers={'Content-Type': 'application/json'}, method='POST')
    opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(request, timeout=45) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise PushError('Push response exceeds size limit')
        result = json.loads(raw)
        expected = 0 if channel == 'serverchan' else 200
        if not isinstance(result, dict) or type(result.get('code')) is not int or result['code'] != expected:
            raise PushError('Push service rejected the request; check account, token and quota')
    except HTTPError as exc:
        code = exc.code
        exc.close()
        raise PushError(f'Push endpoint returned HTTP {code}') from None
    except (URLError, OSError, TimeoutError):
        raise PushError('Push network, TLS or timeout failure') from None
    except (ValueError, TypeError):
        raise PushError('Push endpoint returned an invalid response') from None
