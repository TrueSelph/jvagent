"""HTTP endpoints for SentDMBroadcastAction.

Admin endpoints are scoped by ``action_id`` and require an authenticated admin
session. The public webhook endpoint is registered with SentDM at startup
(``reconcile_webhook_endpoint``); it is protected by an ``api_key`` query
parameter (jvspatial webhook middleware) AND verifies the SentDM
``X-Webhook-Signature`` HMAC against the signing secret SentDM returned when
the webhook was created.
"""

import hashlib
import hmac
import json
import logging
from collections import OrderedDict
from threading import Lock
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException, Request
from jvspatial.api import endpoint
from jvspatial.api.exceptions import ResourceNotFoundError
from jvspatial.exceptions import ValidationError

from .models import SentDMBroadcastRecord
from .sentdm_broadcast_action import SentDMBroadcastAction

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


def _verify_sentdm_signature(
    secret: str, raw_body: bytes, signature_header: Optional[str]
) -> bool:
    """Constant-time verify ``X-Webhook-Signature`` (HMAC-SHA256 hex)."""
    if not secret or not signature_header:
        return False
    sig = str(signature_header).strip()
    if sig.startswith("sha256="):
        sig = sig[len("sha256=") :]
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig.lower(), expected.lower())


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


@endpoint(
    "/actions/{action_id}/sentdm/broadcast",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["SentDM"],
    summary="Send a SentDM broadcast (POST /v3/messages)",
)
async def sentdm_broadcast(
    action_id: str,
    to: Any,
    template: Optional[Dict[str, Any]] = None,
    channels: Optional[List[str]] = None,
    parameters: Optional[Dict[str, Any]] = None,
    sandbox: Optional[bool] = None,
    idempotency_key: Optional[str] = None,
    profile_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Send a broadcast via the configured SentDM action.

    Body fields:

    - ``to`` (required): single phone number or list of E.164 numbers.
    - ``template`` (optional): ``{"id"?, "name"?, "parameters"?}``. Falls back
      to ``default_template_id`` / ``default_template_name`` on the action.
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
    "/actions/{action_id}/sentdm/messages/{message_id}",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["SentDM"],
    summary="Get the current status of a SentDM message",
)
async def sentdm_get_message(
    action_id: str,
    message_id: str,
    profile_id: Optional[str] = None,
) -> Dict[str, Any]:
    """``GET /v3/messages/{id}`` proxy."""
    action = await _get_sentdm_action(action_id)
    try:
        return await action.get_message_status(message_id, profile_id=profile_id)
    except httpx.HTTPStatusError as exc:
        raise _httpx_error_to_http(exc)


@endpoint(
    "/actions/{action_id}/sentdm/messages/{message_id}/activities",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["SentDM"],
    summary="Get the SentDM activity log for a message",
)
async def sentdm_get_message_activities(
    action_id: str,
    message_id: str,
    profile_id: Optional[str] = None,
) -> Dict[str, Any]:
    """``GET /v3/messages/{id}/activities`` proxy."""
    action = await _get_sentdm_action(action_id)
    try:
        return await action.get_message_activities(message_id, profile_id=profile_id)
    except httpx.HTTPStatusError as exc:
        raise _httpx_error_to_http(exc)


@endpoint(
    "/actions/{action_id}/sentdm/templates",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["SentDM"],
    summary="List SentDM templates",
)
async def sentdm_list_templates(
    action_id: str,
    page: Optional[int] = None,
    page_size: Optional[int] = None,
    search: Optional[str] = None,
    status: Optional[str] = None,
    category: Optional[str] = None,
    profile_id: Optional[str] = None,
) -> Dict[str, Any]:
    """``GET /v3/templates`` proxy with optional filters."""
    action = await _get_sentdm_action(action_id)
    try:
        return await action.list_templates(
            page=page,
            page_size=page_size,
            search=search,
            status=status,
            category=category,
            profile_id=profile_id,
        )
    except httpx.HTTPStatusError as exc:
        raise _httpx_error_to_http(exc)


