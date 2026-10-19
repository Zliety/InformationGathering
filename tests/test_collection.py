from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime

import news_digest as app


def documents(count=6):
    return [dict(title=f'原标题{i}', source='国家统计局', url=f'https://example.com/news/{i}',
                 published_date='2026-10-19', text='本期制造业运行情况保持稳定。企业应根据订单情况安排生产计划。')
            for i in range(count)]


def response():
    return json.dumps({'status': 'completed', 'output': [
        {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps({'summary': [
            {'text': '制造业保持稳定。', 'evidence': '本期制造业运行情况保持稳定'},
            {'text': '按订单安排生产。', 'evidence': '企业应根据订单情况安排生产计划'},
        ]})}]},
    ]}).encode()


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'history.json'
        self.env = patch.dict(os.environ, {'LLM_API_BASE_URL': 'https://model.example/v1',
                              'LLM_MODEL': 'plain-model', 'LLM_API_KEY': 'private-key',
                              'HISTORY_PATH': str(self.path)}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_new_pipeline_binds_fields_to_collected_pages_and_never_enables_model_search(self):
        with patch('news_digest.web_sources.collect_documents', return_value=(documents(), [])), \
                patch('news_digest.fetch', return_value=response()) as fetch, redirect_stdout(io.StringIO()):
            text, evidence = app.collect_report(app.configuration(True), [])
        rows = app.parse_report(text, evidence, [])
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0][1], '原标题0')
        self.assertEqual(rows[0][3], '国家统计局')
        self.assertEqual(rows[0][4], 'https://example.com/news/0')
        for call in fetch.call_args_list:
            request = call.args[0]
            self.assertEqual(request.full_url, 'https://model.example/v1/responses')
            self.assertEqual(set(json.loads(request.data)), {'model', 'stream', 'input'})

    def test_unsupported_evidence_is_rejected(self):
        with self.assertRaisesRegex(app.DigestError, 'evidence quote'):
            app.summary_points('{"summary":[{"text":"虚构数据","evidence":"不存在的原文内容至少八字"},'
                               '{"text":"虚构预测","evidence":"另一段不在正文里的文字"}]}', '仅有一段真实正文')

    def test_model_does_not_require_search_metadata_and_chat_is_supported(self):
        os.environ['LLM_API_STYLE'] = 'chat_completions'
        data = {'choices': [{'finish_reason': 'stop', 'message': {'content': '普通回答'}}]}
        with patch('news_digest.fetch', return_value=json.dumps(data).encode()) as fetch:
            self.assertEqual(app.request_model(app.configuration(True), '普通问题'), '普通回答')
        self.assertNotIn('tools', json.loads(fetch.call_args.args[0].data))

    def test_old_search_settings_do_not_select_obsolete_search_paths(self):
        os.environ.update(SEARCH_PROVIDER='tavily', NEWS_COLLECTION_MODE='single',
                          LLM_SEARCH_TOOL='unsupported', LLM_EXTRA_BODY='not-json')
        self.assertEqual(app.configuration(True)['style'], 'responses')

    def test_insufficient_documents_with_source_failures_do_not_call_model(self):
        with patch('news_digest.web_sources.collect_documents', return_value=(documents(2), ['source failed'])), \
                patch('news_digest.fetch') as fetch, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(app.main(['--dry-run']), 1)
        fetch.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_no_new_articles_does_not_call_model_or_send_mail(self):
        with patch('news_digest.web_sources.collect_documents', return_value=([], [])), \
                patch('news_digest.fetch') as fetch, patch('news_digest.send_email') as send, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(app.main(['--dry-run']), 0)
        fetch.assert_not_called()
        send.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_history_and_current_titles_are_in_model_context(self):
        history = [{'table': '', 'articles': [{'title': '已报道事件', 'url': 'https://example.com/old'}]}]
        os.environ['NEWS_PROMPT'] = '重点关注制造业人力'
        with patch('news_digest.web_sources.collect_documents', return_value=(documents(4), [])), \
                patch('news_digest.fetch', return_value=response()) as fetch, redirect_stdout(io.StringIO()):
            app.collect_report(app.configuration(True), history)
        first = json.loads(fetch.call_args_list[0].args[0].data)['input']
        second = json.loads(fetch.call_args_list[1].args[0].data)['input']
        self.assertIn('已报道事件', first)
        self.assertIn('重点关注制造业人力', first)
        self.assertIn('原标题0', second)

    def test_titles_with_pipes_are_preserved_in_the_table(self):
        items = documents(4)
        items[0]['title'] = '原文标题 | 原始副标题'
        with patch('news_digest.web_sources.collect_documents', return_value=(items, [])), \
                patch('news_digest.fetch', return_value=response()), redirect_stdout(io.StringIO()):
            text, sources = app.collect_report(app.configuration(True), [])
        self.assertEqual(app.parse_report(text, sources, [])[0][1], items[0]['title'])

    def test_html_to_preview_pipeline_runs_real_body_extraction(self):
        source_file = Path(self.directory.name) / 'sources.json'
        source_file.write_text(json.dumps({'version': 1, 'sources': [{
            'id': 'local-fixture', 'name': '国家统计局', 'type': 'html',
            'url': 'https://example.com/news/', 'allowed_domains': ['example.com'],
            'link_pattern': r'/news/\d+\.html',
        }]}))
        os.environ['NEWS_SOURCES_PATH'] = str(source_file)
        today = str(datetime.now(app.BEIJING).date())
        listing = '<html><meta charset="utf-8"><ul>' + ''.join(
            f'<li><a href="/news/{i}.html">制造业报告{i}</a>{today}</li>' for i in range(4)) + '</ul></html>'
        def fetch_page(url, domains, timeout):
            if url.endswith('/news/'):
                return listing.encode(), url
            index = url.rsplit('/', 1)[1].split('.')[0]
            paragraphs = '<p>' + documents()[0]['text'] * 5 + '</p>'
            page = (f'<html><head><meta charset="utf-8"><title>制造业原文报告{index}</title>'
                    f'<meta property="article:published_time" content="{today}"></head><body>'
                    f'<article><h1>制造业原文报告{index}</h1>{paragraphs}</article></body></html>')
            return page.encode(), url
        with patch('web_sources.fetch_public', side_effect=fetch_page) as web_fetch, \
                patch('news_digest.fetch', return_value=response()) as model_fetch, \
                patch('news_digest.send_email') as send, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(app.main(['--dry-run']), 0)
        self.assertEqual(web_fetch.call_count, 5)
        self.assertEqual(model_fetch.call_count, 4)
        self.assertIn('制造业原文报告0', (self.path.parent / 'report.md').read_text())
        self.assertFalse(self.path.exists())
        send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
