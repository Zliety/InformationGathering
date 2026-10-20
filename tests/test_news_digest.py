from contextlib import redirect_stderr, redirect_stdout
from email import policy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

import news_digest as app

ENV = {
    'LLM_API_BASE_URL': 'https://model.example/v1',
    'LLM_MODEL': 'search-model', 'LLM_API_KEY': 'test-only-key',
    'SMTP_HOST': 'smtp.example.com', 'SMTP_USERNAME': 'sender@example.com',
    'SMTP_PASSWORD': 'test-only-password', 'MAIL_TO': 'one@example.com,two@example.com',
}


def table(prefix='new'):
    header = '| ' + ' | '.join(app.HEADERS) + ' |\n| --- | --- | --- | --- | --- |'
    rows = [f'| {n} | {prefix}标题{n} | 1. 核心事实<br>2. 原文数据 | 国家统计局 | https://example.com/{prefix}/{n} |' for n in range(1, 5)]
    return header + '\n' + '\n'.join(rows)


def sources(prefix='new'):
    return {f'https://example.com/{prefix}/{n}' for n in range(1, 5)}


def documents():
    return [dict(title=f'new标题{n}', source='国家统计局', url=f'https://example.com/new/{n}',
                 published_date='2026-10-19',
                 text='核心事实是本次发布的数据。原文数据均来自正式统计资料。') for n in range(1, 5)]


