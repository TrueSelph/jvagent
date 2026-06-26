"""WhatsApp Action Endpoints."""

import asyncio
import base64
import binascii
import html
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

import aiohttp
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from jvspatial import create_task
from jvspatial.api import endpoint
from jvspatial.api.constants import APIRoutes
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError
from jvspatial.exceptions import DatabaseError, ValidationError

from jvagent.action.access_control.access_control_action import log_access_denied
from jvagent.action.utils.meta_webhook import verify_meta_webhook_signature
from jvagent.core.agent import Agent

from .utils.endpoint_helpers import (
    _batch_manager,
    _build_utterance_with_quoted_context,
    _clear_whatsapp_typing,
    _handle_media_message,
    _handle_voice_message,
    _process_interaction_async,
    get_whatsapp_action,
    is_directed_message,
    normalize_result,
)

logger = logging.getLogger(__name__)

_META_BRIDGE_NA = {
    "status": "not_applicable",
    "reason": "meta_cloud_api",
    "message": "Bridge session/QR endpoints are not used with the meta Cloud API provider.",
    "ok": True,
}


def _bridge_provider_response(whatsapp_action: Any) -> Optional[Dict[str, Any]]:
    if whatsapp_action.is_meta_provider():
        return dict(_META_BRIDGE_NA)
    return None


async def _agent_and_whatsapp_action_for_webhook(agent_id: str) -> tuple[Any, Any]:
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
        )
    whatsapp_action = await agent.get_action_by_type("WhatsAppAction")
    if not whatsapp_action:
        raise ResourceNotFoundError(
            message="Action with label 'WhatsAppAction' not found",
            details={"agent_id": agent_id},
        )
    return agent, whatsapp_action


async def _authenticate_bridge_webhook_api_key(request: Request) -> None:
    """Require jvagent webhook API key for bridge providers (wwebjs, etc.)."""
    from jvspatial.api.context import get_current_server
    from jvspatial.api.integrations.webhooks.webhook_auth import (
        authenticate_webhook_api_key,
    )

    await authenticate_webhook_api_key(
        request, "api_key", webhook_config=None, server=get_current_server()
    )


# --- Browser connection / QR page (public link, unguessable action_id) ---

_WA_LINK_THEMES = {
    "qr": {
        "primary": "#25D366",
        "icon_bg": "rgba(37, 211, 102, 0.12)",
        "badge_bg": "rgba(37, 211, 102, 0.15)",
    },
    "success": {
        "primary": "#4CAF50",
        "icon_bg": "rgba(76, 175, 80, 0.1)",
        "badge_bg": "rgba(76, 175, 80, 0.15)",
    },
}


