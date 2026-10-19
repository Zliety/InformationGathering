import json
import os
import unittest
from unittest.mock import patch

from scripts import test_web_search as smoke
import news_digest as app


class WebSearchSmokeTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            'LLM_API_BASE_URL': 'https://api.example/v1',
            'LLM_API_STYLE': 'responses',
            'LLM_SEARCH_TOOL': 'web_search',
            'LLM_MODEL': 'test-model',
            'LLM_API_KEY': 'test-key',
        }, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_responses_search_request_requires_tool_and_citations(self):
        response = {'status': 'completed', 'output': [
            {'type': 'web_search_call', 'status': 'completed',
             'action': {'sources': [{'url': 'https://example.com/source'}]}},
            {'type': 'message', 'content': [{'type': 'output_text', 'text': '这是微软官方网站。'}]},
        ]}
        with patch.object(app, 'fetch', return_value=json.dumps(response).encode()) as fetch:
            self.assertEqual(smoke.run(), 0)
        request = fetch.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, 'https://api.example/v1/responses')
        self.assertEqual(payload['tools'], [{'type': 'web_search'}])
        self.assertEqual(payload['tool_choice'], 'required')
        self.assertEqual(payload['include'], ['web_search_call.action.sources'])

    def test_responses_without_completed_search_fails(self):
        response = {'status': 'completed', 'output': [
            {'type': 'message', 'content': [{'type': 'output_text', 'text': '这是微软官方网站。'}]},
        ]}
        with patch.object(app, 'fetch', return_value=json.dumps(response).encode()):
            with self.assertRaisesRegex(app.DigestError, 'no completed web search'):
                smoke.run()

    def test_chat_search_uses_provider_specific_body_and_requires_sources(self):
        os.environ.update(LLM_API_STYLE='chat_completions', LLM_EXTRA_BODY='{"web_search_options":{}}')
        response = {'choices': [{'finish_reason': 'stop', 'message': {
            'content': '这是微软官方网站。',
            'annotations': [{'url_citation': {'url': 'https://example.com/source'}}],
        }}]}
        with patch.object(app, 'fetch', return_value=json.dumps(response).encode()) as fetch:
            self.assertEqual(smoke.run(), 0)
        request = fetch.call_args.args[0]
        payload = json.loads(request.data)
        self.assertIn('web_search_options', payload)
        self.assertEqual(request.full_url, 'https://api.example/v1/chat/completions')


if __name__ == '__main__':
    unittest.main()
