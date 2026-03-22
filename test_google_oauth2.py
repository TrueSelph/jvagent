import asyncio
import json
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Add project paths to find jvagent
sys.path.append(".")

from jvagent.action.google.google_action import GoogleAction


class MockGoogleAction(GoogleAction):
    API_SERVICE_NAME = "mock_service"
    API_VERSION = "v1"
    SCOPES = ["https://www.googleapis.com/auth/mock"]


class TestGoogleOAuth2Flow(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_client_secrets = {
            "web": {
                "client_id": "test_client_id",
                "client_secret": "test_client_secret",  # pragma: allowlist secret
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }

        self.mock_token_data = {
            "token": "mock_access_token",
            "refresh_token": "mock_refresh_token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "test_client_id",
            "client_secret": "test_client_secret",  # pragma: allowlist secret
            "scopes": ["https://www.googleapis.com/auth/mock"],
        }

        self.action = MockGoogleAction()
        self.action.client_secrets_json = self.mock_client_secrets

    @patch("jvagent.action.google.google_action.Flow.from_client_config")
    async def test_get_authorization_url(self, mock_from_config):
        mock_flow = MagicMock()
        mock_flow.authorization_url.return_value = ("https://mock.auth.url", "state")
        mock_from_config.return_value = mock_flow

        url = await self.action.get_authorization_url()

        self.assertEqual(url, "https://mock.auth.url")
        mock_from_config.assert_called_once_with(
            self.mock_client_secrets,
            scopes=self.action.SCOPES,
            redirect_uri=self.action.redirect_uri,
        )
        mock_flow.authorization_url.assert_called_once_with(prompt="consent")

    @patch("jvagent.action.google.google_action.Flow.from_client_config")
    @patch.object(GoogleAction, "save_file", new_callable=AsyncMock)
    async def test_authorize_and_save_credentials(
        self, mock_save_file, mock_from_config
    ):
        mock_flow = MagicMock()
        mock_creds = MagicMock()
        mock_creds.token = "mock_access_token"
        mock_creds.refresh_token = "mock_refresh_token"
        mock_creds.token_uri = "https://oauth2.googleapis.com/token"
        mock_creds.client_id = "test_client_id"
        mock_creds.client_secret = "test_client_secret"  # pragma: allowlist secret
        mock_creds.scopes = self.action.SCOPES

        mock_flow.credentials = mock_creds
        mock_from_config.return_value = mock_flow

        success = await self.action.authorize("mock_auth_code")
        self.assertTrue(success)

        mock_flow.fetch_token.assert_called_once_with(code="mock_auth_code")

        # Verify save_file was called with the correct token JSON
        mock_save_file.assert_called_once()
        args, kwargs = mock_save_file.call_args
        self.assertEqual(args[0], "token.json")

        saved_data = json.loads(args[1].decode("utf-8"))
        self.assertEqual(saved_data["token"], "mock_access_token")
        self.assertEqual(saved_data["refresh_token"], "mock_refresh_token")

    @patch("jvagent.action.google.google_action.build")
    @patch("jvagent.action.google.google_action.Credentials.from_authorized_user_info")
    @patch.object(GoogleAction, "get_file", new_callable=AsyncMock)
    async def test_get_service_with_cached_token(
        self, mock_get_file, mock_from_info, mock_build
    ):
        # Set up get_file to return our mock token data
        mock_get_file.return_value = json.dumps(self.mock_token_data).encode("utf-8")

        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_from_info.return_value = mock_creds

        await self.action.get_service()

        mock_get_file.assert_called_once_with("token.json")
        mock_from_info.assert_called_once_with(self.mock_token_data, self.action.SCOPES)
        mock_build.assert_called_once_with("mock_service", "v1", credentials=mock_creds)

    @patch.object(GoogleAction, "get_file", new_callable=AsyncMock)
    async def test_get_service_missing_token_raises_error(self, mock_get_file):
        # get_file returns None (no token saved)
        mock_get_file.return_value = None

        with self.assertRaisesRegex(
            ValueError,
            "No valid OAuth2 credentials found. Please call the auth_url endpoint to authorize.",
        ):
            await self.action.get_service()


if __name__ == "__main__":
    unittest.main()