def _wa_link_page_html(
    *,
    theme: str,
    title: str,
    icon_svg: str,
    body_inner: str,
    head_extra: str = "",
) -> str:
    t = _WA_LINK_THEMES[theme]
    primary = t["primary"]
    icon_bg = t["icon_bg"]
    badge_bg = t["badge_bg"]
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        {head_extra}
        <title>{title}</title>
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600&display=swap" rel="stylesheet">
        <style>
            :root {{
                --primary: {primary};
                --bg: #0f172a;
                --card-bg: rgba(30, 41, 59, 0.7);
                --text: #f8fafc;
                --text-muted: #94a3b8;
            }}
            body {{
                font-family: 'Outfit', sans-serif;
                background-color: var(--bg);
                color: var(--text);
                display: flex;
                align-items: center;
                justify-content: center;
                min-height: 100vh;
                margin: 0;
            }}
            .container {{
                background: var(--card-bg);
                backdrop-filter: blur(12px);
                -webkit-backdrop-filter: blur(12px);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 24px;
                padding: 3rem;
                max-width: 480px;
                width: 90%;
                text-align: center;
                box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
                animation: slideUp 0.6s cubic-bezier(0.16, 1, 0.3, 1);
            }}
            @keyframes slideUp {{
                from {{ opacity: 0; transform: translateY(20px); }}
                to {{ opacity: 1; transform: translateY(0); }}
            }}
            .icon-circle {{
                width: 80px;
                height: 80px;
                background: {icon_bg};
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                margin: 0 auto 1.5rem;
                border: 2px solid var(--primary);
            }}
            h2 {{
                color: var(--primary);
                font-weight: 600;
                margin-top: 0;
                font-size: 1.75rem;
            }}
            .action-badge {{
                display: inline-block;
                padding: 4px 12px;
                background: {badge_bg};
                color: var(--primary);
                border-radius: 8px;
                font-size: 0.85rem;
                font-weight: 600;
                letter-spacing: 0.05em;
                text-transform: uppercase;
                margin-bottom: 1rem;
            }}
            .agent-info {{
                margin: 1.5rem 0 2rem;
                padding: 1.5rem;
                background: rgba(255, 255, 255, 0.03);
                border-radius: 16px;
                border: 1px solid rgba(255, 255, 255, 0.05);
            }}
            .agent-name {{
                font-size: 1.25rem;
                font-weight: 600;
                display: block;
                margin-bottom: 0.5rem;
            }}
            .agent-desc {{
                font-size: 0.95rem;
                color: var(--text-muted);
                line-height: 1.5;
            }}
            .close-text {{
                margin-top: 1rem;
                font-size: 0.9rem;
                opacity: 0.8;
            }}
            .qr-image {{
                max-width: 280px;
                width: 100%;
                height: auto;
                border-radius: 12px;
                margin: 0.5rem 0 1rem;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="icon-circle">
                {icon_svg}
            </div>
            {body_inner}
        </div>
    </body>
    </html>
    """


def _wa_error_html(message: str, status_code: int = 400) -> HTMLResponse:
    body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Error</title></head>
<body style="font-family:system-ui,sans-serif;padding:2rem;max-width:40rem">
<h1>Error</h1><p>{html.escape(message)}</p>
</body></html>"""
    return HTMLResponse(content=body, status_code=status_code)


def _is_whatsapp_session_connected(st: Any) -> bool:
    if not isinstance(st, dict):
        return False
    s = st.get("status")
    if s is None or s == "":
        return False
    return str(s).upper() == "CONNECTED"


_WA_QR_IMAGE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


async def _qr_result_to_png_bytes(qr: Any) -> Optional[bytes]:
    """Derive PNG bytes from provider ``qrcode()`` result (raw, base64, data URI, or image URL)."""
    if not isinstance(qr, dict):
        return None
    raw = qr.get("raw")
    if isinstance(raw, (bytes, bytearray)) and raw:
        return bytes(raw)
    for key in ("qrcode_base64", "qrcode"):
        val = qr.get(key)
        if not isinstance(val, str):
            continue
        v = val.strip()
        if not v:
            continue
        if v.startswith("http://") or v.startswith("https://"):
            try:
                timeout = aiohttp.ClientTimeout(total=15)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(v) as resp:
                        if resp.status >= 400:
                            return None
                        data = await resp.read()
                        return data if data else None
            except Exception as e:
                logger.debug("QR image URL fetch failed: %s", e)
                return None
        if v.startswith("data:"):
            m = re.match(r"data:image/[^;]+;base64,(.+)", v, re.IGNORECASE | re.DOTALL)
            if m:
                try:
                    return base64.b64decode(m.group(1).strip())
                except (ValueError, binascii.Error):
                    return None
        try:
            return base64.b64decode(v)
        except (ValueError, binascii.Error):
            continue
    return None


@endpoint(
    "/whatsapp/{action_id}/qr",
    methods=["GET"],
    auth=False,
    tags=["WhatsApp"],
    summary="WhatsApp QR code image (PNG)",
)
async def whatsapp_connection_qr(action_id: str) -> Response:
    """Return the current session QR as PNG (proxied from the configured provider)."""
    try:
        wa_action = await get_whatsapp_action(action_id)
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="WhatsApp action not found")

    bridge_na = _bridge_provider_response(wa_action)
    if bridge_na:
        raise HTTPException(status_code=400, detail=bridge_na["message"])

    if not wa_action.is_configured():
        raise HTTPException(
            status_code=400, detail="WhatsApp is not configured for this action"
        )

    try:
        wa = await wa_action.api()
    except ValidationError:
        raise HTTPException(
            status_code=400, detail="WhatsApp is not available for this action"
        )

    try:
        qr = await wa.qrcode()
    except Exception as e:
        logger.warning("WhatsApp qrcode failed for action_id=%s: %s", action_id, e)
        raise HTTPException(status_code=404, detail="No QR code available")

    png = await _qr_result_to_png_bytes(qr)
    if not png:
        raise HTTPException(status_code=404, detail="No QR code available")

    return Response(
        content=png,
        media_type="image/png",
        headers=dict(_WA_QR_IMAGE_HEADERS),
    )


@endpoint(
    "/whatsapp/{action_id}",
    methods=["GET"],
    auth=False,
    tags=["WhatsApp"],
    summary="WhatsApp connection / QR (browser)",
)
async def whatsapp_connection_page(action_id: str) -> HTMLResponse:
    """Show a human-readable page: connected state, or a QR to scan, or a clear error.

    Public link (no auth) — the ``action_id`` should be unguessable (UUID), same as
    ``/api/google/{action_id}`` for OAuth.
    """
    try:
        wa_action = await get_whatsapp_action(action_id)
    except ResourceNotFoundError:
        return _wa_error_html(
            f"WhatsApp action not found: {action_id}", status_code=404
        )

    if wa_action.is_meta_provider() and wa_action.is_configured():
        body_inner = """
            <div class="status-badge">Cloud API</div>
            <h1>WhatsApp Cloud API</h1>
            <p class="subtitle">This agent uses Meta's WhatsApp Cloud API. No QR scan is required.</p>
            <p class="hint">Configure webhooks in Meta App Dashboard → WhatsApp → Configuration.</p>
        """
        icon_svg = '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>'
        content = _wa_link_page_html(
            theme="success",
            title="WhatsApp Cloud API",
            icon_svg=icon_svg,
            body_inner=body_inner,
        )
        return HTMLResponse(content=content, status_code=200)

    if not wa_action.is_configured():
        err = "WhatsApp is not configured for this action. " + "; ".join(
            wa_action._config_issues()
        )
        return _wa_error_html(err, status_code=400)

    try:
        wa = await wa_action.api()
    except ValidationError as e:
        logger.debug("WhatsApp API unavailable for action_id=%s: %s", action_id, e)
        return _wa_error_html("WhatsApp is not available for this action yet.", 400)

    try:
        st = await wa.status()
    except Exception as e:
        logger.warning("WhatsApp status failed for action_id=%s: %s", action_id, e)
        st = {}

    agent = await wa_action.get_agent()
    agent_name = "Agent"
    agent_description = ""
    if agent:
        agent_name = getattr(agent, "alias", None) or getattr(agent, "name", "Agent")
        agent_description = getattr(agent, "description", "")
    safe_name = html.escape(str(agent_name))
    desc_html = (
        f'<p class="agent-desc">{html.escape(str(agent_description))}</p>'
        if agent_description
        else ""
    )

    if _is_whatsapp_session_connected(st):
        icon_svg = """
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="color: var(--primary)">
                    <polyline points="20 6 9 17 4 12"></polyline>
                </svg>
        """
        body_inner = f"""
            <div class="action-badge">WhatsApp is connected</div>
            <h2>All set</h2>
            <p style="color: var(--text-muted)">This agent can send and receive WhatsApp messages.</p>
            <div class="agent-info">
                <span class="agent-name">{safe_name}</span>
                {desc_html}
            </div>
            <p class="close-text" style="color: var(--text-muted)">You can close this window.</p>
        """
        content = _wa_link_page_html(
            theme="success",
            title="WhatsApp connected",
            icon_svg=icon_svg,
            body_inner=body_inner,
        )
        return HTMLResponse(content=content, status_code=200)

    prefix = str(APIRoutes.PREFIX).rstrip("/")
    qr_src = f"{prefix}/whatsapp/{action_id}/qr"
    src_escaped = html.escape(qr_src, quote=True)
    icon_svg = """
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color: var(--primary)">
                    <rect x="3" y="3" width="7" height="7" rx="1"></rect>
                    <rect x="14" y="3" width="7" height="7" rx="1"></rect>
                    <rect x="3" y="14" width="7" height="7" rx="1"></rect>
                    <path d="M14 14h1v1h-1v-1z M17 14h1v1h-1v-1z M14 17h1v1h-1v-1z M17 17h1v1h-1v-1z M14 20h1v1h-1v-1z M17 20h1v1h-1v-1z"></path>
                </svg>
    """
    body_inner = f"""
        <h2>Scan to connect</h2>
        <p style="color: var(--text-muted)">Open WhatsApp on your phone → Linked devices → Link a device, then scan this code.</p>
        <div class="agent-info">
            <span class="agent-name">{safe_name}</span>
            {desc_html}
        </div>
        <img class="qr-image" src="{src_escaped}" width="280" height="280" alt="WhatsApp QR code" />
        <p class="close-text" style="color: var(--text-muted)">If the code does not appear, refresh or ensure the WhatsApp bridge is running. This page reloads every 15 seconds; after you scan, you should see a connected message.</p>
    """
    content = _wa_link_page_html(
        theme="qr",
        title="WhatsApp QR",
        icon_svg=icon_svg,
        body_inner=body_inner,
        head_extra='<meta http-equiv="refresh" content="15">',
    )
    return HTMLResponse(content=content, status_code=200)


@endpoint(
    "/whatsapp/interact/webhook/{agent_id}",
    methods=["GET"],
    webhook=True,
    auth=False,
    tags=["WhatsApp"],
    summary="Meta Cloud API webhook: GET hub challenge (subscription verify)",
)
async def whatsapp_interact_webhook_verify(request: Request, agent_id: str) -> Any:
    """Meta webhook verification (hub.challenge) for meta provider."""
    _, whatsapp_action = await _agent_and_whatsapp_action_for_webhook(agent_id)
    if not whatsapp_action.is_meta_provider():
        raise HTTPException(
            status_code=403, detail="Webhook verify only applies to meta provider"
        )
    params = getattr(request.state, "parsed_payload", None)
    if not isinstance(params, dict):
        params = dict(request.query_params)
    challenge = whatsapp_action.parse_webhook_verify(params)
    if isinstance(challenge, dict):
        raise HTTPException(status_code=403, detail="Webhook verification failed")
    return PlainTextResponse(str(challenge), media_type="text/plain")


@endpoint(
    "/whatsapp/interact/webhook/{agent_id}",
    methods=["POST"],
    webhook=True,
    auth=False,
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="received"),
            "response": ResponseField(
                field_type=Optional[str], example="Hello!", default=None
            ),
        }
    ),
)
async def whatsapp_interact(request: Request, agent_id: str) -> Dict[str, Any]:
    """WhatsApp Interact Webhook.

    Meta Cloud API POST uses ``X-Hub-Signature-256`` (no ``api_key``). Bridge
    providers must pass ``?api_key=`` or ``X-API-Key``; validated in-handler.

    AWS Lambda compatibility: In serverless mode, the webhook typically awaits the full
    interaction (including response generation and WhatsApp send) before returning, so work
    completes before the runtime freezes. jvspatial webhook middleware also avoids unsafe
    fire-and-forget patterns when serverless.

    On long-running servers (not serverless: SERVERLESS_MODE=false or unset on a non-serverless
    platform), this handler may return early and finish work via a background task.

    Args:
        request: FastAPI request object
        agent_id: Agent ID from URL path

    Returns:
        Dict containing status and optional response message

    Raises:
        ResourceNotFoundError: If agent or action not found
        HTTPException: For validation errors
    """
    try:
        agent, whatsapp_action = await _agent_and_whatsapp_action_for_webhook(agent_id)

        if whatsapp_action.is_meta_provider():
            app_secret = whatsapp_action._env_app_secret()
            if not app_secret:
                raise HTTPException(
                    status_code=500,
                    detail="WHATSAPP_APP_SECRET (or FACEBOOK_APP_SECRET) is required for meta webhook POST",
                )
            raw_body: bytes = getattr(request.state, "raw_body", b"")
            if not raw_body:
                raw_body = await request.body()
            if not verify_meta_webhook_signature(raw_body, request, app_secret):
                raise HTTPException(status_code=401, detail="Invalid X-Hub-Signature-256")
            try:
                request_data = json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                logger.debug("Meta WhatsApp webhook JSON parse error: %s", e)
                raise HTTPException(status_code=400, detail="Invalid JSON body")
        else:
            await _authenticate_bridge_webhook_api_key(request)
            request_data = getattr(request.state, "parsed_payload", None)
            if request_data is None:
                request_data = await request.json()

        try:
            wa = await whatsapp_action.api()
            data = await wa.parse_inbound_message(request_data)
        except ValidationError as e:
            logger.debug(f"Validation error parsing WhatsApp webhook request: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid request format: {e}")
        except Exception as e:
            logger.debug(f"Error parsing WhatsApp webhook request: {e}")
            data = None

        if not data or data.message_type in ["ignored"]:
            return {"status": "ignored", "response": "Ignore message"}

        if data.fromMe:
            return {"status": "received", "response": "Ignore message"}

        # MessagePayload is a dataclass, access attributes directly
        utterance = data.body or data.caption
        utterance = utterance.strip() if utterance else None

        # Skip LID conversion for groups - @g.us IDs are not LIDs and cause "No LID for user" errors
        if (
            not whatsapp_action.is_meta_provider()
            and "@lid" in data.sender
            and "@g.us" not in data.sender
        ):
            data.sender = await wa.convert_lid_to_phone_number(data.sender)
            t0 = getattr(request.state, "webhook_start", None)
            if t0 is not None:
                logger.debug(
                    f"Webhook: convert_lid done in {int((time.perf_counter() - t0) * 1000)}ms"
                )

        sender = data.sender
        sender_name = data.sender_name

        access_control_action = await agent.get_access_control_action()

        # Run access check and directed-message check in parallel
        async def _check_access():
            if access_control_action:
                return await access_control_action.has_action_access(
                    user_id=sender, action_label="WhatsAppAction", channel="whatsapp"
                )
            return True

        has_access, direct_message = await asyncio.gather(
            _check_access(),
            is_directed_message(whatsapp_action, data),
        )
        if not has_access:
            log_access_denied(
                agent_id=agent_id,
                user_id=sender or None,
                channel="whatsapp",
                action_label="WhatsAppAction",
                stage="whatsapp",
            )
            return {"status": "received", "response": "Access denied"}

        # Validate sender
        if (
            not sender
            or sender == data.receiver
            or any(keyword in data.sender for keyword in whatsapp_action.ignore_list)
            or any(keyword in data.receiver for keyword in whatsapp_action.ignore_list)
        ):
            return {"status": "ignored", "response": "Sender blocked"}

        if not direct_message:
            return {"status": "ignored", "response": "Not directed message"}

        # Check if this is a media message
        if data.message_type in ["image", "document", "video", "audio"] and data.media:
            return await _handle_media_message(
                data, sender, agent_id, whatsapp_action, utterance
            )
        elif data.message_type in ["ptt"] and data.media:
            voice_result = await _handle_voice_message(data, sender, whatsapp_action)
            utterance = voice_result.get("transcript", "")
        elif data.message_type in ["location"] and data.location:
            typing_result = await wa.set_typing_status(
                phone=sender, value=True, is_group=data.isGroup
            )
            utterance = f"Location: {data.location.get('latitude')}, {data.location.get('longitude')}"
        elif utterance:
            # Trigger typing immediately
            try:
                typing_result = await wa.set_typing_status(
                    phone=sender, value=True, is_group=data.isGroup
                )
                t0 = getattr(request.state, "webhook_start", None)
                if t0 is not None:
                    logger.debug(
                        f"Webhook: set_typing done in {int((time.perf_counter() - t0) * 1000)}ms"
                    )
                if not typing_result.get("ok", True):
                    logger.debug(
                        f"Failed to set typing status for {sender}: {typing_result.get('error', 'Unknown error')}"
                    )
            except Exception as e:
                logger.debug(f"Failed to set typing status for {sender}: {e}")
        else:
            await _clear_whatsapp_typing(
                agent, agent_id, sender, getattr(data, "isGroup", False)
            )
            return {"status": "ignored", "response": "Ignore interaction"}

        # Serverless: flush stale persisted media batches before text/voice/location
        # interactions. Not run on media webhooks so multi-image albums can coalesce
        # without flushing a partial batch when the next image arrives after the window.
        await _batch_manager.flush_pending_batch_if_stale(
            sender, whatsapp_action.media_batch_window
        )

        quoted = getattr(data, "quoted_message", None) or {}
        utterance = _build_utterance_with_quoted_context(quoted, utterance) or utterance

        if utterance and len(utterance) > whatsapp_action.utterance_max_length:
            await _clear_whatsapp_typing(
                agent, agent_id, sender, getattr(data, "isGroup", False)
            )
            return {"status": "ignored", "response": "Utterance too long."}

        task = await create_task(
            _process_interaction_async(
                data, utterance, sender, agent_id, agent, sender_name=sender_name
            ),
            name=f"whatsapp_interaction_{sender}",
        )
        if task is None:
            logger.info(f"Processing interaction synchronously for {sender}")
        t0 = getattr(request.state, "webhook_start", None)
        if t0 is not None:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            if task is None:
                logger.debug(f"Webhook: interaction done in {elapsed_ms}ms")
            else:
                logger.debug(f"Webhook: queued for async in {elapsed_ms}ms")
        return {"status": "received"}

    except (ResourceNotFoundError, HTTPException):
        raise
    except DatabaseError as e:
        logger.error(f"Database error in WhatsApp webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")
    except Exception as e:
        logger.error(f"Unexpected error in WhatsApp webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@endpoint(
    "/actions/{action_id}/send_message",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="sent"),
        }
    ),
)
async def send_message(
    action_id: str,
    to: str,
    message: str,
    is_group: bool = False,
    is_newsletter: bool = False,
    message_id: str = "",
    outbox: bool = False,
    options: Optional[dict] = None,
) -> Dict[str, Any]:
    """Send a WhatsApp message via a specific WhatsApp action.

    Args:
        action_id: ID of the WhatsApp action
        to: Phone number to send message to
        message: Message content
        is_group: Whether the message is for a group
        is_newsletter: Whether the message is a newsletter
        message_id: ID of the message
        options: Additional options

    Returns:
        Dict[str, Any]: Result of the message send operation
    """
    whatsapp_action = await get_whatsapp_action(action_id)

    if outbox:
        logger.debug("Outbox not implemented yet")
        return {"status": "outbox not implemented yet"}

    wa = await whatsapp_action.api()
    result = await wa.send_message(
        phone=to,
        message=message,
        is_group=is_group,
        is_newsletter=is_newsletter,
        message_id=message_id,
        options=options,
    )
    return normalize_result(result, "sent")


