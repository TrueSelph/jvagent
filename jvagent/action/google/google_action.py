import json
import logging
from typing import Any, ClassVar, Dict, List, Optional, Union

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from jvspatial.core.annotations import attribute
from jvspatial.env import env

from jvagent.action.base import Action
from jvagent.action.oauth.audit import _audit_log_oauth_event

logger = logging.getLogger(__name__)

_OIDC_SCOPES = frozenset({"openid", "profile", "email"})


class GoogleAction(Action):
    """Base class for Google Workspace actions. Login is MCP OAuth (MCPOAuthToken)."""

    auth_url: str = attribute(
        default="",
        description="MCP Google Workspace login URL (set on startup).",
    )

    _built_service: Optional[Any] = None

    API_SERVICE_NAME: ClassVar[str] = ""
    API_VERSION: ClassVar[str] = ""
    SCOPES: ClassVar[List[str]] = []

    _MCP_SERVER: ClassVar[str] = "google_workspace"
    _MCP_ACCOUNT: ClassVar[str] = "integral"
    _MCP_SERVICE: ClassVar[str] = ""

    async def _apply_env_defaults(self) -> None:
        """Point auth_url at MCP OAuth (`/api/mcp/google_workspace/auth`)."""
        from jvagent.action.mcp_oauth.hydrate import mcp_google_workspace_auth_url

        self.auth_url = mcp_google_workspace_auth_url(
            self._MCP_ACCOUNT, service=self._MCP_SERVICE or None
        )
        await self.save()

    async def on_register(self) -> None:
        await self._apply_env_defaults()

    async def on_reload(self) -> None:
        await self._apply_env_defaults()

    async def on_startup(self) -> None:
        await self._apply_env_defaults()

    def _clear_cached_services(self) -> None:
        self._built_service = None

    def _mcp_auth_url(self) -> str:
        from jvagent.action.mcp_oauth.hydrate import mcp_google_workspace_auth_url

        return mcp_google_workspace_auth_url(
            self._MCP_ACCOUNT, service=self._MCP_SERVICE or None
        )

    async def get_service(self):
        """Build and return an authenticated Google API service object with caching."""
        if not self.API_SERVICE_NAME or not self.API_VERSION:
            raise ValueError(
                f"{self.__class__.__name__} must define API_SERVICE_NAME and API_VERSION"
            )

        if hasattr(self, "_built_service") and self._built_service:
            if self._built_service._http.credentials.valid:
                return self._built_service
            logger.info("Cached service credentials expired. Rebuilding...")

        try:
            creds = await self._get_credentials()
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

    def _raw_client_secrets(self) -> Union[str, Dict[str, Any], Any]:
        """``GOOGLE_CLIENT_SECRETS_JSON`` env: path to client secrets file or raw JSON string."""
        return env("GOOGLE_CLIENT_SECRETS_JSON") or ""

    def _resolve_client_secret_from_env(self) -> str:
        """Re-read ``client_secret`` from ``GOOGLE_CLIENT_SECRETS_JSON`` each refresh."""
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
        if isinstance(client_config, dict):
            for outer in ("web", "installed"):
                inner = client_config.get(outer)
                if isinstance(inner, dict) and inner.get("client_secret"):
                    return str(inner["client_secret"])
            if client_config.get("client_secret"):
                return str(client_config["client_secret"])
        return ""

    async def _mcp_oauth_action(self) -> Any:
        from jvagent.action.mcp_oauth.mcp_oauth_action import MCPOAuthAction

        return await self.get_action(MCPOAuthAction)

    async def _load_mcp_token(
        self,
    ) -> tuple[Optional[str], Optional[Dict[str, Any]], Any]:
        """Return ``(account_name, token_dict, node)`` for this action's MCP service."""
        from jvagent.action.mcp_oauth.mcp_oauth_action import (
            service_token_payload,
            token_row_for_service,
        )

        oauth = await self._mcp_oauth_action()
        if oauth is None:
            return None, None, None
        rows = await oauth.list_oauth_tokens(self._MCP_SERVER)
        if not rows:
            token = await oauth.get_oauth_token(self._MCP_SERVER, self._MCP_ACCOUNT)
            if token and self._MCP_SERVICE:
                token = service_token_payload(
                    token, self._MCP_SERVICE, self._MCP_SERVER
                )
            return (self._MCP_ACCOUNT, token, None) if token else (None, None, None)
        return token_row_for_service(
            rows, self._MCP_SERVER, self._MCP_SERVICE, self._MCP_ACCOUNT
        )

    def _scopes_for_refresh(self, token_data: Optional[Dict[str, Any]]) -> List[str]:
        """This action's granted API scopes only (no OIDC / extra APIs)."""
        raw = (token_data or {}).get("scopes")
        granted: List[str] = []
        if isinstance(raw, str) and raw.strip():
            granted = raw.split()
        elif isinstance(raw, list):
            granted = [str(s).strip() for s in raw if str(s).strip()]
        if not granted:
            return []
        api = {
            s
            for s in granted
            if s.startswith("https://www.googleapis.com/auth/")
            and s not in _OIDC_SCOPES
        }
        wanted = [s for s in (self.SCOPES or []) if s]
        if wanted:
            return [s for s in wanted if s in api]
        return [s for s in granted if s in api]

    def _credentials_from_mcp_payload(self, token_data: Dict[str, Any]) -> Any:
        env_secret = self._resolve_client_secret_from_env()
        scopes = self._scopes_for_refresh(token_data)
        token_info = {
            "token": token_data.get("token") or "",
            "refresh_token": token_data.get("refresh_token") or "",
            "token_uri": token_data.get("token_uri")
            or "https://oauth2.googleapis.com/token",
            "client_id": token_data.get("client_id") or "",
            "client_secret": env_secret or token_data.get("client_secret") or "",
            "scopes": scopes,
        }
        expiry = token_data.get("expiry")
        if expiry:
            token_info["expiry"] = expiry
        return Credentials.from_authorized_user_info(token_info, scopes)

    async def _get_credentials(self) -> Any:
        """Load Google credentials from MCPOAuthToken (not GoogleToken)."""
        account_name, token_data, _node = await self._load_mcp_token()
        label = self.__class__.__name__
        auth = self._mcp_auth_url()
        if not token_data or not token_data.get("refresh_token"):
            raise ValueError(
                f"No valid MCP OAuth credentials for {label}. Authorize at {auth}"
            )

        refresh_scopes = self._scopes_for_refresh(token_data)
        creds = self._credentials_from_mcp_payload(token_data)
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token and refresh_scopes:
            logger.info(
                "Refreshing expired MCP OAuth credentials for %s %s.",
                label,
                self.id,
            )
            try:
                creds.refresh(Request())
                await self._save_credentials(creds)
                self._clear_cached_services()
            except Exception as e:
                logger.error(
                    "Failed to refresh MCP OAuth credentials for %s: %s",
                    self.id,
                    e,
                )
                _audit_log_oauth_event(
                    provider="mcp_google",
                    event="token_refresh_failed",
                    action_id=self.id,
                    agent_id=self.agent_id,
                    client_id_hint=getattr(creds, "client_id", None),
                    extra_details={"error_type": type(e).__name__},
                )
                raise ValueError(
                    f"OAuth2 credentials for {label} expired and could not "
                    f"be refreshed. Re-authorize at {auth}"
                ) from e
            return creds
        raise ValueError(
            f"No valid MCP OAuth credentials for {label}. Authorize at {auth}"
        )

    async def _load_credentials(self) -> Any:
        """Alias for Docs and other callers that still use this name."""
        return await self._get_credentials()

    async def _save_credentials(self, creds: Any) -> None:
        """Persist refreshed tokens on this service's MCP blob and re-hydrate."""
        oauth = await self._mcp_oauth_action()
        if oauth is None:
            logger.warning(
                "MCPOAuthAction not found; cannot save refreshed %s credentials",
                self.__class__.__name__,
            )
            return

        account_name, token_data, _node = await self._load_mcp_token()
        payload: Dict[str, Any] = dict(token_data or {})
        payload["type"] = "authorized_user"
        payload["token"] = creds.token or ""
        payload["refresh_token"] = creds.refresh_token or payload.get("refresh_token")
        payload["client_id"] = creds.client_id or payload.get("client_id") or ""
        payload["token_uri"] = creds.token_uri or "https://oauth2.googleapis.com/token"
        if getattr(creds, "scopes", None):
            payload["scopes"] = list(creds.scopes)
        else:
            refresh_scopes = self._scopes_for_refresh(payload)
            if refresh_scopes:
                payload["scopes"] = refresh_scopes
        if getattr(creds, "expiry", None):
            payload["expiry"] = creds.expiry.isoformat()

        store_account = account_name or self._MCP_ACCOUNT
        await oauth.save_oauth_token_for_service(
            self._MCP_SERVER,
            store_account,
            payload,
            service=self._MCP_SERVICE or None,
        )

        _audit_log_oauth_event(
            provider="mcp_google",
            event="token_saved",
            action_id=self.id,
            agent_id=self.agent_id,
            client_id_hint=creds.client_id,
        )
        logger.info(
            "Saved MCP OAuth credentials for %s %s",
            self.__class__.__name__,
            self.id,
        )