def response():
    text = json.dumps({'summary': [
        {'text': '核心事实', 'evidence': '核心事实是本次发布的数据'},
        {'text': '原文数据', 'evidence': '原文数据均来自正式统计资料'},
    ]})
    return {'status': 'completed', 'output': [
        {'type': 'message', 'content': [{'type': 'output_text', 'text': text}]},
    ]}


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'history.json'
        self.environment = patch.dict(os.environ, {**ENV, 'HISTORY_PATH': str(self.path)}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.collector = patch('news_digest.web_sources.collect_documents', return_value=(documents(), []))
        self.collector.start()
        self.addCleanup(self.collector.stop)

    def test_default_prompt_has_user_requirements(self):
        prompt = app.configuration()['prompt']
        for term in ('比亚迪', '立讯精密', '蓝思科技', '歌尔股份', 'DL/IDL', '近三次', '4-8', '不可自行总结生成'):
            self.assertIn(term, prompt)

    def test_configuration_requires_credentials_before_work(self):
        del os.environ['LLM_API_KEY']
        with self.assertRaisesRegex(app.DigestError, 'LLM_API_KEY'):
            app.configuration()

    def test_dry_run_does_not_require_email(self):
        for name in ('SMTP_HOST', 'SMTP_USERNAME', 'SMTP_PASSWORD', 'MAIL_TO'):
            del os.environ[name]
        self.assertIsNone(app.configuration(True)['port'])
        self.assertEqual(app.configuration(True)['channels'], [])
        with self.assertRaisesRegex(app.DigestError, 'No delivery channel configured'):
            app.configuration()





    def test_all_configured_channels_are_detected_without_selection(self):
        os.environ.update(SERVERCHAN_SENDKEY='SCTtestkey', PUSHPLUS_TOKEN='push-token',
                          PUSH_CHANNELS='email')
        self.assertEqual(app.configuration()['channels'], ['email', 'serverchan', 'pushplus'])

    def test_push_only_does_not_require_mail_credentials(self):
        for name in ('SMTP_HOST', 'SMTP_USERNAME', 'SMTP_PASSWORD', 'MAIL_TO'):
            del os.environ[name]
        os.environ['PUSHPLUS_TOKEN'] = 'push-token'
        config = app.configuration()
        self.assertEqual(config['channels'], ['pushplus'])
        self.assertIsNone(config['port'])

    def test_partial_email_configuration_reports_missing_fields(self):
        del os.environ['SMTP_PASSWORD']
        os.environ['PUSHPLUS_TOKEN'] = 'push-token'
        with self.assertRaisesRegex(app.DigestError, 'SMTP_PASSWORD'):
            app.configuration()

    def test_gmail_aliases_enable_email_without_generic_smtp_names(self):
        for name in ('SMTP_HOST', 'SMTP_USERNAME', 'SMTP_PASSWORD'):
            os.environ['GMAIL_' + name] = os.environ.pop(name)
        self.assertEqual(app.configuration()['channels'], ['email'])

    @patch('news_digest.push_channels.deliver')
    @patch('news_digest.send_email', side_effect=OSError('private diagnostic'))
    def test_failed_channel_does_not_prevent_other_channels_being_attempted(self, mail, push):
        os.environ.update(SERVERCHAN_SENDKEY='SCTtestkey', PUSHPLUS_TOKEN='push-token')
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaisesRegex(app.DigestError, 'email'):
            app.send_notifications(app.configuration(), 'summary', [], notification=True)
        self.assertEqual([call.args[0] for call in push.call_args_list], ['serverchan', 'pushplus'])
        self.assertNotIn('private diagnostic', output.getvalue())
        self.assertFalse(self.path.exists())

    def test_rejects_insecure_or_invalid_configuration(self):
        for name, value in (('SMTP_SECURITY', 'none'), ('LLM_API_BASE_URL', 'http://model.example/v1'),
                            ('LLM_API_BASE_URL', 'https://user:pass@model.example/v1'),
                            ('LLM_TIMEOUT', '0'), ('MAIL_SUBJECT', 'hello\ninjection')):
            with self.subTest(name=name), patch.dict(os.environ, {name: value}):
                with self.assertRaises(app.DigestError):
                    app.configuration()


    @patch('news_digest.fetch')
    def test_http_error_logs_safe_code_and_request_id_not_message(self, fetch):
        body = json.dumps({'error': {
            'type': 'server_error', 'code': 'upstream_error',
            'message': 'do not print test-only-key or the private prompt',
        }}).encode()
        error = HTTPError('https://model.example/v1/responses', 502, 'Bad Gateway',
                          {'x-request-id': 'req_test-123'}, io.BytesIO(body))
        fetch.side_effect = error
        with self.assertRaises(app.DigestError) as raised:
            app.request_model(app.configuration(), 'ordinary question')
        self.assertIn('HTTP 502', str(raised.exception))
        self.assertIn('type=server_error', str(raised.exception))
        self.assertIn('code=upstream_error', str(raised.exception))
        self.assertIn('request_id=req_test-123', str(raised.exception))
        self.assertNotIn('test-only-key', str(raised.exception))
        self.assertNotIn('private prompt', str(raised.exception))


    def test_valid_table_preserves_title_and_summary(self):
        rows = app.parse_report(table(), sources(), [])
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0][1], 'new标题1')
        self.assertIn('<br>2.', rows[0][2])

    def test_invalid_table_and_unverified_url_are_rejected(self):
        examples = [table().replace('序号 (No.)', '序号'), 'Here is the report\n' + table(),
                    table().replace('https://example.com/new/1', 'https://invented.example/1'),
                    table().replace('1. 核心事实<br>2. 原文数据', 'unstructured summary'),
                    '\n'.join(table().splitlines()[:2]),
                    table().replace('| 2 |', '| 9 |')]
        for text in examples:
            with self.subTest(text=text):
                with self.assertRaises(app.DigestError):
                    app.parse_report(text, sources(), [])

    def test_one_to_eight_items_are_accepted_but_empty_or_nine_are_rejected(self):
        header = table().splitlines()[:2]
        for count in (1, 2, 3, 4, 8):
            rows = [f'| {n} | 标题{n} | 1. 核心事实<br>2. 原文数据 | 来源 | https://example.com/item/{n} |'
                    for n in range(1, count + 1)]
            urls = {f'https://example.com/item/{n}' for n in range(1, count + 1)}
            with self.subTest(count=count):
                self.assertEqual(len(app.parse_report('\n'.join(header + rows), urls, [])), count)
        rows = [f'| {n} | 标题{n} | 1. 核心事实<br>2. 原文数据 | 来源 | https://example.com/item/{n} |'
                for n in range(1, 10)]
        for items in ([], rows):
            with self.assertRaises(app.DigestError):
                app.parse_report('\n'.join(header + items),
                                 {f'https://example.com/item/{n}' for n in range(1, 10)}, [])

    def test_history_and_current_report_duplicates_are_rejected(self):
        rows = app.parse_report(table(), sources(), [])
        app.save_history(self.path, [], table(), rows)
        history = app.load_history(self.path)
        with self.assertRaisesRegex(app.DigestError, 'repeats'):
            app.parse_report(table(), sources(), history)
        changed_url = table().replace('/new/1', '/different/1')
        with self.assertRaisesRegex(app.DigestError, 'repeats'):
            app.parse_report(changed_url, sources() | {'https://example.com/different/1'}, history)
        same_report = table().replace('/new/2', '/new/1')
        with self.assertRaisesRegex(app.DigestError, 'repeats'):
            app.parse_report(same_report, sources(), [])

    def test_tracking_parameters_do_not_bypass_url_deduplication(self):
        self.assertEqual(app.canonical_url('https://example.com/news?utm_source=x#part'),
                         app.canonical_url('https://example.com/news'))
        self.assertEqual(app.title_key('ＡＢＣ： 新闻'), app.title_key('abc新闻'))

    def test_history_persistence_keeps_only_last_three_reports(self):
        for n in range(5):
            prefix = f'run{n}'
            history = app.load_history(self.path)
            rows = app.parse_report(table(prefix), sources(prefix), history)
            app.save_history(self.path, history, table(prefix), rows)
        history = app.load_history(self.path)
        self.assertEqual(len(history), 3)
        self.assertIn('run2', history[0]['table'])
        self.assertIn('run4', history[-1]['table'])

    def test_corrupt_history_does_not_silently_reset(self):
        for value in ('bad json', '{}', '{"version":1,"reports":[{}]}'):
            self.path.write_text(value)
            with self.assertRaisesRegex(app.DigestError, 'History'):
                app.load_history(self.path)

    def test_html_escapes_external_content_and_renders_table(self):
        rows = app.parse_report(table(), sources(), [])
        rows[0][1] = '<script>alert(1)</script>'
        rendered = app.html_table(rows)
        self.assertNotIn('<script>', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        self.assertEqual(rendered.count('<th '), 5)
        self.assertEqual(rendered.count('<td '), 20)
        self.assertIn('1. 核心事实</p>', rendered)
        self.assertIn('2. 原文数据</p>', rendered)
        self.assertIn('href="https://example.com/new/1"', rendered)
        self.assertIn('查看原文</a>', rendered)

    def test_redirect_never_forwards_api_credentials(self):
        request = Request('https://model.example/v1/responses', headers={'Authorization': 'Bearer test-only-key'})
        with self.assertRaisesRegex(app.DigestError, 'final endpoint'):
            app.SafeRedirectHandler().redirect_request(request, None, 302, '', {}, 'https://other.example/api')





    @patch('news_digest.smtplib.SMTP_SSL')
    def test_ssl_email_contains_only_report_in_plain_and_html_parts(self, factory):
        smtp = factory.return_value.__enter__.return_value
        smtp.send_message.return_value = {}
        rows = app.parse_report(table(), sources(), [])
        app.send_email(app.configuration(), table(), rows)
        smtp.login.assert_called_once_with('sender@example.com', 'test-only-password')
        message = smtp.send_message.call_args.args[0]
        self.assertEqual(message.get_body(('plain',)).get_content().strip(), table())
        self.assertIn('<table ', message.get_body(('html',)).get_content())
        self.assertIn('two@example.com', str(message['To']))
        self.assertIn('+0800', str(message['Date']))
        self.assertIn(b'multipart/alternative', message.as_bytes(policy=policy.SMTP))
        smtp.starttls.assert_not_called()

    @patch('news_digest.smtplib.SMTP')
    def test_starttls_precedes_login(self, factory):
        os.environ['SMTP_SECURITY'] = 'starttls'
        smtp = factory.return_value.__enter__.return_value
        smtp.send_message.return_value = {}
        config = app.configuration()
        self.assertEqual(config['port'], 587)
        app.send_email(config, table(), app.parse_report(table(), sources(), []))
        names = [call[0] for call in smtp.method_calls]
        self.assertLess(names.index('starttls'), names.index('login'))

    @patch('news_digest.smtplib.SMTP_SSL')
    def test_partial_recipient_rejection_fails(self, factory):
        factory.return_value.__enter__.return_value.send_message.return_value = {'one@example.com': (550, b'No')}
        with self.assertRaisesRegex(app.DigestError, 'some recipients'):
            app.send_email(app.configuration(), table(), app.parse_report(table(), sources(), []))

    @patch('news_digest.send_email')
    @patch('news_digest.fetch')
    def test_full_pipeline_and_dry_run_history_behavior(self, fetch, send):
        output_path = self.path.parent / 'github-output'
        os.environ['GITHUB_OUTPUT'] = str(output_path)
        for args, should_send in ((['--dry-run'], False), ([], True)):
            with self.subTest(args=args):
                send.reset_mock()
                output_path.write_text('') if output_path.exists() else None
                fetch.return_value = json.dumps(response()).encode()
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(app.main(args), 0)
                self.assertEqual(send.called, should_send)
                self.assertEqual(self.path.exists(), should_send)
                if should_send:
                    self.assertEqual(output_path.read_text(), 'report_ready=true\nreport_created=true\n')
                else:
                    self.assertEqual(output_path.read_text(), 'report_ready=true\n')

    @patch('news_digest.send_email')
    @patch('news_digest.fetch')
    def test_no_news_sends_notification_without_saving_or_uploading_history(self, fetch, send):
        output_path = self.path.parent / 'github-output'
        os.environ['GITHUB_OUTPUT'] = str(output_path)
        result = response()
        result['output'][0]['content'][0]['text'] = 'NO_NEWS'
        fetch.return_value = json.dumps(result).encode()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(app.main([]), 0)
        send.assert_called_once()
        self.assertTrue(send.call_args.kwargs['notification'])
        self.assertIn('本次无符合条件的新资讯', send.call_args.args[1])
        self.assertIn('合格资讯：0', send.call_args.args[1])
        self.assertFalse(self.path.exists())
        self.assertFalse(output_path.exists())

    @patch('news_digest.send_email')
    @patch('news_digest.fetch')
    def test_api_failure_never_sends_or_logs_secrets(self, fetch, send):
        fetch.side_effect = HTTPError('https://model.example', 401, 'test-only-key', {}, None)
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            self.assertEqual(app.main([]), 1)
        self.assertNotIn('test-only-key', output.getvalue())
        self.assertIn('HTTP 401', output.getvalue())
        send.assert_not_called()
        self.assertFalse(self.path.exists())

    @patch('news_digest.send_email', side_effect=OSError('sensitive SMTP diagnostic'))
    @patch('news_digest.fetch')
    def test_failed_delivery_does_not_advance_history(self, fetch, send):
        fetch.return_value = json.dumps(response()).encode()
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            self.assertEqual(app.main([]), 1)
        self.assertIn('notification delivery', output.getvalue())
        self.assertNotIn('sensitive', output.getvalue())
        self.assertFalse(self.path.exists())

    @patch('news_digest.fetch')
    def test_validation_never_uses_network(self, fetch):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(app.main(['--validate-config']), 0)
        fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