@endpoint(
    "/actions/{action_id}/send_image",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="sent"),
        }
    ),
)
async def send_image(
    action_id: str,
    to: str,
    image_url: str,
    caption: str = "",
    filename: str = "image.jpg",
    is_group: bool = False,
) -> Dict[str, Any]:
    """Send a WhatsApp image via a specific WhatsApp action.

    Args:
        action_id: ID of the WhatsApp action
        to: Phone number to send image to
        image_url: URL of the image
        caption: Caption for the image
        filename: Filename for the image
        is_group: Whether the image is for a group

    Returns:
        Dict[str, Any]: Result of the image send operation
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    wa = await whatsapp_action.api()
    result = await wa.send_image(
        phone=to,
        file_url=image_url,
        caption=caption,
        filename=filename,
        is_group=is_group,
    )
    return normalize_result(result, "sent")


@endpoint(
    "/actions/{action_id}/send_file",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="sent"),
        }
    ),
)
async def send_file(
    action_id: str,
    to: str,
    file_url: str,
    caption: str = "",
    filename: str = "file",
    is_group: bool = False,
) -> Dict[str, Any]:
    """Send a WhatsApp file/document via a specific WhatsApp action.

    Args:
        action_id: ID of the WhatsApp action
        to: Phone number to send file to
        file_url: URL of the file
        caption: Caption for the file
        filename: Filename for the file
        is_group: Whether the file is for a group

    Returns:
        Dict[str, Any]: Result of the file send operation
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    wa = await whatsapp_action.api()
    result = await wa.send_file(
        phone=to,
        file_url=file_url,
        caption=caption,
        filename=filename,
        is_group=is_group,
    )
    return normalize_result(result, "sent")


