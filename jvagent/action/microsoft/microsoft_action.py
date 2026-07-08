import base64
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import httpx
from jvspatial.core.annotations import attribute
from jvspatial.env import env

from jvagent.action.base import Action
from jvagent.action.oauth.audit import _audit_log_oauth_event
from jvagent.action.oauth.token_crypto import (
    decrypt_token_from_storage,
    encrypt_token_for_storage,
)
from jvagent.core.public_url import get_public_base_url

from .microsoft_token import MicrosoftToken

logger = logging.getLogger(__name__)

GRAPH_V1 = "https://graph.microsoft.com/v1.0"


def pkce_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class MicrosoftAction(Action):
    """Base class for Microsoft 365 actions using OAuth2 (Entra ID) + MS Graph."""

    redirect_uri: str = attribute(
        default="http://localhost:8080/",
        description="Redirect URI registered for this app in Entra ID.",
    )
    auth_url: str = attribute(
        default="",
        description="Authorization landing URL for this action (public /api/microsoft/{id}).",
    )

    SCOPES: ClassVar[List[str]] = []

    # AUDIT-actions XC-4: per-action auth URL entry; ``/api/microsoft/callback/``
    # is intentionally SHARED across all MicrosoftAction instances and NOT
    # declared here.
    additional_endpoint_path_templates: ClassVar[List[str]] = [
        "/api/microsoft/{action_id}",
    ]

    def _tenant_id(self) -> str:
        return (env("MICROSOFT_TENANT_ID") or "common").strip()

    def _client_id(self) -> str:
        cid = env("MICROSOFT_CLIENT_ID") or ""
        return str(cid).strip()

    def _client_secret(self) -> str:
        return str(env("MICROSOFT_CLIENT_SECRET") or "").strip()

    def _token_url(self) -> str:
        t = self._tenant_id()
        return f"https://login.microsoftonline.com/{t}/oauth2/v2.0/token"

    def _authorize_url(self) -> str:
        t = self._tenant_id()
        return f"https://login.microsoftonline.com/{t}/oauth2/v2.0/authorize"

    async def _apply_env_defaults(self) -> None:
        base = get_public_base_url()
        self.auth_url = base + f"/api/microsoft/{self.id}"
        self.redirect_uri = base + r"/api/microsoft/callback/"
        await self.save()
        self._warn_if_oauth_unusable()

    def _warn_if_oauth_unusable(self) -> None:
        try:
            self._require_client_config()
        except Exception as e:
            logger.warning(
                "Microsoft action %s (%s): OAuth client is not ready — %s",
                self.id,
                self.__class__.__name__,
                e,
            )

    def _require_client_config(self) -> Tuple[str, str]:
        cid = self._client_id()
        if not cid:
            raise ValueError(
                "Set MICROSOFT_CLIENT_ID (and MICROSOFT_CLIENT_SECRET for confidential client web apps)."
            )
        return cid, self._client_secret()

    async def on_register(self) -> None:
        await self._apply_env_defaults()

    async def on_reload(self) -> None:
        await self._apply_env_defaults()

    async def on_startup(self) -> None:
        await self._apply_env_defaults()

    async def get_authorization_url(self, code_verifier: Optional[str] = None) -> str:
        """Build the Microsoft / Entra ID authorization URL.

        The ``state`` parameter is an opaque CSRF token from
        :func:`jvagent.action.utils.oauth_state.create_oauth_state`. The
        ``code_verifier`` is persisted server-side and looked up at callback
        time — never sent to the IdP via the browser. AUDIT-actions XC-2.
        """
        from jvagent.action.oauth.state import create_oauth_state

        cid, _ = self._require_client_config()
        if not self.SCOPES:
            raise ValueError(
                f"{self.__class__.__name__} must define SCOPES for Microsoft Graph delegated auth."
            )
        scope_str = " ".join(self.SCOPES)
        state_token = await create_oauth_state(
            action_id=self.id,
            provider="microsoft",
            code_verifier=code_verifier or "",
            redirect_uri=self.redirect_uri,
        )
        params: Dict[str, str] = {
            "client_id": cid,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "response_mode": "query",
            "scope": scope_str,
            "state": state_token,
            "prompt": "consent",
        }
        if code_verifier:
            params["code_challenge"] = pkce_challenge(code_verifier)
            params["code_challenge_method"] = "S256"
        return self._authorize_url() + "?" + urlencode(params)

    async def authorize(self, code: str, code_verifier: Optional[str] = None) -> bool:
        cid, secret = self._require_client_config()
        data = {
            "client_id": cid,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
        }
        if secret:
            data["client_secret"] = secret
        if code_verifier:
            data["code_verifier"] = code_verifier
        token_url = self._token_url()
        async with httpx.AsyncClient() as client:
            resp = await client.post(token_url, data=data)
            resp.raise_for_status()
            payload = resp.json()

        expires_in = int(payload.get("expires_in") or 3600)
        expiry = datetime.now(timezone.utc) + timedelta(seconds=max(0, expires_in - 60))
        scope_raw = payload.get("scope") or " ".join(self.SCOPES)
        scopes_list = (
            scope_raw.split() if isinstance(scope_raw, str) else list(self.SCOPES)
        )
        await self._save_token_payload(
            access_token=payload.get("access_token") or "",
            refresh_token=payload.get("refresh_token") or "",
            token_uri=token_url,
            client_id=cid,
            client_secret=secret,
            scopes=scopes_list,
            expiry=expiry,
        )
        return True

    async def _save_token_payload(
        self,
        *,
        access_token: str,
        refresh_token: str,
        token_uri: str,
        client_id: str,
        client_secret: str,
        scopes: List[str],
        expiry: Optional[datetime],
    ) -> None:
        # AUDIT-actions XC-1: do NOT persist ``client_secret`` on the
        # MicrosoftToken row. The secret is app-wide and re-read from
        # ``MICROSOFT_CLIENT_SECRET`` env at every refresh via
        # ``_client_secret()``. Storing it per row duplicated the leak
        # surface — one DB dump exposed the secret on every consenting
        # user's row.
        #
        # AUDIT-actions XC-1 Fix 2: encrypt access_token + refresh_token
        # before persisting. No-op when JVAGENT_TOKEN_ENC_KEY is unset.
        enc_token = encrypt_token_for_storage(access_token or "")
        enc_refresh = encrypt_token_for_storage(refresh_token or "")
        token_data = {
            "action_id": self.id,
            "token": enc_token,
            "refresh_token": enc_refresh,
            "token_uri": token_uri,
            "client_id": client_id,
            "client_secret": "",  # intentional — see comment above.
            "scopes": scopes,
            "agent_id": self.agent_id,
            "expiry": expiry,
        }
        token_node = await self.node(node="MicrosoftToken", action_id=self.id)
        if token_node:
            token_node.token = enc_token
            token_node.refresh_token = enc_refresh
            token_node.token_uri = token_uri
            token_node.client_id = client_id
            # Scrub any legacy client_secret on existing row.
            token_node.client_secret = ""
            token_node.scopes = scopes
            token_node.expiry = expiry
            await token_node.save()
        else:
            token_node = await MicrosoftToken.create(**token_data)
            await self.connect(token_node)
        _audit_log_oauth_event(
            provider="microsoft",
            event="token_saved",
            action_id=self.id,
            agent_id=self.agent_id,
            client_id_hint=client_id,
        )
        logger.info("Saved Microsoft credentials for action %s", self.id)

    async def _refresh_access_token(self, token_node: MicrosoftToken) -> None:
        # Decrypt persisted refresh_token before using it as the actual
        # OAuth grant. AUDIT-actions XC-1 Fix 2.
        stored_refresh = decrypt_token_from_storage(token_node.refresh_token or "")
        if not stored_refresh:
            raise ValueError("No refresh token; re-authorize the Microsoft action.")
        cid, secret = self._require_client_config()
        data = {
            "client_id": cid,
            "grant_type": "refresh_token",
            "refresh_token": stored_refresh,
        }
        if secret:
            data["client_secret"] = secret
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                token_node.token_uri or self._token_url(), data=data
            )
            resp.raise_for_status()
            payload = resp.json()
        expires_in = int(payload.get("expires_in") or 3600)
        expiry = datetime.now(timezone.utc) + timedelta(seconds=max(0, expires_in - 60))
        # Rotation rule: prefer the provider-supplied new refresh_token;
        # else keep the previous plaintext one (NOT the encrypted blob).
        new_refresh = payload.get("refresh_token") or stored_refresh
        await self._save_token_payload(
            access_token=payload.get("access_token") or "",
            refresh_token=new_refresh,
            token_uri=token_node.token_uri or self._token_url(),
            client_id=cid,
            client_secret=secret,
            scopes=token_node.scopes or self.SCOPES,
            expiry=expiry,
        )

    async def _get_access_token(self) -> str:
        token_node = await self.node(node="MicrosoftToken", action_id=self.id)
        if not token_node or not token_node.token:
            raise ValueError(
                f"No Microsoft OAuth token for action {self.id}; open auth_url to sign in."
            )
        now = datetime.now(timezone.utc)
        exp = token_node.expiry
        if exp is not None:
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if now >= exp:
                logger.info("Refreshing Microsoft token for action %s", self.id)
                await self._refresh_access_token(token_node)
                token_node = await self.node(node="MicrosoftToken", action_id=self.id)
        if not token_node or not token_node.token:
            raise ValueError(f"No valid Microsoft access token for action {self.id}.")
        # Decrypt the stored access token before returning. AUDIT-actions
        # XC-1 Fix 2. Legacy plaintext rows are returned unchanged.
        plain = decrypt_token_from_storage(token_node.token)
        if not plain:
            raise ValueError(
                f"Stored Microsoft access token for action {self.id} could not be "
                "decrypted; user must re-authorize."
            )
        return plain

    async def graph_request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: Optional[Dict[str, Any]] = None,
        content: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> httpx.Response:
        token = await self._get_access_token()
        url = (
            path
            if path.startswith("http")
            else f"{GRAPH_V1.rstrip('/')}/{path.lstrip('/')}"
        )
        h = {"Authorization": f"Bearer {token}"}
        if headers:
            h.update(headers)
        if json_body is not None and content is None:
            h.setdefault("Content-Type", "application/json")
        async with httpx.AsyncClient() as client:
            resp = await client.request(
                method,
                url,
                headers=h,
                params=params,
                json=json_body if content is None else None,
                content=content,
                timeout=120.0,
            )
            return resp

    async def graph_json(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: Optional[Dict[str, Any]] = None,
        ok: Tuple[int, ...] = (200, 201),
    ) -> Any:
        resp = await self.graph_request(
            method, path, json_body=json_body, params=params
        )
        if resp.status_code not in ok:
            detail = resp.text[:500]
            raise RuntimeError(f"Graph {method} {path} -> {resp.status_code}: {detail}")
        if resp.status_code == 204:
            return None
        if not resp.content:
            return None
        return resp.json()
