"""SentDM Broadcast Action implementation."""

import logging
from datetime import datetime, timezone
from typing import (
    Any,
    ClassVar,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)
from urllib.parse import urlsplit

import httpx
from jvspatial.api.auth.api_key_service import APIKeyService
from jvspatial.core.annotations import attribute
from jvspatial.core.context import GraphContext
from jvspatial.db import get_prime_database
from jvspatial.env import env
from jvspatial.exceptions import DatabaseError, ValidationError

from jvagent.action.base import Action
from jvagent.core.public_url import get_public_base_url

from .models import SentDMBroadcastRecord
from .webhook_auth import get_or_create_system_user

logger = logging.getLogger(__name__)


def _sentdm_webhook_endpoint_url(ep: Mapping[str, Any]) -> str:
    for key in (
        "endpoint_url",
        "endpointUrl",
        "url",
        "callback_url",
        "callbackUrl",
        "webhook_url",
        "webhookUrl",
    ):
        raw = ep.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def _sentdm_webhook_record_id(ep: Mapping[str, Any]) -> str:
    for key in ("id", "webhook_id", "webhookId", "uuid", "UUID"):
        raw = ep.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return ""


def _extract_sentdm_webhook_list_items(body: Any) -> List[Dict[str, Any]]:
    """Unwrap ``GET /v3/webhooks`` JSON into webhook row dicts (handles nested ``data``)."""
    acc: List[Dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for it in node:
                walk(it)
            return
        if not isinstance(node, dict):
            return
        branched = False
        for key in ("data", "items", "webhooks", "results", "records", "rows"):
            child = node.get(key)
            if isinstance(child, list):
                branched = True
                for it in child:
                    walk(it)
            elif isinstance(child, dict):
                branched = True
                walk(child)
        if branched:
            return
        if _sentdm_webhook_endpoint_url(node) or _sentdm_webhook_record_id(node):
            acc.append(node)

    walk(body)
    return acc


# Current + legacy inbound paths (reconcile deletes stale rows on the same host).
_JVAGENT_SENTDM_WEBHOOK_PATH_PREFIXES: Tuple[str, ...] = (
    "/api/webhook/",
    "/api/sentdm/webhook/",
)


def _sentdm_webhook_url_on_public_origin(ep_url: str, public_base: str) -> bool:
    """True when ``ep_url`` is our jvagent SentDM inbound URL on ``public_base``."""
    ep = urlsplit((ep_url or "").strip())
    base = urlsplit((public_base or "").strip())
    if (ep.scheme or "https").lower() != (base.scheme or "https").lower():
        return False
    if ep.netloc.lower() != base.netloc.lower():
        return False
    path = ep.path or "/"
    return any(path.startswith(p) for p in _JVAGENT_SENTDM_WEBHOOK_PATH_PREFIXES)


def _sentdm_webhook_urls_equivalent(a: str, b: str) -> bool:
    """True for identical URLs or same origin+path when Sent omits ``?api_key=`` in list APIs."""
    sa = (a or "").strip()
    sb = (b or "").strip()
    if sa == sb:
        return True
    pa, pb = urlsplit(sa), urlsplit(sb)
    if pa.netloc.lower() != pb.netloc.lower():
        return False
    if (pa.scheme or "https").lower() != (pb.scheme or "https").lower():
        return False
    path_a = (pa.path or "/").rstrip("/") or "/"
    path_b = (pb.path or "/").rstrip("/") or "/"
    return path_a == path_b


# Sent's POST /v3/webhooks expects ``message`` (singular) for message lifecycle
# events; older bundled docs used ``messages`` — we normalize when calling the API.
_SENTDM_WEBHOOK_EVENT_API_VALUE = {
    "message": "message",
    "messages": "message",
    "templates": "templates",
}
_DEFAULT_WEBHOOK_DISPLAY_NAME = "jvagent SentDM"
_DEFAULT_WEBHOOK_EVENT_FILTERS: Dict[str, List[str]] = {
    "message": ["queued", "sent", "delivered", "read", "failed", "received"],
}


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

    # AUDIT-actions XC-4: SentDM webhook receives status callbacks. The
    # path is per-action_id (``/webhook/{action_id}`` is intentionally
    # generic but unique to this instance).
    additional_endpoint_path_templates: ClassVar[List[str]] = [
        "/webhook/{action_id}",
    ]

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
        default_factory=lambda: ["message"],
        description=(
            "Sent webhook parent ``event_types`` for ``POST /v3/webhooks``. Use "
            "``message`` (singular) for outbound/inbound message events; "
            "``templates`` for template lifecycle. Sub-types (e.g. "
            "``message.delivered``) are *not* listed here — use "
            "``webhook_event_filters`` instead. Legacy ``messages`` maps to "
            "``message``."
        ),
    )
    webhook_event_filters: Optional[Dict[str, List[str]]] = attribute(
        default=None,
        description=(
            "Optional Sent ``event_filters`` map: parent event type → list of "
            "subtype suffixes (e.g. ``{'message': ['sent', 'delivered']}``). "
            "``None`` applies a broadcast-focused default "
            "(queued, sent, delivered, read, failed, received). ``{}`` omits filters (all "
            "sub-types for subscribed parents)."
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

    persist_records: bool = attribute(
        default=True,
        description=(
            "Persist a SentDMBroadcastRecord per (recipient, channel) on each "
            "send so webhook events can be folded back into the graph."
        ),
    )
    persist_sandbox_sends: bool = attribute(
        default=False,
        description=(
            "Persist records for sandbox sends too. Off by default to keep the "
            "graph free of test traffic."
        ),
    )
    record_event_history_limit: int = attribute(
        default=25,
        description=(
            "Maximum entries kept in SentDMBroadcastRecord.events. Older entries "
            "are dropped FIFO."
        ),
        ge=1,
        le=500,
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
            details: Any = None
            if isinstance(body, dict):
                err = body.get("error")
                if isinstance(err, dict):
                    details = err.get("details")
            logger.error(
                "SentDM %s %s failed (http=%s): %s",
                method.upper(),
                path,
                response.status_code,
                body,
            )
            if details is not None:
                logger.error("SentDM validation details: %s", details)
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

        resolved_template = self._resolve_template(template, parameters)
        effective_sandbox = bool(self.sandbox if sandbox is None else sandbox)

        payload: Dict[str, Any] = {
            "to": recipients,
            "channel": channel_list,
            "template": resolved_template,
            "sandbox": effective_sandbox,
        }

        response = await self._request(
            "POST",
            "/v3/messages",
            json_body=payload,
            idempotency_key=idempotency_key,
            profile_id=profile_id,
        )

        try:
            await self._persist_send_records(
                response=response,
                recipients=recipients,
                channels=channel_list,
                template=resolved_template,
                sandbox=effective_sandbox,
                idempotency_key=idempotency_key,
                profile_id=profile_id,
            )
        except Exception as exc:  # pragma: no cover - best-effort persistence
            logger.warning(
                "SentDM send_broadcast succeeded but record persistence failed: %s",
                exc,
                exc_info=True,
            )

        return response

    # --- broadcast record helpers -----------------------------------------

    @staticmethod
    def _unwrap_sent_v3_envelope(body: Any) -> Any:
        """Return ``body['data']`` when ``body`` looks like ``{success, data, meta}``."""
        if not isinstance(body, dict) or "data" not in body:
            return body
        inner = body.get("data")
        if not isinstance(inner, (dict, list)):
            return body
        if "success" in body or "meta" in body or body.get("ok") is True:
            return inner
        return body

    @staticmethod
    def _dict_has_message_identifier(d: dict) -> bool:
        for key in ("id", "message_id", "messageId"):
            v = d.get(key)
            if isinstance(v, str) and v.strip():
                return True
        return False

    @staticmethod
    def _collect_message_lists_and_singletons(
        node: Any, *, _depth: int = 0
    ) -> List[Dict[str, Any]]:
        """Gather dict rows that look like Sent message descriptors from a node."""
        if _depth > 12:
            return []
        out: List[Dict[str, Any]] = []
        if isinstance(node, list):
            for entry in node:
                if isinstance(
                    entry, dict
                ) and SentDMBroadcastAction._dict_has_message_identifier(entry):
                    out.append(entry)
            return out
        if not isinstance(node, dict):
            return out

        for key in (
            "messages",
            "results",
            "items",
            "records",
            "rows",
            "message",
            "data",
            "Messages",
            "Results",
        ):
            if key not in node:
                continue
            value = node.get(key)
            if isinstance(value, list):
                for entry in value:
                    if isinstance(
                        entry, dict
                    ) and SentDMBroadcastAction._dict_has_message_identifier(entry):
                        out.append(entry)
                if out:
                    return out
            if isinstance(value, dict):
                if SentDMBroadcastAction._dict_has_message_identifier(value):
                    out.append(value)
                    return out
                nested = SentDMBroadcastAction._collect_message_lists_and_singletons(
                    value, _depth=_depth + 1
                )
                if nested:
                    return nested

        if SentDMBroadcastAction._dict_has_message_identifier(node):
            out.append(node)
            return out
        msg = node.get("message")
        if isinstance(msg, dict) and SentDMBroadcastAction._dict_has_message_identifier(
            msg
        ):
            out.append(msg)
            return out
        return out

    @staticmethod
    def _walk_sent_tree_for_message_dicts(
        obj: Any, *, _depth: int = 0, _seen: Optional[set] = None
    ) -> List[Dict[str, Any]]:
        """Last-resort scan for message-shaped dicts (nested ``results.messages``, etc.)."""
        if _depth > 14 or obj is None:
            return []
        if _seen is None:
            _seen = set()
        found: List[Dict[str, Any]] = []
        if isinstance(obj, dict):
            oid = id(obj)
            if oid in _seen:
                return []
            _seen.add(oid)
            if SentDMBroadcastAction._dict_has_message_identifier(obj):
                hint_keys = (
                    "to",
                    "channel",
                    "message_status",
                    "messageStatus",
                    "status",
                    "template_id",
                    "templateId",
                    "sandbox",
                    "recipient",
                    "phone",
                )
                if any(k in obj for k in hint_keys):
                    return [obj]
                if _depth >= 2:
                    rid = str(
                        obj.get("id")
                        or obj.get("message_id")
                        or obj.get("messageId")
                        or ""
                    ).strip()
                    if len(rid) >= 32 and rid.count("-") >= 4:
                        return [obj]
            for k, v in obj.items():
                if k in ("meta",) and isinstance(v, dict) and len(v) > 8:
                    continue
                found.extend(
                    SentDMBroadcastAction._walk_sent_tree_for_message_dicts(
                        v, _depth=_depth + 1, _seen=_seen
                    )
                )
        elif isinstance(obj, list):
            for it in obj:
                found.extend(
                    SentDMBroadcastAction._walk_sent_tree_for_message_dicts(
                        it, _depth=_depth + 1, _seen=_seen
                    )
                )
        return found

    @staticmethod
    def _extract_sent_message_descriptors(
        response: Any,
    ) -> List[Dict[str, Any]]:
        """Extract per-message descriptors from a ``POST /v3/messages`` response.

        SentDM's response shape is not fully documented and may evolve, so we
        probe a few candidate locations and collect anything that looks like a
        message descriptor (i.e. has an ``id`` / ``message_id``). The
        descriptors are returned verbatim so callers can read ``id``, ``to``,
        ``channel``, ``status`` etc. with whatever names SentDM uses.

        Recent APIs wrap payloads as ``{success, data, meta}`` where ``data``
        holds ``messages`` or a single message object — older code only treated
        top-level ``data`` as a list and missed those rows.
        """
        cur: Any = response
        for _ in range(3):
            nxt = SentDMBroadcastAction._unwrap_sent_v3_envelope(cur)
            if nxt is cur:
                break
            cur = nxt

        if isinstance(cur, list):
            return SentDMBroadcastAction._collect_message_lists_and_singletons(cur)

        if isinstance(cur, dict):
            found = SentDMBroadcastAction._collect_message_lists_and_singletons(cur)
            if found:
                return found
            # ``data`` may be a nested envelope or message bag
            inner = cur.get("data")
            if isinstance(inner, (dict, list)):
                found = SentDMBroadcastAction._collect_message_lists_and_singletons(
                    inner
                )
                if found:
                    return found

        if isinstance(cur, (dict, list)):
            scanned = SentDMBroadcastAction._walk_sent_tree_for_message_dicts(cur)
            if scanned:
                # De-dupe by message id
                by_id: Dict[str, Dict[str, Any]] = {}
                for row in scanned:
                    rid = str(
                        row.get("id")
                        or row.get("message_id")
                        or row.get("messageId")
                        or ""
                    ).strip()
                    if rid:
                        by_id[rid] = row
                return list(by_id.values())

        return []

    async def _persist_send_records(
        self,
        *,
        response: Any,
        recipients: Sequence[str],
        channels: Sequence[str],
        template: Mapping[str, Any],
        sandbox: bool,
        idempotency_key: Optional[str],
        profile_id: Optional[str],
    ) -> List[SentDMBroadcastRecord]:
        """Create a SentDMBroadcastRecord per (message_id, recipient, channel)."""
        if not self.persist_records:
            return []
        if sandbox and not self.persist_sandbox_sends:
            logger.info(
                "SentDM persist: skipping SentDMBroadcastRecord creation "
                "(sandbox send; set persist_sandbox_sends=true on the action to record)"
            )
            return []

        descriptors = self._extract_sent_message_descriptors(response)
        if not descriptors:
            data_keys: Optional[List[str]] = None
            if isinstance(response, dict) and isinstance(response.get("data"), dict):
                data_keys = list(response["data"].keys())[:32]
            logger.info(
                "SentDM persist: no message descriptors in POST /v3/messages response; "
                "skipping SentDMBroadcastRecord (type=%s keys=%s data_keys=%s)",
                type(response).__name__,
                list(response.keys()) if isinstance(response, dict) else None,
                data_keys,
            )
            return []

        agent = await self.get_agent()
        agent_id = str(getattr(agent, "id", "") or "")

        template_id = str(template.get("id") or "") or None
        template_name = str(template.get("name") or "") or None
        template_params = template.get("parameters") or {}
        if not isinstance(template_params, dict):
            template_params = {}

        records: List[SentDMBroadcastRecord] = []
        for desc in descriptors:
            sentdm_message_id = str(desc.get("id") or desc.get("message_id") or "")
            if not sentdm_message_id:
                continue
            to_value = str(
                desc.get("to") or desc.get("recipient") or desc.get("phone") or ""
            )
            if not to_value and len(recipients) == 1:
                to_value = recipients[0]
            channel_value = str(desc.get("channel") or "")
            if not channel_value and len(channels) == 1:
                channel_value = channels[0]

            status_value = str(desc.get("status") or "accepted").lower() or "accepted"

            record = await SentDMBroadcastRecord.create(
                action_id=str(self.id),
                agent_id=agent_id,
                sentdm_message_id=sentdm_message_id,
                to=to_value,
                channel=channel_value,
                template_id=template_id,
                template_name=template_name,
                parameters=dict(template_params),
                idempotency_key=idempotency_key or None,
                profile_id=self._effective_profile_id(profile_id) or None,
                sandbox=sandbox,
                status=status_value,
                last_event_field="send",
                last_event_payload=desc,
                last_status_at=datetime.now(timezone.utc),
                events=[
                    {
                        "field": "send",
                        "status": status_value,
                        "payload": desc,
                        "received_at": datetime.now(timezone.utc).isoformat(),
                    }
                ],
            )
            try:
                await self.connect(record)
            except Exception as exc:  # pragma: no cover - non-fatal
                logger.debug(
                    "SentDM persist: failed to connect action->record edge: %s", exc
                )
            records.append(record)

        if records:
            logger.debug(
                "SentDM persist: stored %d broadcast record(s) for action %s",
                len(records),
                self.id,
            )
        return records

    async def _record_for_message_id(
        self, message_id: str
    ) -> Optional[SentDMBroadcastRecord]:
        """Look up the broadcast record for a SentDM message id."""
        key = (message_id or "").strip()
        if not key:
            return None
        try:
            result = await SentDMBroadcastRecord.find_one(
                action_id=str(self.id), sentdm_message_id=key
            )
        except Exception as exc:
            logger.warning("SentDM persist: find_one failed for %s: %s", key, exc)
            return None
        if result is None or not isinstance(result, SentDMBroadcastRecord):
            return None
        return result

    async def _create_record_from_webhook_fold(
        self,
        field: str,
        fold: Dict[str, Any],
        message_id: str,
    ) -> SentDMBroadcastRecord:
        """Create a minimal ``SentDMBroadcastRecord`` from the first webhook payload."""
        body = fold if isinstance(fold, dict) else {}
        mid = (message_id or "").strip()
        if not mid:
            raise ValueError("message_id is required to create a webhook record")

        outbound = str(body.get("outbound_number") or "").strip()
        to_val = "" if outbound.lower() == "unknown" else outbound

        chan = str(body.get("channel") or "").strip()
        if chan.lower() == "unknown":
            chan = ""

        tid_raw = body.get("template_id")
        template_id: Optional[str]
        if tid_raw is None or (isinstance(tid_raw, str) and not tid_raw.strip()):
            template_id = None
        else:
            template_id = str(tid_raw).strip() or None

        record = await SentDMBroadcastRecord.create(
            action_id=str(self.id),
            agent_id=str(self.agent_id or ""),
            sentdm_message_id=mid,
            to=to_val,
            channel=chan,
            template_id=template_id,
            template_name=None,
            parameters={},
            idempotency_key=None,
            profile_id=None,
            sandbox=False,
            status="accepted",
            last_event_field=None,
            last_event_payload=None,
            last_status_at=None,
            events=[],
            error=None,
        )
        try:
            await self.connect(record)
        except Exception as exc:  # pragma: no cover - non-fatal
            logger.debug(
                "SentDM webhook upsert: connect action->record failed: %s", exc
            )
        logger.info(
            "SentDM webhook: created SentDMBroadcastRecord for message_id=%s "
            "(field=%s action=%s)",
            mid,
            field or "(unknown)",
            self.id,
        )
        return record

    async def apply_webhook_with_upsert(
        self,
        field: str,
        fold: Dict[str, Any],
        message_id: str,
    ) -> Optional[SentDMBroadcastRecord]:
        """Find or create the graph record for ``message_id`` and fold in this event."""
        mid = (message_id or "").strip()
        if not mid:
            return None

        record = await self._record_for_message_id(mid)
        if record is None:
            record = await self._create_record_from_webhook_fold(field, fold, mid)
        return await self._apply_webhook_event_to_record(record, field, fold)

    @staticmethod
    def _derive_status_and_error(
        field: str,
        payload: Any,
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """Extract a normalized status (and optional error) from a webhook event.

        Resolution order:

        1. ``payload.status`` / ``payload.message_status`` (or nested ``data``)
           when it matches a known status string (case-insensitive).
        2. Event-name suffix when ``payload.event``, ``event_type``, ``eventType``,
           or ``sub_type`` looks like ``message.delivered`` / ``message.sent`` / etc.
        3. None when nothing matched (caller keeps the prior status).
        """
        known = {
            "accepted",
            "queued",
            "processing",
            "routed",
            "sent",
            "delivered",
            "read",
            "failed",
            "rejected",
            "undelivered",
            "expired",
            "unknown",
            "received",
        }

        def _normalize(value: Any) -> Optional[str]:
            if not isinstance(value, str):
                return None
            v = value.strip().lower()
            if not v:
                return None
            if v in known:
                return v
            return None

        body: Dict[str, Any] = payload if isinstance(payload, dict) else {}
        status = _normalize(body.get("status"))
        if not status:
            status = _normalize(body.get("message_status"))
        if not status and isinstance(body.get("data"), dict):
            status = _normalize(body["data"].get("status"))
        if not status and isinstance(body.get("data"), dict):
            status = _normalize(body["data"].get("message_status"))

        if not status:
            event_name = (
                body.get("event")
                or body.get("event_type")
                or body.get("eventType")
                or body.get("sub_type")
            )
            if isinstance(event_name, str) and "." in event_name:
                suffix = event_name.split(".", 1)[1].strip().lower()
                status = _normalize(suffix)

        error_payload: Optional[Dict[str, Any]] = None
        if status in {"failed", "rejected", "undelivered"}:
            err = body.get("error") or body.get("failure_reason") or body.get("reason")
            if isinstance(err, dict):
                error_payload = err
            elif isinstance(err, str) and err.strip():
                error_payload = {"message": err.strip()}

        # field is reserved for callers; not used to derive status yet but
        # available for future heuristics.
        _ = field
        return status, error_payload

    _WEBHOOK_AUDIT_KEYS = frozenset(
        {
            "sub_type",
            "message_id",
            "sentdm_message_id",
            "id",
            "status",
            "message_status",
            "channel",
            "account_id",
            "template_id",
            "inbound_number",
            "outbound_number",
        }
    )

    @staticmethod
    def _compact_webhook_audit_payload(payload: Any) -> Optional[Dict[str, Any]]:
        """Persist only Sent message fields useful for status correlation / support."""
        if not isinstance(payload, dict):
            return None
        out = {
            k: payload[k]
            for k in SentDMBroadcastAction._WEBHOOK_AUDIT_KEYS
            if k in payload and payload[k] is not None and str(payload[k]).strip() != ""
        }
        return out or None

    async def _apply_webhook_event_to_record(
        self,
        record: SentDMBroadcastRecord,
        field: str,
        payload: Any,
        *,
        source: str = "webhook",
    ) -> SentDMBroadcastRecord:
        """Fold a webhook (or refresh) event into a broadcast record."""
        new_status, error_payload = self._derive_status_and_error(field, payload)
        now = datetime.now(timezone.utc)

        stored = self._compact_webhook_audit_payload(payload)
        event_entry: Dict[str, Any] = {
            "source": source,
            "field": field or None,
            "status": new_status,
            "received_at": now.isoformat(),
            "payload": stored,
        }
        events = list(record.events or [])
        events.append(event_entry)
        cap = max(int(self.record_event_history_limit or 1), 1)
        if len(events) > cap:
            events = events[-cap:]
        record.events = events

        record.last_event_field = field or record.last_event_field
        if stored is not None:
            record.last_event_payload = stored

        if new_status and new_status != record.status:
            record.status = new_status
            record.last_status_at = now
        elif new_status:
            record.last_status_at = now

        if error_payload:
            record.error = error_payload
        elif new_status and new_status not in {"failed", "rejected", "undelivered"}:
            record.error = None

        record.updated_at = now
        await record.save()
        return record

    async def refresh_record(
        self,
        record_id: str,
    ) -> Dict[str, Any]:
        """Re-fetch the latest server-truth status for one record.

        Useful when a webhook was missed or delivered to a different replica.
        Calls ``GET /v3/messages/{sentdm_message_id}`` and folds the result
        into the record's audit log.
        """
        if not record_id:
            raise ValidationError("record_id is required")
        record = await SentDMBroadcastRecord.get(record_id)
        if record is None or not isinstance(record, SentDMBroadcastRecord):
            raise ValidationError(f"SentDMBroadcastRecord not found: {record_id}")
        if str(getattr(record, "action_id", "")) != str(self.id):
            raise ValidationError(
                f"Record {record_id} does not belong to action {self.id}"
            )
        if not record.sentdm_message_id:
            raise ValidationError(
                f"Record {record_id} has no sentdm_message_id; cannot refresh"
            )

        status_body = await self.get_message_status(
            record.sentdm_message_id, profile_id=record.profile_id or None
        )
        fold: Dict[str, Any]
        if isinstance(status_body, dict):
            inner = status_body.get("data")
            fold = dict(inner) if isinstance(inner, dict) else dict(status_body)
        else:
            fold = {}
        updated = await self._apply_webhook_event_to_record(
            record, "refresh", fold, source="refresh"
        )
        return {
            "record_id": updated.id,
            "sentdm_message_id": updated.sentdm_message_id,
            "status": updated.status,
            "last_status_at": (
                updated.last_status_at.isoformat() if updated.last_status_at else None
            ),
            "upstream": status_body,
        }

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
        return f"{base_url.rstrip('/')}/api/webhook/{str(self.id)}"

    @staticmethod
    def _sentdm_webhook_path_prefix_for_base(base_url: str) -> str:
        """URL prefix for any jvagent SentDM webhook on this public host.

        Matches ``<public-base>/api/webhook/<any-action-id>`` (and legacy
        ``/api/sentdm/webhook/``) so reconcile can remove stale Sent endpoints
        on the same ``JVAGENT_PUBLIC_BASE_URL``.
        """
        return f"{(base_url or '').rstrip('/')}/api/webhook/"

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
                allowed_endpoints=[
                    "/api/webhook/*",
                    "/api/sentdm/webhook/*",
                ],
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
        """List webhook endpoints registered on the SentDM account (all pages)."""
        merged: Dict[str, Dict[str, Any]] = {}
        page = 1
        while page <= 100:
            body = await self._request(
                "GET",
                "/v3/webhooks",
                params={"page": str(page), "page_size": "100"},
            )
            batch = _extract_sentdm_webhook_list_items(body)
            if not batch:
                break
            for ep in batch:
                wid = _sentdm_webhook_record_id(ep)
                ep_url = _sentdm_webhook_endpoint_url(ep)
                key = wid or f"url:{ep_url}"
                merged[key] = ep
            if len(batch) < 100:
                break
            page += 1
        return list(merged.values())

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
        mapped: List[str] = []
        for raw in event_types or self.webhook_event_types or ["message"]:
            key = str(raw).strip().lower()
            if key in _SENTDM_WEBHOOK_EVENT_API_VALUE:
                mapped.append(_SENTDM_WEBHOOK_EVENT_API_VALUE[key])
        # De-dupe while preserving order
        seen: set = set()
        events: List[str] = []
        for ev in mapped:
            if ev not in seen:
                seen.add(ev)
                events.append(ev)
        if not events:
            events = ["message"]

        payload: Dict[str, Any] = {
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

        filters_raw = self.webhook_event_filters
        if filters_raw is None:
            payload["event_filters"] = {
                k: list(v) for k, v in _DEFAULT_WEBHOOK_EVENT_FILTERS.items()
            }
        elif filters_raw:
            cleaned: Dict[str, List[str]] = {}
            for raw_key, raw_list in filters_raw.items():
                if not isinstance(raw_list, list):
                    continue
                key = str(raw_key).strip()
                if not key:
                    continue
                items = [str(x).strip() for x in raw_list if str(x).strip()]
                if items:
                    cleaned[key] = items
            if cleaned:
                payload["event_filters"] = cleaned

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
        """Ensure SentDM has exactly one webhook for this action's URL.

        - Generates the webhook URL if missing.
        - Lists existing SentDM webhooks; keeps an exact ``endpoint_url`` match.
        - Deletes other webhooks under the same public host whose path starts with
          ``/api/webhook/`` or legacy ``/api/sentdm/webhook/`` (any action id) that
          are not the desired URL.
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

        try:
            existing = await self._sentdm_webhook_list()
        except Exception as exc:
            logger.warning("SentDM webhook list failed: %s", exc)
            return {"status": "error", "message": f"webhook list failed: {exc}"}

        exact_matches: List[Dict[str, Any]] = []
        stale_matches: List[Dict[str, Any]] = []
        for ep in existing:
            ep_url = _sentdm_webhook_endpoint_url(ep)
            if not ep_url:
                continue
            if _sentdm_webhook_urls_equivalent(ep_url, desired_url):
                exact_matches.append(ep)
            elif _sentdm_webhook_url_on_public_origin(
                ep_url, base_url
            ) and not _sentdm_webhook_urls_equivalent(ep_url, desired_url):
                stale_matches.append(ep)

        deleted: List[str] = []
        for ep in stale_matches:
            wid = _sentdm_webhook_record_id(ep)
            if not wid:
                logger.warning(
                    "SentDM reconcile: skipping stale webhook with no id (url=%s)",
                    _sentdm_webhook_endpoint_url(ep) or "?",
                )
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
                wid = _sentdm_webhook_record_id(ep)
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
            wid = _sentdm_webhook_record_id(kept)
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
            logger.info(
                "SentDM webhook reconcile (%s) skipped: JVAGENT_PUBLIC_BASE_URL is not set",
                reason,
            )
            return
        try:
            result = await self.reconcile_webhook_endpoint()
            if isinstance(result, dict) and result.get("status") == "ok":
                logger.info(
                    "SentDM webhook reconciled (%s): created=%s deleted=%s desired_url=%s",
                    reason,
                    result.get("created"),
                    result.get("deleted_webhook_ids"),
                    result.get("desired_url"),
                )
            elif isinstance(result, dict) and result.get("status") == "skipped":
                logger.info(
                    "SentDM webhook reconcile (%s) skipped: %s",
                    reason,
                    result.get("reason"),
                )
            else:
                logger.warning(
                    "SentDM webhook reconcile (%s) returned non-ok: %s", reason, result
                )
        except Exception as exc:
            logger.warning(
                "SentDM webhook reconcile (%s) failed: %s", reason, exc, exc_info=True
            )