@endpoint(
    "/actions/{action_id}/send_voice",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="sent"),
        }
    ),
)
async def send_voice(
    action_id: str,
    to: str,
    voice_url: str,
    is_group: bool = False,
    quoted_message_id: str = "",
) -> Dict[str, Any]:
    """Send a WhatsApp voice message via a specific WhatsApp action.

    Args:
        action_id: ID of the WhatsApp action
        to: Phone number to send voice to
        voice_url: URL of the voice/audio file
        is_group: Whether the voice is for a group
        quoted_message_id: ID of message to quote/reply to

    Returns:
        Dict[str, Any]: Result of the voice send operation
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    wa = await whatsapp_action.api()
    result = await wa.send_voice(
        phone=to,
        file_url=voice_url,
        is_group=is_group,
        quoted_message_id=quoted_message_id,
    )
    return normalize_result(result, "sent")


@endpoint(
    "/actions/{action_id}/send_location",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="sent"),
        }
    ),
)
async def send_location(
    action_id: str,
    to: str,
    latitude: float,
    longitude: float,
    title: str = "",
    is_group: bool = False,
) -> Dict[str, Any]:
    """Send a WhatsApp location via a specific WhatsApp action.

    Args:
        action_id: ID of the WhatsApp action
        to: Phone number to send location to
        latitude: Latitude coordinate
        longitude: Longitude coordinate
        title: Title/name for the location
        is_group: Whether the location is for a group

    Returns:
        Dict[str, Any]: Result of the location send operation
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    wa = await whatsapp_action.api()
    result = await wa.send_location(
        phone=to, latitude=latitude, longitude=longitude, title=title, is_group=is_group
    )
    return normalize_result(result, "sent")