@endpoint(
    "/actions/{action_id}/sentdm/status",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["SentDM"],
    summary="SentDM connection healthcheck",
)
async def sentdm_status(action_id: str) -> Dict[str, Any]:
    """Return :py:meth:`SentDMBroadcastAction.healthcheck` output."""
    action = await _get_sentdm_action(action_id)
    result = await action.healthcheck()
    if isinstance(result, dict):
        return result
    return {"healthy": bool(result)}


@endpoint(
    "/actions/{action_id}/sentdm/webhook/register",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["SentDM"],
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
    "/actions/{action_id}/sentdm/webhook",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["SentDM"],
    summary="Show the currently registered SentDM webhook URL",
)
async def sentdm_get_webhook(action_id: str) -> Dict[str, Any]:
    """Return the persisted webhook URL + SentDM webhook id (read-only).

    Does not contact SentDM. To force a reconcile, POST
    ``/actions/{action_id}/sentdm/webhook/register``.
    """
    action = await _get_sentdm_action(action_id)
    return {
        "configured": action.is_configured(),
        "webhook_url": action.webhook_url,
        "sentdm_webhook_id": action.sentdm_webhook_id,
        "has_signing_secret": bool((action.sentdm_webhook_secret or "").strip()),
        "event_types": list(action.webhook_event_types or []),
        "display_name": action.webhook_display_name,
    }


