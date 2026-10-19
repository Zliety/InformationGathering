from contextlib import redirect_stdout
from datetime import datetime
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import news_digest as app


def search_result(index=0, **changes):
    result = dict(title=f'原文标题{index}', url=f'https://example.com/news/{index}',
                  raw_content='原始报道中的核心事实和相关数据。',
                  published_date=str(datetime.now(app.BEIJING).date()))
    result.update(changes)
    return json.dumps({'results': [result]}).encode()


def extraction_result(index=0, **changes):
    article = dict(title=f'原文标题{index}', url=f'https://example.com/news/{index}',
                   summary=['核心事实', '相关数据'], source='国家统计局',
                   published_date=str(datetime.now(app.BEIJING).date()))
    article.update(changes)
    return json.dumps({'status': 'completed', 'output': [
        {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(article)}]},
    ]}).encode()


class SearchProviderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'history.json'
        environment = patch.dict(os.environ, {
            'LLM_API_BASE_URL': 'https://model.example/v1', 'LLM_MODEL': 'test-model',
            'LLM_API_KEY': 'private-model-key', 'SEARCH_PROVIDER': 'tavily',
            'TAVILY_API_KEY': 'private-search-key', 'HISTORY_PATH': str(self.path),
        }, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.today = datetime.now(app.BEIJING).date()

    def article(self):
        with redirect_stdout(io.StringIO()):
            return app.request_article(app.configuration(True), app.SEARCH_DIMENSIONS[0], self.today, [])

    def test_external_search_requires_its_key_and_does_not_enable_legacy_mode(self):
        del os.environ['TAVILY_API_KEY']
        with self.assertRaisesRegex(app.DigestError, 'TAVILY_API_KEY'):
            app.configuration(True)
        os.environ['NEWS_COLLECTION_MODE'] = 'single'
        with self.assertRaisesRegex(app.DigestError, 'requires.*batched'):
            app.configuration(True)

    def test_plain_chat_api_is_allowed_without_provider_search_options(self):
        os.environ['LLM_API_STYLE'] = 'chat_completions'
        self.assertEqual(app.configuration(True)['extra'], {})

    def test_search_key_goes_only_to_search_service_and_model_uses_no_web_tool(self):
        with patch('news_digest.fetch', side_effect=[search_result(), extraction_result()]) as fetch:
            text, sources = self.article()
        self.assertEqual(app.candidate_row(text, sources, self.today)[0], '原文标题0')
        search, model = [call.args[0] for call in fetch.call_args_list]
        self.assertEqual(search.full_url, 'https://api.tavily.com/search')
        self.assertEqual(search.get_header('Authorization'), 'Bearer private-search-key')
        self.assertEqual(model.get_header('Authorization'), 'Bearer private-model-key')
        body = json.loads(search.data)
        self.assertFalse(body['include_answer'])
        self.assertEqual(body['include_raw_content'], 'text')
        self.assertNotIn('private-model-key', search.data.decode())
        body = json.loads(model.data)
        self.assertNotIn('tools', body)
        self.assertNotIn('tool_choice', body)
        self.assertNotIn('private-search-key', model.data.decode())

    def test_missing_raw_article_content_fails_without_using_generated_snippets(self):
        with patch('news_digest.fetch', return_value=search_result(raw_content=None)) as fetch:
            with self.assertRaisesRegex(app.DigestError, 'no usable source article text'):
                self.article()
        self.assertEqual(fetch.call_count, 1)

    def test_no_search_results_skips_model_extraction(self):
        with patch('news_digest.fetch', return_value=b'{"results": []}') as fetch:
            self.assertEqual(self.article(), ('NO_NEWS', set()))
        self.assertEqual(fetch.call_count, 1)

    def test_model_cannot_invent_a_title_even_when_url_is_valid(self):
        with patch('news_digest.fetch', side_effect=[search_result(), extraction_result(title='编造标题')]):
            with self.assertRaisesRegex(app.DigestError, 'does not match'):
                self.article()

    def test_long_documents_are_explicitly_marked_as_excerpts(self):
        with patch('news_digest.fetch', return_value=search_result(raw_content='正文' * 5000)):
            text, sources, titles = app.search_tavily(app.configuration(True), '宏观经济', self.today)
        doc = json.loads(text)['source_documents'][0]
        self.assertEqual(len(doc['text']), 6000)
        self.assertTrue(doc['text_truncated'])
        self.assertEqual(sources, set(titles))

    def test_search_service_error_does_not_log_raw_error_message_or_keys(self):
        error = HTTPError('https://api.tavily.com/search', 401, 'Unauthorized', {},
                          io.BytesIO(b'{"error":{"message":"private-search-key"}}'))
        with patch('news_digest.fetch', side_effect=error):
            with self.assertRaises(app.ModelAPIError) as raised:
                self.article()
        self.assertIn('Tavily search returned HTTP 401', str(raised.exception))
        self.assertNotIn('private-search-key', str(raised.exception))

    def test_full_daily_pipeline_can_run_without_any_model_search_calls(self):
        responses = [response for i in range(7) for response in (search_result(i), extraction_result(i))]
        with patch('news_digest.fetch', side_effect=responses) as fetch, \
                patch('news_digest.send_email') as send, redirect_stdout(io.StringIO()):
            self.assertEqual(app.main(['--dry-run']), 0)
        self.assertEqual(fetch.call_count, 14)
        self.assertFalse(self.path.exists())
        send.assert_not_called()
        for index, call in enumerate(fetch.call_args_list):
            request = call.args[0]
            if index % 2 == 0:
                self.assertEqual(request.full_url, 'https://api.tavily.com/search')
            else:
                self.assertEqual(request.full_url, 'https://model.example/v1/responses')
                self.assertNotIn('tools', json.loads(request.data))


if __name__ == '__main__':
    unittest.main()
