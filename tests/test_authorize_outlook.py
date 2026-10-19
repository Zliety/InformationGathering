import unittest
from unittest.mock import patch

from scripts import authorize_outlook as authorize


class OutlookAuthorizationTests(unittest.TestCase):
    @patch('scripts.authorize_outlook.time.sleep')
    @patch('scripts.authorize_outlook.post_form')
    def test_device_flow_returns_refresh_token_after_user_consent(self, post_form, sleep):
        post_form.side_effect = [
            {'device_code': 'device-secret', 'user_code': 'ABCD-EFGH',
             'verification_uri': 'https://microsoft.com/devicelogin', 'expires_in': 60, 'interval': 1},
            {'error': 'authorization_pending'},
            {'access_token': 'access-token', 'refresh_token': 'refresh-token'},
        ]
        token = authorize.authorize('00000000-0000-0000-0000-000000000002')
        self.assertEqual(token, 'refresh-token')
        self.assertEqual(post_form.call_count, 3)
        device_url, device_body = post_form.call_args_list[0].args
        self.assertEqual(device_url, 'https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode')
        self.assertEqual(device_body['scope'], authorize.SCOPE)
        token_url, token_body = post_form.call_args_list[1].args
        self.assertEqual(token_url, 'https://login.microsoftonline.com/consumers/oauth2/v2.0/token')
        self.assertEqual(token_body['device_code'], 'device-secret')
        sleep.assert_called()

    def test_device_flow_rejects_invalid_client_id_and_authority(self):
        with self.assertRaisesRegex(RuntimeError, 'UUID format'):
            authorize.authorize('not-a-client-id')
        with self.assertRaisesRegex(RuntimeError, 'consumers'):
            authorize.authorize('00000000-0000-0000-0000-000000000002', 'common')

    @patch('scripts.authorize_outlook.post_form')
    def test_device_flow_reports_denial_without_exposing_tokens(self, post_form):
        post_form.side_effect = [
            {'device_code': 'device-secret', 'user_code': 'ABCD-EFGH',
             'verification_uri': 'https://microsoft.com/devicelogin', 'expires_in': 60, 'interval': 1},
            {'error': 'access_denied'},
        ]
        with self.assertRaisesRegex(RuntimeError, 'access_denied'):
            authorize.authorize('00000000-0000-0000-0000-000000000002')


if __name__ == '__main__':
    unittest.main()
