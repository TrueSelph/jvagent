import json
import logging
from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional, Union

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.core.public_url import get_public_base_url

from .google_token import GoogleToken

logger = logging.getLogger(__name__)


class GoogleAction(Action):
    """Base class for Google actions using OAuth2 authentication."""

    client_secrets_json: Union[str, Dict[str, Any]] = attribute(
        default="", description="Client secrets JSON (string or object) for OAuth2"
    )
    redirect_uri: str = attribute(
        default="http://localhost:8080/",
        description="The redirect URI used in the OAuth2 flow.",
    )
    auth_url: str = attribute(
        default="", description="The authorization URL for the user to visit."
    )

    _built_service: Optional[Any] = None

    # These must be overridden by subclasses
    API_SERVICE_NAME: ClassVar[str] = ""
    API_VERSION: ClassVar[str] = ""
    SCOPES: ClassVar[List[str]] = []

    async def _apply_env_defaults(self) -> None:
        base = get_public_base_url()
        self.auth_url = base + f"/api/google/{self.id}"
        self.redirect_uri = base + f"/api/google/callback/"
        await self.save()

        self._warn_if_oauth_unusable()

    def _warn_if_oauth_unusable(self) -> None:
        """Best-effort OAuth client check for lifecycle hooks; logs warning only."""
        try:
            self._create_flow()
        except Exception as e:
            logger.warning(
                "Google action %s (%s): OAuth client is not ready — %s",
                self.id,
                self.__class__.__name__,
                e,
            )

    async def on_register(self) -> None:
        """Called when action is registered. Validates configuration."""
        await self._apply_env_defaults()

    async def on_reload(self) -> None:
        """Called when action is reloaded. Re-registers session with current webhook URL."""
        await self._apply_env_defaults()

    async def on_startup(self) -> None:
        """Initialize filter and adapter, attempt session registration with configurable timeout."""
        await self._apply_env_defaults()

    async def get_service(self):
        """Build and return an authenticated Google API service object with caching."""
        if not self.API_SERVICE_NAME or not self.API_VERSION:
            raise ValueError(
                f"{self.__class__.__name__} must define API_SERVICE_NAME and API_VERSION"
            )

        # 1. Check if we already have a built service in memory
        if hasattr(self, "_built_service") and self._built_service:
            # Check if the credentials inside the service are still valid
            # This is a local check, no network call.
            if self._built_service._http.credentials.valid:
                return self._built_service
            else:
                logger.info("Cached service credentials expired. Rebuilding...")

        try:
            # 2. Get fresh/refreshed credentials from DB
            creds = await self._get_credentials()

            # 3. Build the actual service object
            logger.warning(
                f"Building Google {self.API_SERVICE_NAME} service for {self.id}"
            )
            self._built_service = build(
                self.API_SERVICE_NAME,
                self.API_VERSION,
                credentials=creds,
                static_discovery=False,
            )

            return self._built_service

        except Exception as e:
            logger.error(
                f"Error building Google {self.API_SERVICE_NAME} service: {e}",
                exc_info=True,
            )
            self._built_service = None
            raise

    async def get_authorization_url(self, code_verifier: Optional[str] = None) -> str:
        """Returns the OAuth2 authorization URL for the user to visit."""
        flow = self._create_flow()

        if code_verifier:
            flow.code_verifier = code_verifier

        # Encode action_id and potentially code_verifier into state
        # format: action_id:code_verifier (or just action_id if verifier is handled elsewhere)
        state = self.id
        if code_verifier:
            state = f"{self.id}:{code_verifier}"

        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            state=state,
        )
        return auth_url

    async def authorize(self, code: str, code_verifier: Optional[str] = None) -> bool:
        """Exchanges the authorization code for credentials and saves them."""
        flow = self._create_flow()
        if code_verifier:
            flow.code_verifier = code_verifier
        flow.fetch_token(code=code)
        creds = flow.credentials
        await self._save_credentials(creds)
        return True

    def _create_flow(self) -> Flow:
        """Creates the OAuth2 flow object from client secrets."""
        if not self.client_secrets_json:
            raise ValueError("client_secrets_json is required for OAuth2 flow.")

        client_config = None
        if isinstance(self.client_secrets_json, str):
            try:
                client_config = json.loads(self.client_secrets_json)
            except json.JSONDecodeError:
                # Assume it's a file path
                with open(self.client_secrets_json, "r") as f:
                    client_config = json.load(f)
        else:
            client_config = self.client_secrets_json

        # Check for redirect_uris in the config (Google web secrets)
        if "web" in client_config and "redirect_uris" in client_config["web"]:
            if self.redirect_uri not in client_config["web"]["redirect_uris"]:
                raise ValueError(
                    f"redirect_uri is not in the client config\nclient_config:\n{client_config['web']['redirect_uris']}\n\nredirect_uri:\n{self.redirect_uri}\n\nUPDATE IN GOOGLE CONSOLE: https://console.cloud.google.com/apis/credentials"
                )
        elif (
            "installed" in client_config
            and "redirect_uris" in client_config["installed"]
        ):
            if self.redirect_uri not in client_config["installed"]["redirect_uris"]:
                raise ValueError(
                    f"redirect_uri is not in the client config\nclient_config:\n{client_config['installed']['redirect_uris']}\n\nredirect_uri:\n{self.redirect_uri}\n\nUPDATE IN GOOGLE CONSOLE: https://console.cloud.google.com/apis/credentials"
                )

        return Flow.from_client_config(
            client_config, scopes=self.SCOPES, redirect_uri=self.redirect_uri
        )

    async def _get_credentials(self) -> Credentials:
        """Retrieves and refreshes cached credentials from database, or raises an error if missing."""
        creds = None

        # Retrieve token node from database
        token_node = await self.node(node="GoogleToken", action_id=self.id)

        if token_node:
            try:
                token_info = {
                    "token": token_node.token,
                    "refresh_token": token_node.refresh_token,
                    "token_uri": token_node.token_uri,
                    "client_id": token_node.client_id,
                    "client_secret": token_node.client_secret,
                    "scopes": token_node.scopes,
                    "expiry": (
                        token_node.expiry.isoformat()
                        if isinstance(token_node.expiry, datetime)
                        else token_node.expiry
                    ),
                }
                creds = Credentials.from_authorized_user_info(token_info, self.SCOPES)
            except Exception as e:
                logger.warning(f"Failed to load cached credentials for {self.id}: {e}")

        # If there are no (valid) credentials available, let the user log in.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info(
                    f"Refreshing expired Google OAuth2 credentials for {self.id}."
                )
                try:
                    creds.refresh(Request())

                    await self._save_credentials(creds)
                except Exception as e:
                    logger.error(f"Failed to refresh credentials for {self.id}: {e}")
                    raise ValueError(
                        f"OAuth2 credentials for {self.id} expired and could not be refreshed. Please re-authorize."
                    )
            else:
                raise ValueError(
                    f"No valid OAuth2 credentials found for {self.id}. Please call the auth_url endpoint to authorize."
                )

        return creds

    async def _save_credentials(self, creds: Credentials) -> None:
        """Saves the credentials to database as a GoogleToken node."""
        token_data = {
            "action_id": self.id,
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": creds.scopes,
            "agent_id": self.agent_id,
            "expiry": creds.expiry.isoformat() if creds.expiry else None,
        }

        # Update existing token or create new one
        token_node = await self.node(node="GoogleToken", action_id=self.id)
        if token_node:
            token_node.token = creds.token
            token_node.refresh_token = creds.refresh_token
            token_node.token_uri = creds.token_uri
            token_node.client_id = creds.client_id
            token_node.client_secret = creds.client_secret
            token_node.scopes = creds.scopes
            token_node.expiry = creds.expiry.isoformat() if creds.expiry else None
            await token_node.save()
        else:
            token_node = await GoogleToken.create(**token_data)
            # Establish google_action >> token relationship
            await self.connect(token_node)

        logger.info(f"Saved Google credentials for {self.id} to database")
