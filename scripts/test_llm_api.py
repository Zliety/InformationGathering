"""Check basic model API connectivity without invoking web search or email."""

import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request

from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import news_digest as app


QUESTION = '请用中文回答：17 加 25 等于多少？'


def response_text(style, data):
    if not isinstance(data, dict):
        raise app.DigestError('Model API returned an unsupported response structure')

    if style == 'responses':
        if data.get('status') != 'completed':
            raise app.DigestError('Model response did not complete')
        output = data.get('output')
        if not isinstance(output, list):
            raise app.DigestError('Model API returned an unsupported response structure')
        parts = [part['text'] for item in output if isinstance(item, dict) and item.get('type') == 'message'
                 for part in item.get('content', []) if isinstance(part, dict) and part.get('type') == 'output_text'
                 and isinstance(part.get('text'), str)]
        answer = '\n'.join(parts)
    else:
        choices = data.get('choices')
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise app.DigestError('Model API returned an unsupported response structure')
        choice = choices[0]
        if choice.get('finish_reason') != 'stop':
            raise app.DigestError('Model response was truncated or did not finish normally')
        message = choice.get('message')
        if not isinstance(message, dict) or not isinstance(message.get('content'), str):
            raise app.DigestError('Model API returned an unsupported response structure')
        answer = message['content']

    if not answer.strip():
        raise app.DigestError('Model API returned empty text')
    return answer.strip()


def run():
    base = app.setting('LLM_API_BASE_URL').rstrip('/')
    model = app.setting('LLM_MODEL')
    api_key = app.setting('LLM_API_KEY')
    missing = [name for name, value in (('LLM_API_BASE_URL', base), ('LLM_MODEL', model),
                                         ('LLM_API_KEY', api_key)) if not value]
    if missing:
        raise app.DigestError('Missing configuration: ' + ', '.join(missing))
    if not app.valid_url(base, https_only=True):
        raise app.DigestError('LLM_API_BASE_URL must be an HTTPS base URL')
    parsed_base = urlsplit(base)
    if parsed_base.query or parsed_base.fragment:
        raise app.DigestError('LLM_API_BASE_URL must not contain a query or fragment')

    style = app.setting('LLM_API_STYLE', 'responses').lower()
    if style not in ('responses', 'chat_completions'):
        raise app.DigestError('LLM_API_STYLE must be responses or chat_completions')
    endpoint = '/responses' if style == 'responses' else '/chat/completions'
    if style == 'responses':
        payload = {'model': model, 'input': QUESTION, 'stream': False}
    else:
        payload = {'model': model, 'messages': [{'role': 'user', 'content': QUESTION}], 'stream': False}

    request = Request(base + endpoint, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                      method='POST', headers={'Authorization': 'Bearer ' + api_key,
                                              'Content-Type': 'application/json'})
    host_path = parsed_base.netloc + parsed_base.path.rstrip('/') + endpoint
    print(f'Testing basic model request: {host_path} (style={style}, model={model})', flush=True)
    print('Search tools are disabled for this request; no email will be sent.', flush=True)

    try:
        data = json.loads(app.fetch(request, app.positive_int('LLM_TIMEOUT', 60)))
        answer = response_text(style, data)
    except HTTPError as exc:
        details = app.http_error_details(exc)
        guidance = {
            401: 'check whether the API key is valid',
            403: 'check key permissions, account access, and model availability',
            404: 'check the base URL, API style, endpoint path, and model availability',
            429: 'check rate limits, billing, and quota',
        }.get(exc.code, 'check the provider status and request ID')
        if exc.code >= 500:
            guidance = 'the provider or its upstream service failed; compare this with the web-search run'
        raise app.DigestError(f'Model API returned HTTP {exc.code}{details}; {guidance}') from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise app.DigestError('Model API returned a non-JSON or invalid response') from None
    except (URLError, TimeoutError, OSError):
        raise app.DigestError('Could not reach the model API; check runner network access, TLS, and timeout') from None

    print(f'Model answer: {answer[:1000]}')
    if not re.search(r'\b42\b|四十二', answer):
        print('The API request succeeded, but the answer did not contain the expected result (42).', file=sys.stderr)
        return 2
    print('Basic model API test passed. This does not verify web-search support.')
    return 0


def main():
    try:
        return run()
    except app.DigestError as exc:
        print(f'API test failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
