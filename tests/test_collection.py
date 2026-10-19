from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import news_digest as app


def candidate(index, **changes):
    article = dict(title=f'原文标题{index}', summary=['报道中的事实', '报道中的数据'],
                   source='国家统计局', url=f'https://example.com/news/{index}',
                   published_date=str(datetime.now(app.BEIJING).date()))
    article.update(changes)
    return json.dumps(article, ensure_ascii=False), {app.canonical_url(article['url'])}


def provider_response(index):
    text, sources = candidate(index)
    return json.dumps({'status': 'completed', 'output': [
        {'type': 'web_search_call', 'status': 'completed',
         'action': {'sources': [{'url': url} for url in sources]}},
        {'type': 'message', 'content': [{'type': 'output_text', 'text': text}]},
    ]}).encode()


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'history.json'
        environment = patch.dict(os.environ, {
            'LLM_API_BASE_URL': 'https://model.example/v1', 'LLM_MODEL': 'test-model',
            'LLM_API_KEY': 'test-key', 'HISTORY_PATH': str(self.path),
        }, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def collect(self, results, history=None):
        with patch('news_digest.request_search', side_effect=results) as search, redirect_stdout(io.StringIO()):
            text, sources = app.collect_report(app.configuration(True), history or [])
        return text, sources, search

    def test_default_batches_generate_valid_table_and_compact_context(self):
        history = [{'table': 'private-full-report-marker', 'articles': [
            {'title': '历史标题', 'url': 'https://example.com/old'}]}]
        text, sources, search = self.collect([candidate(i) for i in range(7)], history)
        self.assertEqual(len(app.parse_report(text, sources, history)), 7)
        self.assertEqual(search.call_count, 7)
        first_config, first_messages = search.call_args_list[0].args
        self.assertEqual(first_config['timeout'], 90)
        self.assertNotIn('private-full-report-marker', json.dumps(first_messages))
        self.assertIn('历史标题', first_messages[0]['content'])
        self.assertIn('原文标题0', search.call_args_list[1].args[1][0]['content'])
        self.assertEqual(first_messages[1]['content'], app.configuration(True)['prompt'])

    def test_timeout_of_one_dimension_does_not_lose_other_articles(self):
        error = app.ModelAPIError('Model API returned HTTP 524', 524)
        text, sources, _ = self.collect([error] + [candidate(i) for i in range(6)])
        self.assertEqual(len(app.parse_report(text, sources, [])), 6)

    def test_failed_tasks_with_insufficient_articles_fail_instead_of_no_news(self):
        results = [candidate(i) for i in range(3)] + [TimeoutError()] * 4
        with self.assertRaisesRegex(app.DigestError, 'Fewer than 4'), redirect_stdout(io.StringIO()):
            self.collect(results)

    def test_authentication_error_stops_remaining_requests(self):
        with patch('news_digest.request_search', side_effect=app.ModelAPIError('HTTP 401', 401)) as search:
            with self.assertRaisesRegex(app.DigestError, 'HTTP 401'), redirect_stdout(io.StringIO()):
                app.collect_report(app.configuration(True), [])
        self.assertEqual(search.call_count, 1)

    def test_cross_dimension_and_history_duplicates_are_removed(self):
        history = [{'table': '', 'articles': [{'title': '原文标题0', 'url': 'https://example.com/news/0'}]}]
        text, sources, _ = self.collect([candidate(0), candidate(1), candidate(1)] +
                                       [candidate(i) for i in range(2, 6)], history)
        self.assertEqual(len(app.parse_report(text, sources, history)), 5)

    def test_no_news_does_not_send_or_save(self):
        with patch('news_digest.request_search', return_value=('NO_NEWS', set())), \
                patch('news_digest.send_email') as send, redirect_stdout(io.StringIO()):
            self.assertEqual(app.main(['--dry-run']), 0)
        send.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_rejects_old_future_dates_and_unrelated_source_metadata(self):
        today = datetime.now(app.BEIJING).date()
        for date in (today - timedelta(days=3), today + timedelta(days=1)):
            text, sources = candidate(1, published_date=str(date))
            with self.assertRaisesRegex(app.DigestError, 'date window'):
                app.candidate_row(text, sources, today)
        text, _ = candidate(1)
        with self.assertRaisesRegex(app.DigestError, 'own search metadata'):
            app.candidate_row(text, {'https://example.com/unrelated'}, today)

    def test_custom_prompt_preserved_and_batch_timeout_respects_global_timeout(self):
        os.environ.update(NEWS_PROMPT='自定义企业及城市要求', LLM_TIMEOUT='20', LLM_BATCH_TIMEOUT='40')
        _, _, search = self.collect([candidate(i) for i in range(7)])
        config, messages = search.call_args.args
        self.assertEqual(config['timeout'], 20)
        self.assertEqual(messages[1]['content'], '自定义企业及城市要求')

    def test_real_batch_pipeline_uses_seven_searches_without_mail_or_history_in_dry_run(self):
        with patch('news_digest.fetch', side_effect=[provider_response(i) for i in range(7)]) as fetch, \
                patch('news_digest.send_email') as send, redirect_stdout(io.StringIO()):
            self.assertEqual(app.main(['--dry-run']), 0)
        self.assertEqual(fetch.call_count, 7)
        self.assertFalse(self.path.exists())
        send.assert_not_called()
        for call in fetch.call_args_list:
            payload = json.loads(call.args[0].data)
            self.assertEqual(payload['tools'], [{'type': 'web_search'}])

    def test_batched_pipeline_sends_only_validated_table_and_saves_history(self):
        os.environ.update(SMTP_HOST='smtp.example.com', SMTP_USERNAME='sender@example.com',
                          SMTP_PASSWORD='smtp-secret', MAIL_TO='recipient@example.com')
        with patch('news_digest.fetch', side_effect=[provider_response(i) for i in range(7)]), \
                patch('news_digest.send_email') as send, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(app.main([]), 0)
        self.assertEqual(len(send.call_args.args[2]), 7)
        self.assertEqual(len(app.load_history(self.path)[0]['articles']), 7)

    def test_actual_http_524_isolated_and_remaining_tasks_finish(self):
        error = HTTPError('https://model.example/v1/responses', 524, 'Timeout', {}, io.BytesIO(b''))
        output = io.StringIO()
        results = [error] + [provider_response(i) for i in range(6)]
        with patch('news_digest.fetch', side_effect=results) as fetch, redirect_stdout(output):
            self.assertEqual(app.main(['--dry-run']), 0)
        self.assertEqual(fetch.call_count, 7)
        self.assertIn('HTTP 524', output.getvalue())
        self.assertIn('6 articles, 1 failed tasks', output.getvalue())
        self.assertFalse(self.path.exists())

    def test_chat_completions_batch_pipeline_validates_citations(self):
        os.environ.update(LLM_API_STYLE='chat_completions', LLM_EXTRA_BODY='{"web_search_options":{}}')
        results = []
        for index in range(7):
            text, sources = candidate(index)
            results.append(json.dumps({'choices': [{'finish_reason': 'stop', 'message': {
                'content': text, 'annotations': [{'url': url} for url in sources],
            }}]}).encode())
        with patch('news_digest.fetch', side_effect=results) as fetch, redirect_stdout(io.StringIO()):
            self.assertEqual(app.main(['--dry-run']), 0)
        self.assertEqual(fetch.call_count, 7)
        self.assertTrue(all(call.args[0].full_url.endswith('/chat/completions')
                            for call in fetch.call_args_list))


if __name__ == '__main__':
    unittest.main()