@endpoint(
    "/actions/{action_id}/sentdm/broadcasts",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["SentDM"],
    summary="List persisted SentDM broadcast records",
)
async def sentdm_list_broadcasts(
    action_id: str,
    status: Optional[str] = None,
    to: Optional[str] = None,
    sentdm_message_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> Dict[str, Any]:
    """Return broadcast records persisted for this action.

    Filters:

    - ``status`` (e.g. ``delivered``, ``failed``)
    - ``to`` — exact recipient match (E.164)
    - ``sentdm_message_id`` — direct id lookup

    Pagination is client-side (in-memory) for now — fine for the volumes
    these records target.
    """
    action = await _get_sentdm_action(action_id)

    query: Dict[str, Any] = {"action_id": str(action.id)}
    if status:
        query["status"] = status.strip().lower()
    if to:
        query["to"] = to.strip()
    if sentdm_message_id:
        query["sentdm_message_id"] = sentdm_message_id.strip()

    try:
        records = await SentDMBroadcastRecord.find(**query)
    except Exception as exc:
        logger.warning("SentDM list broadcasts query failed: %s", exc)
        raise HTTPException(status_code=500, detail="broadcast record query failed")

    records_sorted = sorted(
        records,
        key=lambda r: getattr(r, "created_at", None) or 0,
        reverse=True,
    )

    safe_page = max(1, int(page or 1))
    safe_page_size = max(1, min(int(page_size or 50), 500))
    start = (safe_page - 1) * safe_page_size
    end = start + safe_page_size
    page_records = records_sorted[start:end]

    return {
        "total": len(records_sorted),
        "page": safe_page,
        "page_size": safe_page_size,
        "records": [_record_to_dict(r) for r in page_records],
    }


@endpoint(
    "/actions/{action_id}/sentdm/broadcasts/{record_id}",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["SentDM"],
    summary="Show a single SentDM broadcast record (with full event history)",
)
async def sentdm_get_broadcast(action_id: str, record_id: str) -> Dict[str, Any]:
    """Return one broadcast record by its node id, including the full audit log."""
    action = await _get_sentdm_action(action_id)
    record = await SentDMBroadcastRecord.get(record_id)
    if record is None or not isinstance(record, SentDMBroadcastRecord):
        raise ResourceNotFoundError(f"Broadcast record not found: {record_id}")
    if str(getattr(record, "action_id", "")) != str(action.id):
        raise HTTPException(
            status_code=404,
            detail=(
                f"Broadcast record {record_id} does not belong to action {action_id}"
            ),
        )
    return _record_to_dict(record, include_events=True)


@endpoint(
    "/actions/{action_id}/sentdm/broadcasts/{record_id}/refresh",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["SentDM"],
    summary="Re-fetch a broadcast's status from SentDM and update the record",
)
async def sentdm_refresh_broadcast(action_id: str, record_id: str) -> Dict[str, Any]:
    """Force-refresh a record's status from SentDM (recovers from missed webhooks)."""
    action = await _get_sentdm_action(action_id)
    try:
        return await action.refresh_record(record_id)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except httpx.HTTPStatusError as exc:
        raise _httpx_error_to_http(exc)


def _record_to_dict(
    record: SentDMBroadcastRecord, *, include_events: bool = False
) -> Dict[str, Any]:
    """Serialize a broadcast record for HTTP responses."""

    def _iso(value: Any) -> Optional[str]:
        try:
            return value.isoformat() if value is not None else None
        except AttributeError:
            return str(value) if value is not None else None

    data: Dict[str, Any] = {
        "id": record.id,
        "action_id": record.action_id,
        "agent_id": record.agent_id,
        "sentdm_message_id": record.sentdm_message_id,
        "to": record.to,
        "channel": record.channel,
        "template_id": record.template_id,
        "template_name": record.template_name,
        "parameters": record.parameters,
        "idempotency_key": record.idempotency_key,
        "profile_id": record.profile_id,
        "sandbox": record.sandbox,
        "status": record.status,
        "last_event_field": record.last_event_field,
        "last_status_at": _iso(record.last_status_at),
        "error": record.error,
        "created_at": _iso(record.created_at),
        "updated_at": _iso(record.updated_at),
        "event_count": len(record.events or []),
    }
    if include_events:
        data["events"] = list(record.events or [])
        data["last_event_payload"] = record.last_event_payload
    return data


@endpoint(
    "/sentdm/webhook/{action_id}",
    methods=["POST"],
    webhook=True,
    auth=False,
    webhook_auth="api_key",
    tags=["SentDM"],
    summary="Inbound SentDM webhook (delivery / template events)",
)
async def sentdm_webhook_receive(request: Request, action_id: str) -> Dict[str, Any]:
    """Receive a signed event from SentDM.

    Performs:
    1. ``X-Webhook-Signature`` HMAC-SHA256 verification with the stored
       signing secret.
    2. ``X-Webhook-ID`` de-duplication via an in-memory LRU.
    3. Logging of the event for later inspection. Downstream dispatch hooks
       can be added without changing the wire contract.
    """
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

    signature = request.headers.get("x-webhook-signature")
    if not _verify_sentdm_signature(secret, raw_body, signature):
        raise HTTPException(status_code=401, detail="Invalid X-Webhook-Signature")

    webhook_id = request.headers.get("x-webhook-id") or ""
    if webhook_id and not _remember_webhook_id(webhook_id):
        return {"status": "duplicate", "webhook_id": webhook_id}

    payload: Any = getattr(request.state, "parsed_payload", None)
    if payload is None:
        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning("SentDM webhook JSON parse error: %s", exc)
            raise HTTPException(status_code=400, detail="Invalid JSON body")

    field = ""
    event_payload: Any = None
    if isinstance(payload, dict):
        field = str(payload.get("field") or "")
        event_payload = payload.get("payload")

    sentdm_message_id = _extract_message_id_from_event(event_payload)

    logger.info(
        "SentDM webhook received: action=%s webhook_id=%s field=%s message_id=%s",
        action_id,
        webhook_id or "(none)",
        field or "(unknown)",
        sentdm_message_id or "(none)",
    )
    logger.debug("SentDM webhook payload (action=%s): %r", action_id, event_payload)

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
                    record, field, event_payload
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
