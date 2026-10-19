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
from urllib.parse import parse_qs
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


def response(prefix='new'):
    return {'status': 'completed', 'output': [
        {'type': 'web_search_call', 'status': 'completed',
         'action': {'sources': [{'url': url} for url in sources(prefix)]}},
        {'type': 'message', 'content': [{'type': 'output_text', 'text': table(prefix)}]},
    ]}


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'history.json'
        self.environment = patch.dict(os.environ, {**ENV, 'HISTORY_PATH': str(self.path)}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

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
        self.assertEqual(app.configuration(True)['port'], 465)
        with self.assertRaisesRegex(app.DigestError, 'SMTP_HOST'):
            app.configuration()

    def test_graph_configuration_requires_application_credentials_not_smtp_password(self):
        os.environ.update(MAIL_PROVIDER='graph', MS_TENANT_ID='00000000-0000-0000-0000-000000000001',
                          MS_CLIENT_ID='00000000-0000-0000-0000-000000000002',
                          MS_CLIENT_SECRET='application-secret', MAIL_FROM='sender@example.com')
        for name in ('SMTP_HOST', 'SMTP_USERNAME', 'SMTP_PASSWORD'):
            os.environ.pop(name, None)
        config = app.configuration()
        self.assertEqual(config['mail_provider'], 'graph')
        self.assertIsNone(config['port'])
        del os.environ['MS_CLIENT_SECRET']
        with self.assertRaisesRegex(app.DigestError, 'MS_CLIENT_SECRET'):
            app.configuration()

    def test_graph_requires_valid_tenant_and_client_ids(self):
        os.environ.update(MAIL_PROVIDER='graph', MS_TENANT_ID='tenant', MS_CLIENT_ID='client',
                          MS_CLIENT_SECRET='secret', MAIL_FROM='sender@example.com')
        with self.assertRaisesRegex(app.DigestError, 'UUID'):
            app.configuration()

    def test_personal_graph_configuration_needs_delegated_refresh_token(self):
        os.environ.update(MAIL_PROVIDER='graph', MS_AUTH_MODE='delegated', MS_AUTHORITY='consumers',
                          MS_CLIENT_ID='00000000-0000-0000-0000-000000000002',
                          MS_REFRESH_TOKEN='refresh-token', MAIL_TO='receiver@example.com')
        for name in ('MS_TENANT_ID', 'MS_CLIENT_SECRET', 'MAIL_FROM', 'SMTP_HOST', 'SMTP_USERNAME', 'SMTP_PASSWORD'):
            os.environ.pop(name, None)
        config = app.configuration()
        self.assertEqual(config['auth_mode'], 'delegated')
        self.assertEqual(config['authority'], 'consumers')
        self.assertEqual(config['sender'], '')
        self.assertIsNone(config['port'])
        del os.environ['MS_REFRESH_TOKEN']
        with self.assertRaisesRegex(app.DigestError, 'MS_REFRESH_TOKEN'):
            app.configuration()

    def test_rejects_invalid_or_reserved_extra_parameters(self):
        for value in ('not json', '[]', '{"messages":[]}', '{"tools":[]}', '{"stream":true}'):
            with self.subTest(value=value), patch.dict(os.environ, {'LLM_EXTRA_BODY': value}):
                with self.assertRaises(app.DigestError):
                    app.configuration()

    def test_chat_requires_search_configuration(self):
        os.environ['LLM_API_STYLE'] = 'chat_completions'
        with self.assertRaisesRegex(app.DigestError, 'search options'):
            app.configuration()

    def test_rejects_insecure_or_invalid_configuration(self):
        for name, value in (('SMTP_SECURITY', 'none'), ('LLM_API_BASE_URL', 'http://model.example/v1'),
                            ('LLM_API_BASE_URL', 'https://user:pass@model.example/v1'),
                            ('LLM_TIMEOUT', '0'), ('MAIL_SUBJECT', 'hello\ninjection')):
            with self.subTest(name=name), patch.dict(os.environ, {name: value}):
                with self.assertRaises(app.DigestError):
                    app.configuration()

    @patch('news_digest.fetch')
    def test_responses_request_forces_search_and_includes_history(self, fetch):
        old = [{'table': 'old-report-marker', 'articles': []}]
        fetch.return_value = json.dumps(response()).encode()
        text, evidence = app.request_report(app.configuration(), old)
        request = fetch.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, 'https://model.example/v1/responses')
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-only-key')
        self.assertEqual(payload['tools'], [{'type': 'web_search'}])
        self.assertEqual(payload['tool_choice'], 'required')
        self.assertEqual(payload['include'], ['web_search_call.action.sources'])
        self.assertIn('old-report-marker', payload['input'][0]['content'])
        self.assertIn('+08:00', payload['input'][0]['content'])
        self.assertEqual(text, table())
        self.assertEqual(evidence, sources())

    @patch('news_digest.fetch')
    def test_custom_chat_search_options_and_citations(self, fetch):
        os.environ.update(LLM_API_STYLE='chat_completions', LLM_EXTRA_BODY='{"web_search_options":{}}', NEWS_PROMPT='Custom prompt')
        fetch.return_value = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {
            'content': table(), 'annotations': [{'type': 'url_citation', 'url_citation': {'url': url}} for url in sources()]}}]}).encode()
        text, evidence = app.request_report(app.configuration(), [])
        payload = json.loads(fetch.call_args.args[0].data)
        self.assertIn('web_search_options', payload)
        self.assertEqual(payload['messages'][1]['content'], 'Custom prompt')
        self.assertEqual(evidence, sources())
        self.assertEqual(text, table())

    @patch('news_digest.fetch')
    def test_model_without_completed_search_or_metadata_is_rejected(self, fetch):
        no_search = response()
        no_search['output'] = no_search['output'][1:]
        no_metadata = response()
        no_metadata['output'][0]['action'] = {}
        truncated = response()
        truncated['status'] = 'incomplete'
        for result in (no_search, no_metadata, truncated):
            with self.subTest(result=result):
                fetch.return_value = json.dumps(result).encode()
                with self.assertRaises(app.DigestError):
                    app.request_report(app.configuration(), [])

    def test_generated_urls_are_not_evidence(self):
        self.assertEqual(app.source_urls({'message': {'content': table()}, 'input': {'url': 'https://example.com'}}), set())

    def test_valid_table_preserves_title_and_summary(self):
        rows = app.parse_report(table(), sources(), [])
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0][1], 'new标题1')
        self.assertIn('<br>2.', rows[0][2])

    def test_invalid_table_and_unverified_url_are_rejected(self):
        examples = [table().replace('序号 (No.)', '序号'), 'Here is the report\n' + table(),
                    table().replace('https://example.com/new/1', 'https://invented.example/1'),
                    table().replace('1. 核心事实<br>2. 原文数据', 'unstructured summary'),
                    '\n'.join(table().splitlines()[:-1]),
                    table().replace('| 2 |', '| 9 |')]
        for text in examples:
            with self.subTest(text=text):
                with self.assertRaises(app.DigestError):
                    app.parse_report(text, sources(), [])

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
        self.assertIn('1. 核心事实<br>2. 原文数据', rendered)
        self.assertIn('<a href="https://example.com/new/1">', rendered)

    def test_redirect_never_forwards_api_credentials(self):
        request = Request('https://model.example/v1/responses', headers={'Authorization': 'Bearer test-only-key'})
        with self.assertRaisesRegex(app.DigestError, 'final endpoint'):
            app.SafeRedirectHandler().redirect_request(request, None, 302, '', {}, 'https://other.example/api')

    @patch('news_digest.fetch')
    def test_graph_oauth_and_sendmail_payload(self, fetch):
        os.environ.update(MAIL_PROVIDER='graph', MS_TENANT_ID='00000000-0000-0000-0000-000000000001',
                          MS_CLIENT_ID='00000000-0000-0000-0000-000000000002',
                          MS_CLIENT_SECRET='application-secret', MAIL_FROM='sender@example.com')
        for name in ('SMTP_HOST', 'SMTP_USERNAME', 'SMTP_PASSWORD'):
            os.environ.pop(name, None)
        config = app.configuration()
        rows = app.parse_report(table(), sources(), [])
        fetch.side_effect = [json.dumps({'access_token': 'access-token-secret'}).encode(), b'']
        app.send_email(config, table(), rows)
        token_request, mail_request = (call.args[0] for call in fetch.call_args_list)
        self.assertEqual(token_request.full_url,
                         'https://login.microsoftonline.com/00000000-0000-0000-0000-000000000001/oauth2/v2.0/token')
        token_body = parse_qs(token_request.data.decode())
        self.assertEqual(token_body['client_id'], ['00000000-0000-0000-0000-000000000002'])
        self.assertEqual(token_body['client_secret'], ['application-secret'])
        self.assertEqual(token_body['scope'], ['https://graph.microsoft.com/.default'])
        self.assertEqual(token_body['grant_type'], ['client_credentials'])
        self.assertEqual(mail_request.full_url, 'https://graph.microsoft.com/v1.0/users/sender%40example.com/sendMail')
        self.assertEqual(mail_request.get_header('Authorization'), 'Bearer access-token-secret')
        payload = json.loads(mail_request.data)
        self.assertEqual(payload['message']['body']['contentType'], 'HTML')
        self.assertEqual(len(payload['message']['toRecipients']), 2)
        self.assertIn('<table ', payload['message']['body']['content'])
        self.assertTrue(payload['saveToSentItems'])

    @patch('news_digest.fetch')
    def test_personal_graph_refresh_token_uses_me_endpoint_without_client_secret(self, fetch):
        os.environ.update(MAIL_PROVIDER='graph', MS_AUTH_MODE='delegated', MS_AUTHORITY='consumers',
                          MS_CLIENT_ID='00000000-0000-0000-0000-000000000002',
                          MS_REFRESH_TOKEN='long-lived-refresh-token', MAIL_TO='receiver@example.com')
        for name in ('MS_TENANT_ID', 'MS_CLIENT_SECRET', 'MAIL_FROM', 'SMTP_HOST', 'SMTP_USERNAME', 'SMTP_PASSWORD'):
            os.environ.pop(name, None)
        config = app.configuration()
        fetch.side_effect = [json.dumps({'access_token': 'delegated-access-token',
                                         'refresh_token': 'rotated-refresh-token'}).encode(), b'']
        app.send_email(config, table(), app.parse_report(table(), sources(), []))
        token_request, mail_request = (call.args[0] for call in fetch.call_args_list)
        self.assertEqual(token_request.full_url,
                         'https://login.microsoftonline.com/consumers/oauth2/v2.0/token')
        token_body = parse_qs(token_request.data.decode())
        self.assertEqual(token_body['grant_type'], ['refresh_token'])
        self.assertEqual(token_body['refresh_token'], ['long-lived-refresh-token'])
        self.assertEqual(token_body['scope'], ['https://graph.microsoft.com/Mail.Send offline_access'])
        self.assertNotIn('client_secret', token_body)
        self.assertEqual(mail_request.full_url, 'https://graph.microsoft.com/v1.0/me/sendMail')
        self.assertEqual(mail_request.get_header('Authorization'), 'Bearer delegated-access-token')


    @patch('news_digest.fetch')
    def test_graph_errors_do_not_expose_application_credentials(self, fetch):
        os.environ.update(MAIL_PROVIDER='graph', MS_TENANT_ID='00000000-0000-0000-0000-000000000001',
                          MS_CLIENT_ID='00000000-0000-0000-0000-000000000002',
                          MS_CLIENT_SECRET='application-secret', MAIL_FROM='sender@example.com')
        config = app.configuration()
        fetch.side_effect = HTTPError('https://login.microsoftonline.com', 401, 'application-secret', {}, None)
        output = io.StringIO()
        with redirect_stderr(output):
            with self.assertRaisesRegex(app.DigestError, 'HTTP 401'):
                app.send_email(config, table(), app.parse_report(table(), sources(), []))
        self.assertNotIn('application-secret', output.getvalue())

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
        for args, should_send in ((['--dry-run'], False), ([], True)):
            with self.subTest(args=args):
                send.reset_mock()
                fetch.return_value = json.dumps(response()).encode()
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(app.main(args), 0)
                self.assertEqual(send.called, should_send)
                self.assertEqual(self.path.exists(), should_send)

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
        self.assertIn('mail delivery', output.getvalue())
        self.assertNotIn('sensitive', output.getvalue())
        self.assertFalse(self.path.exists())

    @patch('news_digest.fetch')
    def test_validation_never_uses_network(self, fetch):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(app.main(['--validate-config']), 0)
        fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
