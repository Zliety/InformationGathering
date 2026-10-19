"""Check provider web-search support with a small request and no email."""

import sys
from pathlib import Path
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import news_digest as app


QUESTION = '请联网搜索 Microsoft 官方网站，并用一句中文说明它是什么。必须实际执行搜索。'


def run():
    config = app.configuration(dry_run=True)
    print('Testing minimal web search through the production request helper', flush=True)
    answer, sources = app.request_search(config, QUESTION)
    if answer == 'NO_NEWS' or not sources:
        raise app.DigestError('Search test returned no usable source URLs')
    print(f'Model answer: {answer[:1000]}')
    print(f'Completed search with {len(sources)} source URL(s).')
    print('Web-search API test passed.')
    return 0


def main():
    try:
        return run()
    except (URLError, TimeoutError, OSError):
        print('Web-search test failed: network or timeout error', file=sys.stderr)
        return 1
    except app.DigestError as exc:
        print(f'Web-search test failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