# ========================================================================
# GROUP MANAGEMENT ENDPOINTS
# ========================================================================


@endpoint(
    "/actions/{action_id}/group/create",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="created"),
        }
    ),
)
async def create_group(
    action_id: str,
    name: str,
    participants: List[str],
) -> Dict[str, Any]:
    """Create a WhatsApp group via a specific WhatsApp action.

    Args:
        action_id: ID of the WhatsApp action
        name: Name of the group
        participants: List of phone numbers to add as participants

    Returns:
        Dict[str, Any]: Result of the group creation
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    wa = await whatsapp_action.api()
    result = await wa.create_group(name=name, participants=participants)
    return normalize_result(result, "created")


@endpoint(
    "/actions/{action_id}/group/add_participant",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="added"),
        }
    ),
)
async def add_group_participant(
    action_id: str,
    group_id: str,
    phone: str,
) -> Dict[str, Any]:
    """Add a participant to a WhatsApp group.

    Args:
        action_id: ID of the WhatsApp action
        group_id: ID of the group
        phone: Phone number of participant to add

    Returns:
        Dict[str, Any]: Result of the operation
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    wa = await whatsapp_action.api()
    result = await wa.add_group_participant(group_id=group_id, phone=phone)
    return normalize_result(result, "added")


@endpoint(
    "/actions/{action_id}/group/remove_participant",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="removed"),
        }
    ),
)
async def remove_group_participant(
    action_id: str,
    group_id: str,
    phone: str,
) -> Dict[str, Any]:
    """Remove a participant from a WhatsApp group.

    Args:
        action_id: ID of the WhatsApp action
        group_id: ID of the group
        phone: Phone number of participant to remove

    Returns:
        Dict[str, Any]: Result of the operation
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    wa = await whatsapp_action.api()
    result = await wa.remove_group_participant(group_id=group_id, phone=phone)
    return normalize_result(result, "removed")


@endpoint(
    "/actions/{action_id}/profile_picture/{phone}",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "profile_picture": ResponseField(field_type=str, example="https://..."),
        }
    ),
)
async def get_profile_picture(
    action_id: str,
    phone: str,
) -> Dict[str, Any]:
    """Get profile picture URL for a contact.

    Args:
        action_id: ID of the WhatsApp action
        phone: Phone number of the contact

    Returns:
        Dict[str, Any]: Profile picture URL
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    wa = await whatsapp_action.api()
    return await wa.get_profile_picture(phone=phone)


