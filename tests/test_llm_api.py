import json
import os
import unittest
from unittest.mock import patch

from scripts import test_llm_api as smoke
import news_digest as app


class ModelApiSmokeTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            'LLM_API_BASE_URL': 'https://api.example/v1',
            'LLM_API_STYLE': 'responses',
            'LLM_MODEL': 'test-model',
            'LLM_API_KEY': 'test-key',
        }, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_responses_request_is_plain_text_without_search_tool(self):
        response = {'status': 'completed', 'output': [
            {'type': 'message', 'content': [{'type': 'output_text', 'text': '17 加 25 等于 42。'}]},
        ]}
        with patch.object(app, 'fetch', return_value=json.dumps(response).encode()) as fetch:
            self.assertEqual(smoke.run(), 0)
        request = fetch.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, 'https://api.example/v1/responses')
        self.assertEqual(payload['input'], smoke.QUESTION)
        self.assertNotIn('tools', payload)
        self.assertNotIn('tool_choice', payload)

    def test_chat_completions_request_is_plain_text_without_search_options(self):
        os.environ['LLM_API_STYLE'] = 'chat_completions'
        response = {'choices': [{'finish_reason': 'stop', 'message': {'content': '答案是 42。'}}]}
        with patch.object(app, 'fetch', return_value=json.dumps(response).encode()) as fetch:
            self.assertEqual(smoke.run(), 0)
        request = fetch.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, 'https://api.example/v1/chat/completions')
        self.assertNotIn('web_search_options', payload)

    def test_missing_credentials_fail_without_sending_request(self):
        del os.environ['LLM_API_KEY']
        with patch.object(app, 'fetch') as fetch:
            with self.assertRaisesRegex(app.DigestError, 'LLM_API_KEY'):
                smoke.run()
        fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
