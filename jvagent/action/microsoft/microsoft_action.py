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
        cid, _ = self._require_client_config()
        if not self.SCOPES:
            raise ValueError(
                f"{self.__class__.__name__} must define SCOPES for Microsoft Graph delegated auth."
            )
        scope_str = " ".join(self.SCOPES)
        state = self.id
        if code_verifier:
            state = f"{self.id}:{code_verifier}"
        params: Dict[str, str] = {
            "client_id": cid,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "response_mode": "query",
            "scope": scope_str,
            "state": state,
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
        token_data = {
            "action_id": self.id,
            "token": access_token,
            "refresh_token": refresh_token,
            "token_uri": token_uri,
            "client_id": client_id,
            "client_secret": client_secret,
            "scopes": scopes,
            "agent_id": self.agent_id,
            "expiry": expiry,
        }
        token_node = await self.node(node="MicrosoftToken", action_id=self.id)
        if token_node:
            token_node.token = access_token
            token_node.refresh_token = refresh_token
            token_node.token_uri = token_uri
            token_node.client_id = client_id
            token_node.client_secret = client_secret
            token_node.scopes = scopes
            token_node.expiry = expiry
            await token_node.save()
        else:
            token_node = await MicrosoftToken.create(**token_data)
            await self.connect(token_node)
        logger.info("Saved Microsoft credentials for action %s", self.id)

    async def _refresh_access_token(self, token_node: MicrosoftToken) -> None:
        if not token_node.refresh_token:
            raise ValueError("No refresh token; re-authorize the Microsoft action.")
        cid, secret = self._require_client_config()
        data = {
            "client_id": cid,
            "grant_type": "refresh_token",
            "refresh_token": token_node.refresh_token,
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
        new_refresh = payload.get("refresh_token") or token_node.refresh_token
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
        return token_node.token

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