# ========================================================================
# SESSION MANAGEMENT ENDPOINTS
# ========================================================================


@endpoint(
    "/actions/{action_id}/status",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="CONNECTED"),
        }
    ),
)
async def get_session_status(
    action_id: str,
) -> Dict[str, Any]:
    """Get WhatsApp session status.

    Args:
        action_id: ID of the WhatsApp action

    Returns:
        Dict[str, Any]: Session status information
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    wa = await whatsapp_action.api()
    return await wa.status()


@endpoint(
    "/actions/{action_id}/session/register",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="CONNECTED"),
            "ok": ResponseField(field_type=bool, example=True, default=True),
            "message": ResponseField(
                field_type=Optional[str],
                example="Session registered successfully",
                default=None,
            ),
        }
    ),
)
async def register_session(
    action_id: str,
) -> Dict[str, Any]:
    """Register WhatsApp session with the API provider.

    This endpoint is used to manually register or re-register a WhatsApp session,
    particularly useful for:
    - Fresh installs on Lambda where startup registration timed out or didn't run
    - Retrying registration without restarting the app
    - Forcing re-registration after configuration changes

    The endpoint calls register_session() on the WhatsAppAction, which:
    - Generates webhook URL if not set
    - Registers the session with the WhatsApp API provider (WPPConnect, WWebJS, etc.)
    - Returns session status and registration details

    Args:
        action_id: ID of the WhatsApp action

    Returns:
        Dict[str, Any]: Registration result with status, ok flag, and message
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    result = await whatsapp_action.register_session()

    # If registration succeeded, mark as registered to avoid redundant lazy calls
    if isinstance(result, dict):
        if result.get("ok", True) and result.get("status") != "ERROR":
            whatsapp_action._session_registered = True

    return result


