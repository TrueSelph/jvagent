import json
import logging
import os
from contextlib import contextmanager
from typing import Any, ClassVar, Dict, List, Optional, Union

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from jvspatial.core.annotations import attribute
from jvspatial.env import env

from jvagent.action.base import Action
from jvagent.action.utils.oauth_audit import _audit_log_oauth_event
from jvagent.action.utils.oauth_token_crypto import (
    decrypt_token_from_storage,
    encrypt_token_for_storage,
)
from jvagent.core.public_url import get_public_base_url

from .google_token import GoogleToken

logger = logging.getLogger(__name__)


@contextmanager
def _oauthlib_relax_token_scope():
    """Allow token responses whose scope is a superset of the Flow request.

    Google may return merged scopes when ``include_granted_scopes`` is used or
    when the user has prior grants for the same OAuth client; oauthlib would
    otherwise raise ``Warning: Scope has changed...`` during ``fetch_token``.
    """
    key = "OAUTHLIB_RELAX_TOKEN_SCOPE"
    previous = os.environ.get(key)
    os.environ[key] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


class GoogleAction(Action):
    """Base class for Google actions using OAuth2 authentication."""

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

    # AUDIT-actions XC-4: ``/api/google/{action_id}`` is the per-action
    # auth-URL entry point. ``/api/google/callback/`` is intentionally
    # SHARED across every GoogleAction instance and is therefore NOT
    # declared here — unregistering it on one action's deregister would
    # break OAuth callbacks for all the others.
    additional_endpoint_path_templates: ClassVar[List[str]] = [
        "/api/google/{action_id}",
    ]

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
            logger.debug(
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
        """Return the OAuth2 authorization URL for the user to visit.

        The ``state`` parameter is an opaque CSRF token generated by
        :func:`jvagent.action.utils.oauth_state.create_oauth_state`. The
        ``code_verifier`` is persisted alongside it server-side and looked up
        at callback time — it is NEVER sent to the IdP or echoed back via
        the user's browser. AUDIT-actions XC-2.
        """
        from jvagent.action.utils.oauth_state import create_oauth_state

        flow = self._create_flow()
        if code_verifier:
            flow.code_verifier = code_verifier

        state_token = await create_oauth_state(
            action_id=self.id,
            provider="google",
            code_verifier=code_verifier or "",
            redirect_uri=self.redirect_uri,
        )

        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            state=state_token,
        )
        return auth_url

    async def authorize(self, code: str, code_verifier: Optional[str] = None) -> bool:
        """Exchanges the authorization code for credentials and saves them."""
        flow = self._create_flow()
        if code_verifier:
            flow.code_verifier = code_verifier
        with _oauthlib_relax_token_scope():
            flow.fetch_token(code=code)
        creds = flow.credentials
        await self._save_credentials(creds)
        return True

    def _raw_client_secrets(self) -> Union[str, Dict[str, Any], Any]:
        """``GOOGLE_CLIENT_SECRETS_JSON`` env: path to client secrets file or raw JSON string."""
        return env("GOOGLE_CLIENT_SECRETS_JSON") or ""

    def _create_flow(self) -> Flow:
        """Creates the OAuth2 flow object from client secrets."""
        raw = self._raw_client_secrets()
        if not raw:
            raise ValueError(
                "OAuth client config required: set GOOGLE_CLIENT_SECRETS_JSON (path or JSON string)."
            )

        client_config = None
        if isinstance(raw, str):
            try:
                client_config = json.loads(raw)
            except json.JSONDecodeError:
                # Assume it's a file path
                with open(raw, "r") as f:
                    client_config = json.load(f)
        else:
            client_config = raw

        return Flow.from_client_config(
            client_config, scopes=self.SCOPES, redirect_uri=self.redirect_uri
        )

    def _resolve_client_secret_from_env(self) -> str:
        """Re-read ``client_secret`` from ``GOOGLE_CLIENT_SECRETS_JSON`` each refresh.

        AUDIT-actions XC-1: the secret is app-wide, not per-user/per-action.
        Storing it on every ``GoogleToken`` row multiplied the leak surface;
        env is the single source of truth.
        """
        raw = self._raw_client_secrets()
        if not raw:
            return ""
        client_config: Any = None
        try:
            if isinstance(raw, str):
                try:
                    client_config = json.loads(raw)
                except json.JSONDecodeError:
                    with open(raw, "r") as f:
                        client_config = json.load(f)
            else:
                client_config = raw
        except Exception as exc:
            logger.warning(
                "GoogleAction %s: failed to parse client secrets for refresh: %s",
                self.id,
                exc,
            )
            return ""
        # client_config can be {"web": {...}} or {"installed": {...}}.
        if isinstance(client_config, dict):
            for outer in ("web", "installed"):
                inner = client_config.get(outer)
                if isinstance(inner, dict) and inner.get("client_secret"):
                    return str(inner["client_secret"])
            if client_config.get("client_secret"):
                return str(client_config["client_secret"])
        return ""

    async def _get_credentials(self) -> Credentials:
        """Retrieves and refreshes cached credentials from database, or raises an error if missing."""
        creds = None

        # Retrieve token node from database
        token_node = await self.node(node="GoogleToken", action_id=self.id)

        if token_node:
            try:
                # AUDIT-actions XC-1: prefer env-sourced client_secret over
                # the persisted row field. Old rows may still carry a
                # value; new rows must not be written with one.
                env_secret = self._resolve_client_secret_from_env()
                # Decrypt token + refresh_token from storage (AES-GCM
                # via JVAGENT_TOKEN_ENC_KEY). Legacy plaintext rows are
                # passed through unchanged; next save re-encrypts.
                decrypted_token = decrypt_token_from_storage(token_node.token)
                decrypted_refresh = decrypt_token_from_storage(token_node.refresh_token)
                token_info = {
                    "token": decrypted_token,
                    "refresh_token": decrypted_refresh,
                    "token_uri": token_node.token_uri,
                    "client_id": token_node.client_id,
                    "client_secret": env_secret
                    or getattr(token_node, "client_secret", ""),
                    "scopes": token_node.scopes,
                    "expiry": (
                        token_node.expiry.isoformat() if token_node.expiry else None
                    ),
                }
                creds = Credentials.from_authorized_user_info(token_info, self.SCOPES)
            except Exception as e:
                logger.warning(f"Failed to load cached credentials for {self.id}: {e}")
                _audit_log_oauth_event(
                    provider="google",
                    event="token_load_failed",
                    action_id=self.id,
                    agent_id=self.agent_id,
                    extra_details={"error_type": type(e).__name__},
                )

        # If there are no (valid) credentials available, let the user log in.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info(
                    f"Refreshing expired Google OAuth2 credentials for {self.id}."
                )
                try:
                    creds.refresh(Request())

                    await self._save_credentials(creds)
                    # AUDIT-actions XC-21: invalidate the cached service
                    # so subsequent calls rebuild with the refreshed
                    # credentials. Previously a cached service kept
                    # using the about-to-expire access token.
                    self._built_service = None
                except Exception as e:
                    logger.error(f"Failed to refresh credentials for {self.id}: {e}")
                    _audit_log_oauth_event(
                        provider="google",
                        event="token_refresh_failed",
                        action_id=self.id,
                        agent_id=self.agent_id,
                        client_id_hint=getattr(creds, "client_id", None),
                        extra_details={"error_type": type(e).__name__},
                    )
                    raise ValueError(
                        f"OAuth2 credentials for {self.id} expired and could not be refreshed. Please re-authorize."
                    )
            else:
                raise ValueError(
                    f"No valid OAuth2 credentials found for {self.id}. Please call the auth_url endpoint to authorize."
                )

        return creds

    async def _save_credentials(self, creds: Credentials) -> None:
        """Saves the credentials to database as a GoogleToken node.

        AUDIT-actions XC-1: does NOT persist ``client_secret`` — the app-wide
        secret is re-read from ``GOOGLE_CLIENT_SECRETS_JSON`` env at every
        refresh via :meth:`_resolve_client_secret_from_env`. If an existing
        row carries a leftover ``client_secret`` value, it is scrubbed on
        the next save so the field stops being a leak target. Token +
        refresh_token still persist (they are per-user; encryption layer
        in subsequent work).
        """
        # Encrypt token + refresh_token before persisting. No-op when
        # JVAGENT_TOKEN_ENC_KEY is unset (legacy plaintext storage).
        # AUDIT-actions XC-1 Fix 2.
        enc_token = encrypt_token_for_storage(creds.token or "")
        enc_refresh = encrypt_token_for_storage(creds.refresh_token or "")
        token_data = {
            "action_id": self.id,
            "token": enc_token,
            "refresh_token": enc_refresh,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            # client_secret intentionally omitted — AUDIT XC-1.
            "client_secret": "",
            "scopes": creds.scopes,
            "agent_id": self.agent_id,
            "expiry": creds.expiry,
        }

        # Update existing token or create new one
        token_node = await self.node(node="GoogleToken", action_id=self.id)
        if token_node:
            token_node.token = enc_token
            token_node.refresh_token = enc_refresh
            token_node.token_uri = creds.token_uri
            token_node.client_id = creds.client_id
            # Scrub any legacy client_secret on existing row.
            token_node.client_secret = ""
            token_node.scopes = creds.scopes
            token_node.expiry = creds.expiry
            await token_node.save()
        else:
            token_node = await GoogleToken.create(**token_data)
            # Establish google_action >> token relationship
            await self.connect(token_node)

        # AUDIT-actions XC-1 Fix 3: audit-log every token mint / refresh.
        _audit_log_oauth_event(
            provider="google",
            event="token_saved",
            action_id=self.id,
            agent_id=self.agent_id,
            client_id_hint=creds.client_id,
        )
        logger.info(f"Saved Google credentials for {self.id} to database")
