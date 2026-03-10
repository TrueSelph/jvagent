import json
import logging
from typing import Any, ClassVar, Dict, List, Optional, Union

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from jvspatial.core.annotations import attribute
from jvagent.action.base import Action

logger = logging.getLogger(__name__)

class GoogleAction(Action):
    """Base class for Google actions using OAuth2 authentication."""

    client_secrets_json: Union[str, Dict[str, Any]] = attribute(
        default="", description="Client secrets JSON (string or object) for OAuth2"
    )
    redirect_uri: str = attribute(
        default="http://localhost:8080/",
        description="The redirect URI used in the OAuth2 flow."
    )

    # These must be overridden by subclasses
    API_SERVICE_NAME: ClassVar[str] = ""
    API_VERSION: ClassVar[str] = ""
    SCOPES: ClassVar[List[str]] = []
    
    _TOKEN_FILE: ClassVar[str] = "token.json"

    async def get_service(self):
        """Build and return an authenticated Google API service object."""
        if not self.API_SERVICE_NAME or not self.API_VERSION:
            raise ValueError(f"{self.__class__.__name__} must define API_SERVICE_NAME and API_VERSION")

        try:
            creds = await self._get_credentials()
            return build(self.API_SERVICE_NAME, self.API_VERSION, credentials=creds)
        except Exception as e:
            logger.error(f"Error building Google {self.API_SERVICE_NAME} service: {e}", exc_info=True)
            raise

    async def get_authorization_url(self) -> str:
        """Returns the OAuth2 authorization URL for the user to visit."""
        flow = self._create_flow()
        auth_url, _ = flow.authorization_url(prompt='consent')
        return auth_url

    async def authorize(self, code: str) -> bool:
        """Exchanges the authorization code for credentials and saves them."""
        flow = self._create_flow()
        flow.fetch_token(code=code)
        creds = flow.credentials
        await self._save_credentials(creds)
        return True

    def _create_flow(self) -> Flow:
        """Creates the OAuth2 flow object from client secrets."""
        if not self.client_secrets_json:
            raise ValueError("client_secrets_json is required for OAuth2 flow.")
            
        if isinstance(self.client_secrets_json, str):
            try:
                client_config = json.loads(self.client_secrets_json)
            except json.JSONDecodeError:
                # Assume it's a file path
                return Flow.from_client_secrets_file(
                    self.client_secrets_json,
                    scopes=self.SCOPES,
                    redirect_uri=self.redirect_uri
                )
        else:
            client_config = self.client_secrets_json
            
        return Flow.from_client_config(
            client_config,
            scopes=self.SCOPES,
            redirect_uri=self.redirect_uri
        )

    async def _get_credentials(self) -> Credentials:
        """Retrieves and refreshes cached credentials, or raises an error if missing."""
        creds = None
        
        # Try to load existing token
        token_data_bytes = await self.get_file(self._TOKEN_FILE)
        if token_data_bytes:
            try:
                token_info = json.loads(token_data_bytes.decode('utf-8'))
                creds = Credentials.from_authorized_user_info(token_info, self.SCOPES)
            except Exception as e:
                logger.warning(f"Failed to load cached credentials: {e}")

        # If there are no (valid) credentials available, let the user log in.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Refreshing expired Google OAuth2 credentials.")
                try:
                    creds.refresh(Request())
                    await self._save_credentials(creds)
                except Exception as e:
                    logger.error(f"Failed to refresh credentials: {e}")
                    raise ValueError("OAuth2 credentials expired and could not be refreshed. Please re-authorize.")
            else:
                raise ValueError("No valid OAuth2 credentials found. Please call the auth_url endpoint to authorize.")
                
        return creds

    async def _save_credentials(self, creds: Credentials) -> None:
        """Saves the credentials to file storage."""
        token_info = {
            'token': creds.token,
            'refresh_token': creds.refresh_token,
            'token_uri': creds.token_uri,
            'client_id': creds.client_id,
            'client_secret': creds.client_secret,
            'scopes': creds.scopes
        }
        await self.save_file(self._TOKEN_FILE, json.dumps(token_info).encode('utf-8'))