@endpoint(
    "/actions/{action_id}/meta/webhook-url",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    summary="Meta Cloud API callback URL (without api_key query param)",
)
async def get_meta_webhook_url(action_id: str) -> Dict[str, Any]:
    """Return webhook URLs for Meta App Dashboard vs bridge providers."""
    whatsapp_action = await get_whatsapp_action(action_id)
    if not whatsapp_action.is_meta_provider():
        raise HTTPException(
            status_code=400, detail="Endpoint only applies to meta provider"
        )
    url = whatsapp_action.webhook_url or await whatsapp_action.get_webhook_url()
    meta_url = whatsapp_action.meta_callback_url_for_subscription(url)
    return {
        "webhook_url": url,
        "meta_callback_url": meta_url,
        "verify_token_env": "WHATSAPP_VERIFY_TOKEN",
        "dashboard_note": (
            "App Dashboard shows the app default callback only. After startup, "
            "GET .../meta/webhook-status shows the active WABA/phone override."
        ),
    }


@endpoint(
    "/actions/{action_id}/meta/webhook-status",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    summary="Get Meta WhatsApp webhook override status from Graph API",
)
async def get_meta_webhook_status(action_id: str) -> Dict[str, Any]:
    """Return expected callback URL and live override from Meta Graph."""
    whatsapp_action = await get_whatsapp_action(action_id)
    if not whatsapp_action.is_meta_provider():
        raise HTTPException(
            status_code=400, detail="Endpoint only applies to meta provider"
        )
    return await whatsapp_action.get_meta_webhook_override_status()


