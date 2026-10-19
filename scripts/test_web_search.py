"""Check provider web-search support with a small request and no email."""

import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import news_digest as app


QUESTION = '请联网搜索 Microsoft 官方网站，并用一句中文说明它是什么。必须实际执行搜索。'


def run():
    config = app.configuration(dry_run=True)
    if config['style'] == 'responses':
        payload = {**config['extra'], 'model': app.setting('LLM_MODEL'), 'stream': False,
                   'input': QUESTION, 'tools': [{'type': config['tool']}], 'tool_choice': 'required',
                   'include': ['web_search_call.action.sources']}
        endpoint = '/responses'
    else:
        payload = {**config['extra'], 'model': app.setting('LLM_MODEL'), 'stream': False,
                   'messages': [{'role': 'user', 'content': QUESTION}]}
        endpoint = '/chat/completions'

    request = Request(config['base'] + endpoint, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                      method='POST', headers={'Authorization': 'Bearer ' + app.setting('LLM_API_KEY'),
                                              'Content-Type': 'application/json'})
    print(f'Testing minimal web search: {endpoint} (style={config["style"]}, model={app.setting("LLM_MODEL")})',
          flush=True)
    print('This test does not send email or modify report history.', flush=True)

    try:
        data = json.loads(app.fetch(request, config['timeout']))
        if not isinstance(data, dict):
            raise app.DigestError('Model API returned an unsupported response structure')
        if config['style'] == 'responses':
            if data.get('status') != 'completed':
                raise app.DigestError('Model response did not complete')
            output = data.get('output')
            if not isinstance(output, list):
                raise app.DigestError('Model API returned an unsupported response structure')
            searched = any(isinstance(item, dict) and item.get('type') == 'web_search_call'
                           and item.get('status') == 'completed' for item in output)
            answer = '\n'.join(part['text'] for item in output if isinstance(item, dict)
                               and item.get('type') == 'message' for part in item.get('content', [])
                               if isinstance(part, dict) and part.get('type') == 'output_text'
                               and isinstance(part.get('text'), str))
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
            searched = True  # Chat Completions providers expose search through provider-specific response metadata.

        if not searched:
            raise app.DigestError('Response contains no completed web search; verify provider search support and tool name')
        sources = app.source_urls(data)
        if not sources:
            raise app.DigestError('Search completed but returned no source URLs; verify citation metadata support')
        if not answer.strip():
            raise app.DigestError('Model API returned empty text')
    except HTTPError as exc:
        details = app.http_error_details(exc)
        guidance = ('the provider or its search upstream timed out; confirm web-search support, model access, '
                    'and provider-side timeout limits' if exc.code == 524 else
                    'check search tool support, model access, provider settings, and request ID')
        raise app.DigestError(f'Model search API returned HTTP {exc.code}{details}; {guidance}') from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise app.DigestError('Model API returned a non-JSON or invalid response') from None
    except (URLError, TimeoutError, OSError):
        raise app.DigestError('Could not reach the model API; check runner network access, TLS, and timeout') from None

    print(f'Model answer: {answer.strip()[:1000]}')
    print(f'Completed search with {len(sources)} source URL(s).')
    print('Web-search API test passed.')
    return 0


def main():
    try:
        return run()
    except app.DigestError as exc:
        print(f'Web-search test failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
