from contextlib import redirect_stdout
from datetime import date
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import web_sources as web

TODAY = date(2026, 10, 19)
SOURCE = dict(id='test', name='权威机构', type='html', url='https://example.com/news/',
              allowed_domains=['example.com'], link_pattern=r'/news/\d+\.html')


def article_html(published='2026-10-19'):
    return (f'<html><head><meta charset="utf-8"><title>制造业运行情况报告</title>'
            f'<meta property="article:published_time" content="{published}T08:00:00+08:00"></head>'
            '<body><nav>导航广告内容</nav><article><h1>制造业运行情况报告</h1>'
            '<p>统计部门发布制造业运行数据，本期制造业运行情况保持稳定，相关行业需求持续改善。'
            '本次报告披露了企业生产经营活动和市场需求情况，对主要行业的发展进行了具体说明。</p>'
            '<p>企业应根据订单情况安排生产计划，持续关注市场和用工需求的变化。相关统计数据均来自'
            '本次发布的原始材料，企业应结合实际经营情况进行分析，并关注后续正式发布的数据。</p>'
            '<p>报告同时说明了主要行业的生产活动、企业经营情况和市场需求趋势，具体数据以原始报告'
            '公布的内容为准，不包含其他来源的预测或推断。</p></article></body></html>').encode()


def candidate(index=1):
    return dict(title='热榜上的简写标题', url=f'https://example.com/news/{index}.html',
                published_date=TODAY, source=SOURCE)


class WebSourceTests(unittest.TestCase):
    def test_actual_extractor_reads_original_title_date_and_body(self):
        document = web.extract_document(article_html(), candidate()['url'], candidate(), TODAY)
        self.assertEqual(document['title'], '制造业运行情况报告')
        self.assertEqual(document['published_date'], '2026-10-19')
        self.assertIn('企业应根据订单情况安排生产计划', document['text'])
        self.assertNotIn('导航广告内容', document['text'])
        self.assertEqual(document['source'], '权威机构')

    def test_old_article_is_rejected_even_if_list_claims_it_is_new(self):
        self.assertIsNone(web.extract_document(article_html('2026-10-10'), candidate()['url'], candidate(), TODAY))

    def test_publication_date_parsing_preserves_time_zone_and_does_not_invent_relative_dates(self):
        self.assertEqual(web.parse_date('2026-10-18T17:00:00Z'), TODAY)
        self.assertEqual(web.parse_date('2026年10月19日'), TODAY)
        self.assertIsNone(web.parse_date('刚刚'))
        self.assertIsNone(web.parse_date('2026-99-99'))

    def test_html_discovery_filters_external_links_and_preserves_list_dates(self):
        page = '<html><meta charset="utf-8"><ul><li><a href="/news/1.html">制造业数据</a>2026-10-19</li>' \
               '<li><a href="https://evil.example/news/2.html">伪造新闻</a>2026-10-19</li></ul></html>'
        with patch('web_sources.fetch_public', return_value=(page.encode(), SOURCE['url'])):
            items = web.discover(SOURCE, {'source_timeout': 15})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['published_date'], TODAY)

    def test_newsnow_uses_real_item_dates_and_does_not_treat_aggregator_text_as_article_body(self):
        source = {**SOURCE, 'type': 'newsnow', 'platform': 'cls-telegraph'}
        payload = {'status': 'cache', 'items': [dict(title='行业新闻', url='https://example.com/news/1.html',
                                                  extra={'date': '2026-10-19'}, content='这不是采集原文')]}
        with patch('web_sources.fetch_public', return_value=(json.dumps(payload).encode(), 'https://news.example/api/s')):
            items = web.discover(source, {'source_timeout': 15, 'newsnow_api_url': 'https://news.example/api/s'})
        self.assertEqual(items[0]['published_date'], TODAY)
        self.assertNotIn('text', items[0])

    def test_redirects_and_links_cannot_escape_source_domains(self):
        handler = web.SourceRedirectHandler(['example.com'])
        for url in ('https://evil.test/article', 'http://example.com/article',
                    'https://user:secret@example.com/article', 'https://127.0.0.1/article'):
            with self.subTest(url=url), self.assertRaises(web.SourceError):
                handler.redirect_request(None, None, 302, '', {}, url)

    def test_all_sources_failing_is_an_error_instead_of_no_news(self):
        settings = dict(sources_path=Path(__file__).resolve().parents[1] / 'config/news_sources.json')
        with patch('web_sources.discover', side_effect=web.SourceError('Source returned HTTP 403')), \
                redirect_stdout(io.StringIO()), self.assertRaisesRegex(web.SourceError, 'All configured'):
            web.collect_documents(settings, TODAY)

    def test_partial_source_failure_still_extracts_usable_articles_and_removes_history(self):
        settings = dict(sources_path='ignored', news_keywords=[], max_articles=4, source_timeout=15)
        items = [candidate(1), candidate(2)]
        with patch('web_sources.load_sources', return_value=[SOURCE, {**SOURCE, 'id': 'failed'}]), \
                patch('web_sources.discover', side_effect=[items, web.SourceError('Source returned HTTP 403')]), \
                patch('web_sources.fetch_public', return_value=(article_html(), items[1]['url'])) as fetch, \
                redirect_stdout(io.StringIO()):
            documents, problems = web.collect_documents(settings, TODAY, {items[0]['url']})
        self.assertEqual(len(documents), 1)
        self.assertEqual(len(problems), 1)
        self.assertEqual(fetch.call_count, 1)

    def test_source_manifest_rejects_unsafe_domains(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sources.json'
            path.write_text(json.dumps({'version': 1, 'sources': [{**SOURCE, 'url': 'https://evil.test/news/'}]}))
            with self.assertRaises(web.SourceError):
                web.load_sources(path)


if __name__ == '__main__':
    unittest.main()