@endpoint(
    "/actions/{action_id}/meta/webhook-register",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    summary="Register Meta WhatsApp webhook override (WABA or phone number)",
)
async def register_meta_webhook(action_id: str) -> Dict[str, Any]:
    """Push callback URL override to Meta Graph API immediately."""
    whatsapp_action = await get_whatsapp_action(action_id)
    if not whatsapp_action.is_meta_provider():
        raise HTTPException(
            status_code=400, detail="Endpoint only applies to meta provider"
        )
    result = await whatsapp_action.register_meta_webhook_subscription()
    if result.get("status") == "error":
        raise HTTPException(status_code=502, detail=result)
    if result.get("ok") and result.get("status") == "ok":
        whatsapp_action._session_registered = True
    return result


@endpoint(
    "/actions/{action_id}/qrcode",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "qrcode": ResponseField(
                field_type=str, example="data:image/png;base64,..."
            ),
        }
    ),
)
async def get_qrcode(
    action_id: str,
) -> Dict[str, Any]:
    """Get QR code for WhatsApp authentication.

    Args:
        action_id: ID of the WhatsApp action

    Returns:
        Dict[str, Any]: QR code as base64 image
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    bridge_na = _bridge_provider_response(whatsapp_action)
    if bridge_na:
        return bridge_na
    wa = await whatsapp_action.api()
    return await wa.qrcode()


@endpoint(
    "/actions/{action_id}/device",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "device": ResponseField(field_type=dict, example={}),
        }
    ),
)
async def get_device_info(
    action_id: str,
) -> Dict[str, Any]:
    """Get connected device information.

    Args:
        action_id: ID of the WhatsApp action

    Returns:
        Dict[str, Any]: Device information
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    bridge_na = _bridge_provider_response(whatsapp_action)
    if bridge_na:
        return bridge_na
    wa = await whatsapp_action.api()
    return {"device": await wa.get_host_device()}


@endpoint(
    "/actions/{action_id}/logout",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="logout"),
        }
    ),
)
async def logout(
    action_id: str,
) -> Dict[str, Any]:
    """Logout from WhatsApp.

    Args:
        action_id: ID of the WhatsApp action

    Returns:
        Dict[str, Any]: Result of the operation
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    bridge_na = _bridge_provider_response(whatsapp_action)
    if bridge_na:
        return bridge_na
    wa = await whatsapp_action.api()
    result = await wa.logout_session()
    return normalize_result(result, "logout")


@endpoint(
    "/actions/{action_id}/close",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["WhatsApp"],
    response=success_response(
        data={
            "status": ResponseField(field_type=str, example="close"),
        }
    ),
)
async def close(
    action_id: str,
) -> Dict[str, Any]:
    """Close WhatsApp session.

    Args:
        action_id: ID of the WhatsApp action

    Returns:
        Dict[str, Any]: Result of the operation
    """
    whatsapp_action = await get_whatsapp_action(action_id)
    bridge_na = _bridge_provider_response(whatsapp_action)
    if bridge_na:
        return bridge_na
    wa = await whatsapp_action.api()
    result = await wa.close_session()
    return normalize_result(result, "close")
