"""HTTP endpoints for SentDMBroadcastAction.

Admin routes are scoped by ``action_id`` and require an authenticated admin
session. The public webhook route is registered with SentDM at startup
(``reconcile_webhook_endpoint``); it is protected by an ``api_key`` query
parameter (jvspatial webhook middleware) and verifies the SentDM
``X-Webhook-Signature`` per Sent's scheme (``v1,{base64}`` using
``{x-webhook-id}.{x-webhook-timestamp}.{raw_body}`` and a ``whsec_`` signing
secret), with a legacy fallback for older hex digests over the raw body only.
"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
import time
from collections import OrderedDict
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx
from fastapi import HTTPException, Request
from jvspatial.api import endpoint
from jvspatial.api.exceptions import ResourceNotFoundError
from pydantic import Field

from .sentdm_broadcast_action import (
    _DEFAULT_WEBHOOK_EVENT_FILTERS,
    SentDMBroadcastAction,
)

logger = logging.getLogger(__name__)

_WEBHOOK_ID_CACHE_MAX = 1024
_seen_webhook_ids: "OrderedDict[str, None]" = OrderedDict()
_seen_webhook_ids_lock = Lock()


def _remember_webhook_id(webhook_id: str) -> bool:
    """Return True if ``webhook_id`` is new (and remember it), False if duplicate."""
    key = (webhook_id or "").strip()
    if not key:
        return True
    with _seen_webhook_ids_lock:
        if key in _seen_webhook_ids:
            _seen_webhook_ids.move_to_end(key)
            return False
        _seen_webhook_ids[key] = None
        while len(_seen_webhook_ids) > _WEBHOOK_ID_CACHE_MAX:
            _seen_webhook_ids.popitem(last=False)
    return True


def _sentdm_decode_signing_secret(secret: str) -> Optional[bytes]:
    """Decode Sent signing secret (``whsec_`` + base64) to raw HMAC key bytes."""
    s = (secret or "").strip()
    if not s:
        return None
    material = s[6:] if s.startswith("whsec_") else s
    pad = (-len(material)) % 4
    padded = material + ("=" * pad)
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            out = decoder(padded)
        except (binascii.Error, ValueError):
            continue
        if out:
            return out
    if s.startswith("whsec_"):
        return None
    return s.encode("utf-8")


def _sentdm_timestamp_acceptable(timestamp_header: str, *, max_skew: int = 600) -> bool:
    """Reject wildly stale ``x-webhook-timestamp`` values (replay mitigation)."""
    raw = (timestamp_header or "").strip()
    if not raw:
        return True
    try:
        ts = int(raw, 10)
    except ValueError:
        return False
    return abs(int(time.time()) - ts) <= max_skew


def _b64decode_signature_blob(sig_b64: str) -> Optional[bytes]:
    s = (sig_b64 or "").strip()
    if not s:
        return None
    pad = (-len(s)) % 4
    padded = s + ("=" * pad)
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            return decoder(padded)
        except (binascii.Error, ValueError):
            continue
    return None


def _verify_sentdm_signature_v1(
    secret: str,
    raw_body: bytes,
    sig_b64: str,
    webhook_id: str,
    timestamp: str,
) -> bool:
    """Sent documented scheme: HMAC-SHA256(key, f'{id}.{ts}.{body}') → base64, header ``v1,…``."""
    key = _sentdm_decode_signing_secret(secret)
    if key is None:
        return False
    wid = (webhook_id or "").strip()
    ts = (timestamp or "").strip()
    if not wid or not ts:
        return False
    if not _sentdm_timestamp_acceptable(ts):
        return False
    try:
        body_text = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        body_text = raw_body.decode("utf-8", errors="surrogateescape")
    signed = f"{wid}.{ts}.{body_text}"
    digest = hmac.new(key, signed.encode("utf-8"), hashlib.sha256).digest()
    provided = _b64decode_signature_blob(sig_b64)
    if not provided:
        return False
    return hmac.compare_digest(digest, provided)


def _verify_sentdm_signature_legacy(
    secret: str, raw_body: bytes, signature_header: str
) -> bool:
    """Legacy: hex HMAC-SHA256(secret utf-8, raw_body) or ``sha256=`` hex prefix."""
    sig = str(signature_header).strip()
    if sig.startswith("sha256="):
        sig = sig[len("sha256=") :].strip()
    if len(sig) != 64 or any(c not in "0123456789abcdefABCDEF" for c in sig):
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig.lower(), expected.lower())


def _verify_sentdm_signature(
    secret: str,
    raw_body: bytes,
    signature_header: Optional[str],
    *,
    webhook_id: str,
    timestamp: str,
) -> bool:
    """Verify ``X-Webhook-Signature`` (Sent ``v1,`` base64, else legacy hex)."""
    if not secret or not signature_header:
        return False
    sh = str(signature_header).strip()
    if sh.lower().startswith("v1,"):
        return _verify_sentdm_signature_v1(
            secret, raw_body, sh[3:].strip(), webhook_id, timestamp
        )
    return _verify_sentdm_signature_legacy(secret, raw_body, sh)


async def _get_sentdm_action(action_id: str) -> SentDMBroadcastAction:
    action = await SentDMBroadcastAction.get(action_id)
    if not action or not isinstance(action, SentDMBroadcastAction):
        raise ResourceNotFoundError(f"SentDM broadcast action not found: {action_id}")
    return action


def _httpx_error_to_http(exc: httpx.HTTPStatusError) -> HTTPException:
    """Convert an upstream SentDM HTTPStatusError into a FastAPI HTTPException."""
    try:
        body: Any = exc.response.json()
    except ValueError:
        body = exc.response.text
    return HTTPException(
        status_code=exc.response.status_code,
        detail={"upstream": body, "message": str(exc)},
    )


def _extract_message_id_from_event(event_payload: Any) -> str:
    """Pull a SentDM message id out of a webhook event payload.

    SentDM's webhook event shape isn't fully nailed down in the public docs,
    so we probe a handful of likely paths and return the first non-empty
    string. Returns ``""`` when nothing was found.
    """
    if not isinstance(event_payload, dict):
        return ""
    direct_keys = ("id", "message_id", "messageId")
    for key in direct_keys:
        value = event_payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for nested_key in ("message", "data"):
        nested = event_payload.get(nested_key)
        if isinstance(nested, dict):
            for key in direct_keys:
                value = nested.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return ""


def _normalize_sentdm_webhook_envelope(payload: Any) -> Tuple[str, Dict[str, Any]]:
    """Normalize Sent webhook JSON to ``(field, fold)`` for derive + record update.

    Handles the documented envelope ``{field, sub_type, timestamp, payload}`` and a
    dashboard-style wrapper ``{eventType, eventData: {...}}``.
    """
    if not isinstance(payload, dict):
        return "", {}
    root = payload
    env: Dict[str, Any] = root
    nested = root.get("eventData")
    if isinstance(nested, dict) and (
        isinstance(nested.get("payload"), dict) or nested.get("field") is not None
    ):
        env = nested
    field = str(env.get("field") or root.get("field") or "")
    sub_raw = env.get("sub_type")
    if not (isinstance(sub_raw, str) and sub_raw.strip()):
        sub_raw = root.get("eventType") or root.get("event_type")
    sub_type = str(sub_raw).strip() if isinstance(sub_raw, str) else ""
    inner = env.get("payload")
    if not isinstance(inner, dict):
        inner = {}
    fold: Dict[str, Any] = dict(inner)
    if sub_type:
        fold["sub_type"] = sub_type
        fold["event"] = sub_type
    if (
        "message_id" not in fold
        and isinstance(fold.get("id"), str)
        and fold["id"].strip()
    ):
        fold["message_id"] = fold["id"].strip()
    return field, fold


@endpoint(
    "/actions/{action_id}/broadcast",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Broadcast"],
    summary="Send a SentDM broadcast (POST /v3/messages)",
)
async def sentdm_broadcast(
    action_id: str,
    to: Union[str, List[str]] = Field(
        ...,
        description="Recipient E.164 number(s). Pass a string or an array of strings.",
        examples=["5920000000"],
    ),
    template: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Template selector: ``id`` and/or ``name``. Optional nested "
            "``parameters`` are merged under top-level ``parameters``."
        ),
        examples=[{"id": "f70c78f8-4be0-49eb-88e2-cd7aa9a7cef9"}],
    ),
    channels: Optional[List[str]] = Field(
        None,
        description=(
            "Channels to try in order, e.g. ``sms``, ``whatsapp``, ``rcs``. "
            "Defaults to the action's ``default_channels``."
        ),
    ),
    parameters: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Template variables (Sent placeholder names → values), merged on top "
            "of any ``template.parameters``."
        ),
        examples=[{"var_1": "123456"}],
    ),
    sandbox: Optional[bool] = Field(
        None,
        description="When true, Sent validates the payload without delivering to carriers.",
        examples=[True],
    ),
    idempotency_key: Optional[str] = Field(
        default=None,
        description="Forwarded as the ``idempotency-key`` header on the SentDM request.",
    ),
    profile_id: Optional[str] = Field(
        default=None,
        description="Forwarded as ``x-profile-id`` when using a Sent child profile.",
    ),
) -> Dict[str, Any]:
    """Send a broadcast via the configured SentDM action.

    **Example body**

    ::

        {
          "to": "5920000000",
          "template": {"id": "f70c78f8-4be0-49eb-88e2-cd7aa9a7cef9"},
          "parameters": {"var_1": "123456"},
          "sandbox": true
        }

    **Fields**

    - ``to`` (required): one E.164 number or a list of numbers.
    - ``template`` (optional): ``{"id"?, "name"?, "parameters"?}``. Falls back to
      ``default_template_id`` / ``default_template_name`` on the action.
    - ``channels`` (optional): defaults to ``default_channels``.
    - ``parameters`` (optional): merged on top of ``template.parameters``.
    - ``sandbox`` (optional): per-call override.
    - ``idempotency_key`` (optional): forwarded as ``idempotency-key`` header.
    - ``profile_id`` (optional): forwarded as ``x-profile-id`` header.
    """
    action = await _get_sentdm_action(action_id)
    if not action.is_configured():
        raise HTTPException(
            status_code=400,
            detail={
                "message": "SentDM action is not configured",
                "issues": action._config_issues(),
            },
        )

    if isinstance(to, str):
        recipients: List[str] = [to]
    elif isinstance(to, list):
        recipients = [str(t) for t in to if t is not None and str(t).strip()]
    else:
        raise HTTPException(
            status_code=400, detail="'to' must be a string or list of strings"
        )
    if not recipients:
        raise HTTPException(
            status_code=400, detail="'to' must contain at least one recipient"
        )

    try:
        return await action.send_broadcast(
            recipients,
            template=template,
            channels=channels,
            parameters=parameters,
            sandbox=sandbox,
            idempotency_key=idempotency_key,
            profile_id=profile_id,
        )
    except httpx.HTTPStatusError as exc:
        raise _httpx_error_to_http(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@endpoint(
    "/actions/{action_id}/webhook/register",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Webhooks"],
    summary="Force a SentDM webhook reconcile",
)
async def sentdm_register_webhook(action_id: str) -> Dict[str, Any]:
    """Manually trigger :py:meth:`SentDMBroadcastAction.reconcile_webhook_endpoint`."""
    action = await _get_sentdm_action(action_id)
    try:
        return await action.reconcile_webhook_endpoint()
    except httpx.HTTPStatusError as exc:
        raise _httpx_error_to_http(exc)


@endpoint(
    "/actions/{action_id}/webhook",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Webhooks"],
    summary="Show the currently registered SentDM webhook URL",
)
async def sentdm_get_webhook(action_id: str) -> Dict[str, Any]:
    """Return the persisted webhook URL + SentDM webhook id (read-only).

    Does not contact SentDM. To force a reconcile, POST
    ``/actions/{action_id}/webhook/register``.
    """
    action = await _get_sentdm_action(action_id)
    eff = (
        {k: list(v) for k, v in _DEFAULT_WEBHOOK_EVENT_FILTERS.items()}
        if action.webhook_event_filters is None
        else dict(action.webhook_event_filters)
    )
    return {
        "configured": action.is_configured(),
        "webhook_url": action.webhook_url,
        "sentdm_webhook_id": action.sentdm_webhook_id,
        "has_signing_secret": bool((action.sentdm_webhook_secret or "").strip()),
        "event_types": list(action.webhook_event_types or []),
        "webhook_event_filters": action.webhook_event_filters,
        "event_filters_effective": eff,
        "display_name": action.webhook_display_name,
    }


@endpoint(
    "/webhook/{action_id}",
    methods=["POST"],
    webhook=True,
    auth=False,
    webhook_auth="api_key",
    tags=["Webhooks"],
    summary="Inbound SentDM webhook (delivery / template events)",
)
async def sentdm_webhook_receive(request: Request, action_id: str) -> Dict[str, Any]:
    """Receive a signed event from SentDM.

    Performs:
    1. ``X-Webhook-Signature`` verification (Sent ``v1,{base64}`` scheme, or legacy
       hex digest over the raw body).
    2. ``X-Webhook-ID`` de-duplication via an in-memory LRU.
    3. Logging of the event for later inspection. Downstream dispatch hooks
       can be added without changing the wire contract.
    """
    logger.info(
        "SentDM webhook route reached (jvspatial webhook api_key auth passed): "
        "action_id=%s",
        action_id,
    )
    action = await _get_sentdm_action(action_id)
    secret = (action.sentdm_webhook_secret or "").strip()
    if not secret:
        logger.warning(
            "SentDM webhook for action %s arrived but no signing secret is on record",
            action_id,
        )
        raise HTTPException(
            status_code=500,
            detail="SentDM webhook signing secret is not configured on this action",
        )

    raw_body: bytes = getattr(request.state, "raw_body", b"") or b""
    if not raw_body:
        raw_body = await request.body()

    webhook_id = (request.headers.get("x-webhook-id") or "").strip()
    timestamp_hdr = (request.headers.get("x-webhook-timestamp") or "").strip()
    signature = request.headers.get("x-webhook-signature")
    if not _verify_sentdm_signature(
        secret,
        raw_body,
        signature,
        webhook_id=webhook_id,
        timestamp=timestamp_hdr,
    ):
        sig_desc = "absent"
        if signature:
            s = str(signature).strip()
            sig_desc = (
                f"len={len(s)} v1_prefix={s.lower().startswith('v1,')} "
                f"sha256_prefix={s.lower().startswith('sha256=')}"
            )
        logger.warning(
            "SentDM webhook HMAC verification failed: action_id=%s raw_body_bytes=%s "
            "signature_header=%s x_webhook_id_len=%s x_webhook_timestamp=%r",
            action_id,
            len(raw_body),
            sig_desc,
            len(webhook_id),
            timestamp_hdr[:32] if timestamp_hdr else "",
        )
        raise HTTPException(status_code=401, detail="Invalid X-Webhook-Signature")

    if webhook_id and not _remember_webhook_id(webhook_id):
        return {"status": "duplicate", "webhook_id": webhook_id}

    payload: Any = getattr(request.state, "parsed_payload", None)
    if payload is None:
        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning("SentDM webhook JSON parse error: %s", exc)
            raise HTTPException(status_code=400, detail="Invalid JSON body")

    field, fold = _normalize_sentdm_webhook_envelope(payload)
    sentdm_message_id = _extract_message_id_from_event(fold)

    logger.info(
        "SentDM webhook received: action=%s webhook_id=%s field=%s message_id=%s",
        action_id,
        webhook_id or "(none)",
        field or "(unknown)",
        sentdm_message_id or "(none)",
    )
    logger.debug("SentDM webhook fold (action=%s): %r", action_id, fold)

    record_id: Optional[str] = None
    record_status: Optional[str] = None
    if sentdm_message_id:
        try:
            record = await action._record_for_message_id(sentdm_message_id)
        except Exception as exc:  # pragma: no cover - DB hiccup, best effort
            logger.warning(
                "SentDM webhook (action=%s) record lookup failed: %s",
                action_id,
                exc,
            )
            record = None
        if record is not None:
            try:
                updated = await action._apply_webhook_event_to_record(
                    record, field, fold
                )
                record_id = updated.id
                record_status = updated.status
            except Exception as exc:  # pragma: no cover - best effort
                logger.warning(
                    "SentDM webhook (action=%s) record update failed for %s: %s",
                    action_id,
                    sentdm_message_id,
                    exc,
                )
        else:
            logger.info(
                "SentDM webhook (action=%s): no local record for message_id=%s "
                "(broadcast may have been sent elsewhere or persist_records=False)",
                action_id,
                sentdm_message_id,
            )

    return {
        "status": "received",
        "webhook_id": webhook_id or None,
        "field": field or None,
        "sentdm_message_id": sentdm_message_id or None,
        "record_id": record_id,
        "record_status": record_status,
    }
