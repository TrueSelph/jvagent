"""SentDM Broadcast Action implementation."""

import logging
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

import httpx
from jvspatial.api.auth.api_key_service import APIKeyService
from jvspatial.core.annotations import attribute
from jvspatial.core.context import GraphContext
from jvspatial.db import get_prime_database
from jvspatial.env import env
from jvspatial.exceptions import DatabaseError, ValidationError

from jvagent.action.base import Action
from jvagent.core.public_url import get_public_base_url

from .webhook_auth import get_or_create_system_user

logger = logging.getLogger(__name__)


_VALID_EVENT_TYPES = {"messages", "templates"}
_DEFAULT_WEBHOOK_DISPLAY_NAME = "jvagent SentDM"


class SentDMBroadcastAction(Action):
    """Send broadcast SMS / WhatsApp messages via the SentDM v3 API.

    Sending requires a template that already exists in your SentDM account.
    The API supports multi-channel fan-out via the ``channel`` array — a
    single request produces a separate message per ``(recipient, channel)``
    pair.

    Configure the API key via the ``SENTDM_API_KEY`` environment variable.
    Webhook auto-registration additionally requires ``JVAGENT_PUBLIC_BASE_URL``
    and ``JVSPATIAL_JWT_SECRET_KEY``.

    Example usage::

        sentdm = await agent.get_action("SentDMBroadcastAction")
        result = await sentdm.send_broadcast(
            to=["+14155551234"],
            template={"name": "order_confirmation", "parameters": {"name": "Jane"}},
            channels=["sms", "whatsapp"],
        )
    """

    api_base: str = attribute(
        default="https://api.sent.dm",
        description="Base URL for the SentDM v3 API",
    )
    default_channels: List[str] = attribute(
        default_factory=lambda: ["sms"],
        description=(
            "Default channels for send_broadcast when the caller does not pass "
            "channels. Values: sms, whatsapp, rcs."
        ),
    )
    default_template_id: str = attribute(
        default="",
        description="Fallback template UUID used when send_broadcast omits template.id",
    )
    default_template_name: str = attribute(
        default="",
        description="Fallback template name used when send_broadcast omits template.name",
    )
    profile_id: str = attribute(
        default="",
        description=(
            "Optional x-profile-id header value. Required when the API key is an "
            "organization key scoped to a child profile."
        ),
    )
    timeout: int = attribute(
        default=30,
        description="HTTP request timeout in seconds",
        ge=1,
        le=300,
    )
    sandbox: bool = attribute(
        default=False,
        description="When true, mutating calls are validated but not executed",
    )

    webhook_display_name: str = attribute(
        default=_DEFAULT_WEBHOOK_DISPLAY_NAME,
        description="Display name used when creating the SentDM webhook endpoint",
    )
    webhook_event_types: List[str] = attribute(
        default_factory=lambda: ["messages"],
        description=(
            "SentDM event categories to subscribe to. Valid values: "
            "messages, templates."
        ),
    )
    webhook_retry_count: int = attribute(
        default=3,
        description="Retry count SentDM uses when delivering webhook events",
        ge=0,
        le=10,
    )
    webhook_timeout_seconds: int = attribute(
        default=30,
        description="Delivery timeout (seconds) for SentDM webhook calls",
        ge=1,
        le=300,
    )

    webhook_url: Optional[str] = attribute(
        default=None,
        description="Public webhook URL given to SentDM (auto-generated)",
    )
    webhook_api_key_id: Optional[str] = attribute(
        default=None,
        description="ID of the jvspatial API key used to authenticate inbound webhooks",
    )
    sentdm_webhook_id: Optional[str] = attribute(
        default=None,
        description="ID of the webhook record created in SentDM",
    )
    sentdm_webhook_secret: Optional[str] = attribute(
        default=None,
        description="HMAC signing secret returned by SentDM when the webhook was created",
    )

    # --- env / configuration helpers ---------------------------------------

    @staticmethod
    def _env_api_key() -> str:
        return (env("SENTDM_API_KEY") or "").strip()

    def is_configured(self) -> bool:
        """True when the API key is present (the minimum to make any call)."""
        return bool(self._env_api_key())

    def _config_issues(self) -> List[str]:
        issues: List[str] = []
        if not self._env_api_key():
            issues.append("SENTDM_API_KEY is not set")
        if not (self.api_base or "").startswith(("http://", "https://")):
            issues.append("api_base must be an http/https URL")
        return issues

    def get_capabilities(self) -> List[str]:
        """Return broadcast capabilities for PersonaAction when enabled."""
        if not self.enabled or not self.is_configured():
            return []
        return [
            "Send template-based SMS or WhatsApp broadcasts via the SentDM API.",
        ]

    # --- HTTP plumbing -----------------------------------------------------

    def _effective_profile_id(self, override: Optional[str] = None) -> str:
        if override and str(override).strip():
            return str(override).strip()
        return (self.profile_id or "").strip()

    def _headers(
        self,
        *,
        idempotency_key: Optional[str] = None,
        profile_id: Optional[str] = None,
        json_content: bool = True,
    ) -> Dict[str, str]:
        api_key = self._env_api_key()
        if not api_key:
            raise ValidationError("SENTDM_API_KEY is not configured")
        headers: Dict[str, str] = {
            "accept": "application/json",
            "x-api-key": api_key,
        }
        if json_content:
            headers["content-type"] = "application/json"
        idem = (idempotency_key or "").strip()
        if idem:
            headers["idempotency-key"] = idem
        pid = self._effective_profile_id(profile_id)
        if pid:
            headers["x-profile-id"] = pid
        return headers

    def _url(self, path: str) -> str:
        return f"{(self.api_base or 'https://api.sent.dm').rstrip('/')}{path}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json_body: Optional[Mapping[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        profile_id: Optional[str] = None,
    ) -> Any:
        """Issue an HTTP request and return parsed JSON (or raise on error)."""
        headers = self._headers(
            idempotency_key=idempotency_key,
            profile_id=profile_id,
            json_content=json_body is not None,
        )
        url = self._url(path)
        clean_params = {
            k: v for k, v in (params or {}).items() if v is not None and v != ""
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.request(
                    method.upper(),
                    url,
                    params=clean_params or None,
                    json=json_body,
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                logger.error(
                    "SentDM %s %s transport error: %s", method.upper(), path, exc
                )
                raise
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text
        if not response.is_success:
            logger.error(
                "SentDM %s %s failed (http=%s): %s",
                method.upper(),
                path,
                response.status_code,
                body,
            )
            response.raise_for_status()
        return body

    # --- core API methods --------------------------------------------------

    def _resolve_template(
        self,
        template: Optional[Mapping[str, Any]],
        parameters: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Resolve template (id|name + parameters) with fallback to action defaults."""
        tmpl: Dict[str, Any] = dict(template or {})
        tmpl_id = str(tmpl.get("id") or "").strip()
        tmpl_name = str(tmpl.get("name") or "").strip()
        if not tmpl_id and not tmpl_name:
            tmpl_id = (self.default_template_id or "").strip()
            tmpl_name = (self.default_template_name or "").strip()
        if not tmpl_id and not tmpl_name:
            raise ValidationError(
                "send_broadcast requires a template id or name (either on the "
                "call or via default_template_id/default_template_name)"
            )

        resolved: Dict[str, Any] = {}
        if tmpl_id:
            resolved["id"] = tmpl_id
        if tmpl_name:
            resolved["name"] = tmpl_name

        merged_params: Dict[str, Any] = {}
        existing_params = tmpl.get("parameters")
        if isinstance(existing_params, Mapping):
            merged_params.update(existing_params)
        if parameters:
            merged_params.update(parameters)
        resolved["parameters"] = merged_params
        return resolved

    async def send_broadcast(
        self,
        to: Union[str, Sequence[str]],
        template: Optional[Mapping[str, Any]] = None,
        *,
        channels: Optional[Sequence[str]] = None,
        parameters: Optional[Mapping[str, Any]] = None,
        sandbox: Optional[bool] = None,
        idempotency_key: Optional[str] = None,
        profile_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a broadcast via ``POST /v3/messages``.

        Args:
            to: Recipient phone number (E.164) or list of phone numbers.
            template: Template descriptor ``{"id"?: str, "name"?: str,
                "parameters"?: dict}``. At least one of ``id`` / ``name`` must
                resolve (per-call or via action defaults).
            channels: Channel list for fan-out (defaults to ``default_channels``).
            parameters: Variable substitutions merged on top of
                ``template["parameters"]``.
            sandbox: Per-call override for sandbox mode.
            idempotency_key: Optional ``idempotency-key`` header value.
            profile_id: Override the action's ``x-profile-id``.

        Returns:
            Raw JSON response from SentDM.
        """
        recipients: List[str]
        if isinstance(to, str):
            recipients = [to]
        else:
            recipients = [str(r) for r in to]
        if not recipients:
            raise ValidationError("send_broadcast requires at least one recipient")

        channel_list = [str(c) for c in (channels or self.default_channels)]
        if not channel_list:
            raise ValidationError(
                "send_broadcast requires at least one channel "
                "(action.default_channels is empty and none were passed)"
            )

        payload: Dict[str, Any] = {
            "to": recipients,
            "channel": channel_list,
            "template": self._resolve_template(template, parameters),
            "sandbox": bool(self.sandbox if sandbox is None else sandbox),
        }

        return await self._request(
            "POST",
            "/v3/messages",
            json_body=payload,
            idempotency_key=idempotency_key,
            profile_id=profile_id,
        )

    async def get_message_status(
        self,
        message_id: str,
        *,
        profile_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """``GET /v3/messages/{id}``."""
        if not message_id:
            raise ValidationError("message_id is required")
        return await self._request(
            "GET", f"/v3/messages/{message_id}", profile_id=profile_id
        )

    async def get_message_activities(
        self,
        message_id: str,
        *,
        profile_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """``GET /v3/messages/{id}/activities``."""
        if not message_id:
            raise ValidationError("message_id is required")
        return await self._request(
            "GET",
            f"/v3/messages/{message_id}/activities",
            profile_id=profile_id,
        )

    async def list_templates(
        self,
        *,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
        search: Optional[str] = None,
        status: Optional[str] = None,
        category: Optional[str] = None,
        profile_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """``GET /v3/templates`` with optional filters."""
        params: Dict[str, Any] = {
            "page": page,
            "page_size": page_size,
            "search": search,
            "status": status,
            "category": category,
        }
        return await self._request(
            "GET", "/v3/templates", params=params, profile_id=profile_id
        )

    async def get_account(self, *, profile_id: Optional[str] = None) -> Dict[str, Any]:
        """``GET /v3/me`` — account identity and configured channels."""
        return await self._request("GET", "/v3/me", profile_id=profile_id)

    async def healthcheck(self) -> Union[bool, Dict[str, Any]]:
        """Lightweight healthcheck — pings ``/v3/me`` when configured."""
        if not self.is_configured():
            return {
                "healthy": True,
                "configured": False,
                "status": "inactive",
                "message": "SentDM action is not configured",
                "issues": self._config_issues(),
            }

        try:
            account = await self.get_account()
        except httpx.HTTPStatusError as exc:
            return {
                "healthy": False,
                "configured": True,
                "status": "error",
                "message": f"SentDM /v3/me returned {exc.response.status_code}",
            }
        except httpx.HTTPError as exc:
            return {
                "healthy": False,
                "configured": True,
                "status": "error",
                "message": f"SentDM /v3/me transport error: {exc}",
            }
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("SentDM healthcheck error: %s", exc, exc_info=True)
            return {
                "healthy": False,
                "configured": True,
                "status": "error",
                "message": str(exc),
            }

        channels: Dict[str, Any] = {}
        if isinstance(account, dict):
            raw_channels = account.get("channels")
            if isinstance(raw_channels, dict):
                channels = {
                    name: bool((info or {}).get("configured"))
                    for name, info in raw_channels.items()
                    if isinstance(info, dict)
                }

        return {
            "healthy": True,
            "configured": True,
            "status": "active",
            "api_base": self.api_base,
            "default_channels": list(self.default_channels or []),
            "channels": channels,
            "webhook_registered": bool(self.sentdm_webhook_id),
        }

    # --- webhook URL / system user ----------------------------------------

    def _expected_webhook_url_base(self, base_url: str) -> str:
        return f"{base_url.rstrip('/')}/api/sentdm/webhook/{str(self.id)}"

    async def get_webhook_url(
        self,
        *,
        allowed_ip: Optional[str] = None,
        regenerate: bool = False,
    ) -> str:
        """Generate (or retrieve) a secure webhook URL with API-key auth.

        Mirrors :meth:`jvagent.action.whatsapp.whatsapp_action.WhatsAppAction.get_webhook_url`
        — the URL embeds an ``api_key`` query param backed by a jvspatial API
        key owned by a dedicated system user. The plaintext key is only known
        at creation time, so the URL is persisted on the action.
        """
        base_url = get_public_base_url()
        if not base_url or not base_url.strip():
            raise ValidationError(
                "JVAGENT_PUBLIC_BASE_URL is required for webhook URL generation"
            )
        if not base_url.startswith(("http://", "https://")):
            raise ValidationError(
                f"JVAGENT_PUBLIC_BASE_URL must be a valid HTTP/HTTPS URL, got: {base_url}"
            )

        try:
            expected_url_base = self._expected_webhook_url_base(base_url)
            prime_ctx = GraphContext(database=get_prime_database())
            api_key_service = APIKeyService(context=prime_ctx)

            if (
                not regenerate
                and self.webhook_url
                and "?api_key=" in self.webhook_url
                and self.webhook_url.startswith(expected_url_base)
            ):
                if allowed_ip is not None and self.webhook_api_key_id:
                    try:
                        existing_key = await api_key_service.get_key(
                            self.webhook_api_key_id
                        )
                        if existing_key and existing_key.is_active:
                            requested_ips = [allowed_ip] if allowed_ip else []
                            existing_ips = (
                                getattr(existing_key, "allowed_ips", None) or []
                            )
                            if requested_ips == existing_ips:
                                return self.webhook_url
                    except Exception:
                        pass
                else:
                    return self.webhook_url

            system_user_id = await get_or_create_system_user()

            if regenerate and self.webhook_api_key_id:
                try:
                    await api_key_service.revoke_key(
                        self.webhook_api_key_id, system_user_id
                    )
                except Exception:
                    pass

            agent = await self.get_agent()
            agent_name = getattr(agent, "name", None) or "agent"

            plaintext_key, api_key = await api_key_service.generate_key(
                user_id=system_user_id,
                name=f"SentDM Webhook - {agent_name}",
                permissions=["webhook:sentdm"],
                expires_in_days=None,
                allowed_ips=[allowed_ip] if allowed_ip else [],
                allowed_endpoints=["/api/sentdm/webhook/*"],
                key_prefix="jv_",
            )

            self.webhook_api_key_id = api_key.id
            self.webhook_url = f"{expected_url_base}?api_key={plaintext_key}"
            await self.save()
            return self.webhook_url

        except DatabaseError:
            raise
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError(f"Webhook URL generation failed: {exc}")

    # --- SentDM webhook CRUD ----------------------------------------------

    async def _sentdm_webhook_list(self) -> List[Dict[str, Any]]:
        """List webhook endpoints registered on the SentDM account."""
        body = await self._request(
            "GET", "/v3/webhooks", params={"page": 1, "page_size": 100}
        )
        items: Any
        if isinstance(body, dict):
            items = (
                body.get("data")
                or body.get("items")
                or body.get("webhooks")
                or body.get("results")
                or []
            )
        elif isinstance(body, list):
            items = body
        else:
            items = []
        return [w for w in items if isinstance(w, dict)]

    async def _sentdm_webhook_create(
        self,
        endpoint_url: str,
        *,
        display_name: Optional[str] = None,
        event_types: Optional[Sequence[str]] = None,
        retry_count: Optional[int] = None,
        timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create a webhook in SentDM and capture its id + signing secret."""
        events = [
            e
            for e in (event_types or self.webhook_event_types or ["messages"])
            if e in _VALID_EVENT_TYPES
        ]
        if not events:
            events = ["messages"]

        payload = {
            "display_name": display_name or self.webhook_display_name,
            "endpoint_url": endpoint_url,
            "event_types": events,
            "retry_count": (
                retry_count if retry_count is not None else self.webhook_retry_count
            ),
            "timeout_seconds": (
                timeout_seconds
                if timeout_seconds is not None
                else self.webhook_timeout_seconds
            ),
            "sandbox": False,
        }
        body = await self._request("POST", "/v3/webhooks", json_body=payload)
        data = body.get("data") if isinstance(body, dict) and "data" in body else body
        if not isinstance(data, dict):
            data = body if isinstance(body, dict) else {}

        webhook_id = str(data.get("id") or data.get("webhook_id") or "")
        secret = str(
            data.get("signing_secret")
            or data.get("secret")
            or data.get("signingSecret")
            or ""
        )
        if webhook_id:
            self.sentdm_webhook_id = webhook_id
        if secret:
            self.sentdm_webhook_secret = secret
        if webhook_id or secret:
            await self.save()
        return data

    async def _sentdm_webhook_delete(self, webhook_id: str) -> None:
        if not webhook_id:
            return
        try:
            await self._request("DELETE", f"/v3/webhooks/{webhook_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (404, 410):
                return
            raise

    async def reconcile_webhook_endpoint(self) -> Dict[str, Any]:
        """Ensure SentDM has exactly one webhook pointing at our public URL.

        - Generates the webhook URL if missing.
        - Lists existing SentDM webhooks; keeps an exact ``endpoint_url`` match.
        - Deletes stale webhooks with the same ``display_name`` or URL prefix.
        - Creates a new webhook (with signing secret) if no match was kept.
        """
        if not self.is_configured():
            return {
                "status": "skipped",
                "reason": "SentDM action is not configured",
                "issues": self._config_issues(),
            }

        base_url = get_public_base_url()
        if not base_url:
            return {
                "status": "skipped",
                "reason": "JVAGENT_PUBLIC_BASE_URL is not set",
            }

        desired_url = await self.get_webhook_url()
        desired_prefix = self._expected_webhook_url_base(base_url)
        display_name = (
            self.webhook_display_name or _DEFAULT_WEBHOOK_DISPLAY_NAME
        ).strip()

        try:
            existing = await self._sentdm_webhook_list()
        except Exception as exc:
            logger.warning("SentDM webhook list failed: %s", exc)
            return {"status": "error", "message": f"webhook list failed: {exc}"}

        exact_matches: List[Dict[str, Any]] = []
        stale_matches: List[Dict[str, Any]] = []
        for ep in existing:
            ep_url = str(ep.get("endpoint_url") or ep.get("url") or "").strip()
            ep_name = str(ep.get("display_name") or ep.get("name") or "").strip()
            if not ep_url:
                continue
            if ep_url == desired_url:
                exact_matches.append(ep)
            elif desired_prefix and ep_url.startswith(desired_prefix):
                stale_matches.append(ep)
            elif display_name and ep_name == display_name:
                stale_matches.append(ep)

        deleted: List[str] = []
        for ep in stale_matches:
            wid = str(ep.get("id") or ep.get("webhook_id") or "")
            if not wid:
                continue
            try:
                await self._sentdm_webhook_delete(wid)
                deleted.append(wid)
            except Exception as exc:
                logger.warning("SentDM: failed deleting stale webhook %s: %s", wid, exc)

        kept: Optional[Dict[str, Any]] = None
        if exact_matches:
            kept = exact_matches[0]
            for ep in exact_matches[1:]:
                wid = str(ep.get("id") or ep.get("webhook_id") or "")
                if not wid:
                    continue
                try:
                    await self._sentdm_webhook_delete(wid)
                    deleted.append(wid)
                except Exception as exc:
                    logger.warning(
                        "SentDM: failed deleting duplicate webhook %s: %s", wid, exc
                    )

        created: Optional[Dict[str, Any]] = None
        if kept:
            wid = str(kept.get("id") or kept.get("webhook_id") or "")
            if wid and wid != (self.sentdm_webhook_id or ""):
                self.sentdm_webhook_id = wid
                await self.save()
        else:
            try:
                created = await self._sentdm_webhook_create(desired_url)
                kept = created
            except Exception as exc:
                logger.error("SentDM: webhook create failed: %s", exc)
                return {
                    "status": "error",
                    "message": f"webhook create failed: {exc}",
                    "desired_url": desired_url,
                    "deleted_webhook_ids": deleted,
                }

        if not self.sentdm_webhook_secret:
            logger.warning(
                "SentDM webhook created/kept without a signing secret on record; "
                "incoming signature verification will fail. Rotate the secret via "
                "POST /v3/webhooks/{id}/rotate-secret and persist it on the action."
            )

        return {
            "status": "ok",
            "desired_url": desired_url,
            "webhook": kept or {},
            "created": created is not None,
            "deleted_webhook_ids": deleted,
        }

    # --- lifecycle hooks ---------------------------------------------------

    async def on_register(self) -> None:
        """Validate configuration and best-effort reconcile the webhook."""
        if not self.is_configured():
            logger.debug(
                "SentDMBroadcastAction not configured: %s",
                "; ".join(self._config_issues()),
            )
            return
        await self._try_reconcile_webhook(reason="on_register")

    async def on_reload(self) -> None:
        """Re-reconcile webhook so it tracks the current public URL / api key."""
        if not self.is_configured():
            return
        await self._try_reconcile_webhook(reason="on_reload")

    async def _try_reconcile_webhook(self, *, reason: str) -> None:
        if not get_public_base_url():
            logger.debug(
                "SentDM webhook reconcile (%s) skipped: JVAGENT_PUBLIC_BASE_URL is not set",
                reason,
            )
            return
        try:
            result = await self.reconcile_webhook_endpoint()
            if isinstance(result, dict) and result.get("status") == "ok":
                logger.debug(
                    "SentDM webhook reconciled (%s): created=%s deleted=%s",
                    reason,
                    result.get("created"),
                    result.get("deleted_webhook_ids"),
                )
            else:
                logger.warning(
                    "SentDM webhook reconcile (%s) returned non-ok: %s", reason, result
                )
        except Exception as exc:
            logger.warning(
                "SentDM webhook reconcile (%s) failed: %s", reason, exc, exc_info=True
            )
