import ipaddress
import logging
import os
import socket
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import httpx
from jvspatial import create_task

logger = logging.getLogger(__name__)

# Outbound webhook scheme allow-list. Anything else (file://, gopher://, etc.)
# is rejected before DNS resolution. AUDIT-core C-4.
_ALLOWED_WEBHOOK_SCHEMES = frozenset({"http", "https"})


def _is_unsafe_ip(ip: ipaddress._BaseAddress) -> bool:
    """Return True for any IP that must not be a webhook target.

    Uses ipaddress' own classification (covers RFC1918, link-local, loopback,
    multicast, reserved, unspecified, and IPv6 ULA/link-local) rather than a
    hand-rolled CIDR list. AUDIT-core C-4(a)+(b).
    """
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return True
    # Catch IPv4-mapped IPv6 (::ffff:127.0.0.1) by checking the mapped IPv4
    # form against the same classifiers.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        mapped = ip.ipv4_mapped
        if (
            mapped.is_private
            or mapped.is_loopback
            or mapped.is_link_local
            or mapped.is_multicast
            or mapped.is_reserved
            or mapped.is_unspecified
        ):
            return True
    return False


async def _resolve_and_validate(webhook_url: str) -> Tuple[str, List[str]]:
    """Resolve *webhook_url*'s hostname once and reject private/reserved IPs.

    Returns ``(hostname, [validated_ip, ...])`` so the caller can pin the
    httpx connection to those exact addresses, defending against DNS
    rebinding (the resolver can't change between validation and connect
    if the connect uses the IP we already verified).

    Callers MUST NOT loop-retry against the same URL with fresh resolutions:
    an attacker who controls the target DNS can rotate records between
    attempts. Retries must reuse the validated IP list from this call.
    AUDIT-core C-4(d).
    """
    import asyncio

    parsed = urlparse(webhook_url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_WEBHOOK_SCHEMES:
        raise ValueError(
            f"Webhook URL scheme {scheme!r} is not allowed (must be one of "
            f"{sorted(_ALLOWED_WEBHOOK_SCHEMES)}): {webhook_url}"
        )

    hostname = (parsed.hostname or "").strip()
    if not hostname:
        raise ValueError(f"Webhook URL has no resolvable host: {webhook_url}")

    # If the hostname is itself an IP literal, check it BEFORE DNS resolution
    # so an attacker can't bypass via `http://10.0.0.1` style URLs.
    # AUDIT-core C-4(c).
    try:
        literal_ip = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        literal_ip = None
    if literal_ip is not None and _is_unsafe_ip(literal_ip):
        raise ValueError(
            f"Webhook URL hostname is a blocked IP literal "
            f"({literal_ip}): {webhook_url}"
        )

    try:
        addrs = await asyncio.to_thread(
            socket.getaddrinfo, hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
        )
    except socket.gaierror as e:
        raise ValueError(f"Webhook URL hostname resolution failed: {hostname}: {e}")

    safe: List[str] = []
    for _, _, _, _, sockaddr in addrs:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if _is_unsafe_ip(ip):
            raise ValueError(
                f"Webhook URL resolves to blocked address ({ip}): {webhook_url}"
            )
        safe.append(ip_str)

    if not safe:
        raise ValueError(f"Webhook URL has no usable address: {webhook_url}")
    return hostname, safe


async def _post_webhook_pinned_async(
    webhook_url: str, payload: Dict[str, Any], *, timeout: float = 10.0
) -> httpx.Response:
    """POST to *webhook_url* using a connection pinned to a validated IP.

    Resolves the hostname through :func:`_resolve_and_validate`, picks the
    first safe address, then issues the request against
    ``scheme://<ip>:port/path?query`` with the original ``Host`` header set.
    For HTTPS the SNI server-name is forwarded via the per-request extension
    so certificate validation still uses the original hostname.
    """
    hostname, safe_ips = _resolve_and_validate(webhook_url)
    parsed = urlparse(webhook_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    target_ip = safe_ips[0]
    netloc_ip = (
        f"[{target_ip}]:{port}"
        if ":" in target_ip and not target_ip.startswith("[")
        else f"{target_ip}:{port}"
    )
    pinned_url = urlunparse(parsed._replace(netloc=netloc_ip))
    headers = {"Host": parsed.netloc}

    extensions: Dict[str, Any] = {}
    if parsed.scheme == "https":
        # httpx 0.25+: forward the SNI hostname so TLS verification matches
        # the certificate's CN/SAN even though we connect to the IP directly.
        extensions["sni_hostname"] = hostname

    async with httpx.AsyncClient(timeout=timeout) as client:
        request = client.build_request(
            "POST", pinned_url, json=payload, headers=headers
        )
        return await client.send(request, extensions=extensions or None)


def _safe_webhook_target(webhook_url: str) -> str:
    """Return a redacted webhook target for logs."""
    try:
        parsed = urlparse(webhook_url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        pass
    return "[invalid-webhook-url]"


async def trigger_task_created_callback(
    conversation: Any, task_entry: Dict[str, Any]
) -> None:
    """Fire a webhook callback whenever a proactive task is created or updated as active.

    This follows the pattern of push-based task triggers to avoid global database polling.
    """
    try:
        # 1. Get Agent
        agent = await conversation.get_agent()
        if not agent:
            return

        # 2. Extract Webhook URL
        # We look for 'task_created_webhook_url' in TaskCreationInteractAction
        # or in agent metadata/env.
        webhook_url = None

        # Check environment variable first for global default
        webhook_url = os.environ.get("JVAGENT_TASK_CREATED_WEBHOOK_URL")

        # Check action config (more specific)
        dispatch_url = None
        try:
            scheduler = await agent.get_action_by_type("TaskCreationInteractAction")
            if scheduler:
                if (
                    hasattr(scheduler, "task_created_webhook_url")
                    and scheduler.task_created_webhook_url
                ):
                    webhook_url = scheduler.task_created_webhook_url

                # Retrieve the secure dynamic URL for dispatching
                try:
                    # Resolve base dispatch URL (agent-wide)
                    base_dispatch_url = await scheduler.get_webhook_url()
                    if base_dispatch_url:
                        # Enhance URL with session-specific targeting
                        session_id = str(conversation.session_id)
                        connector = "&" if "?" in base_dispatch_url else "?"
                        dispatch_url = f"{base_dispatch_url}{connector}conversation_id={session_id}"
                except Exception:
                    pass
        except Exception:
            pass

        if not webhook_url:
            return

        # 3. Fire Webhook (Background)
        async def _fire():
            try:
                payload = {
                    "agent_id": str(agent.id),
                    "conversation_id": str(conversation.session_id),
                    "task_id": task_entry.get("task_id"),
                    "task_type": task_entry.get("task_type"),
                    "description": task_entry.get("description"),
                    "next_trigger_at": task_entry.get("next_trigger_at"),
                    "dispatch_url": dispatch_url,  # SESSION-TARGETED URL to call back
                    "metadata": task_entry.get("metadata", {}),
                    "timestamp": task_entry.get("created_at"),
                    "event": "task_created",
                }
                redacted_target = _safe_webhook_target(webhook_url)

                redacted_dispatch_target = (
                    _safe_webhook_target(dispatch_url) if dispatch_url else ""
                )

                logger.info(
                    f"Callback: Firing task-created webhook for task {task_entry.get('task_id')} "
                    f"(Session: {conversation.session_id}) to {redacted_target}. "
                    f"Session-Targeted Dispatch URL: {redacted_dispatch_target}"
                )
                response = await _post_webhook_pinned_async(webhook_url, payload)
                response.raise_for_status()
                logger.debug(f"Webhook response: {response.status_code}")
            except Exception as e:
                logger.error(f"Failed to fire task creation webhook: {e}")

        # Use jvspatial.create_task to fire-and-forget safely in background
        await create_task(_fire(), name=f"task_webhook_{task_entry.get('task_id')}")

    except Exception as e:
        logger.error(f"Error in trigger_task_created_callback: {e}")


def _event_env_var(event_name: str) -> str:
    return f"JVAGENT_{event_name.upper()}_WEBHOOK_URL"


def _event_callback_name(event_name: str) -> str:
    return event_name.replace("task_", "")


async def _resolve_event_webhook(
    conversation: Any, event_name: str
) -> tuple[Optional[str], Optional[str], Optional[Any]]:
    webhook_url = os.environ.get(_event_env_var(event_name))
    dispatch_url = None
    agent = await conversation.get_agent()
    if not agent:
        return webhook_url, dispatch_url, agent
    if webhook_url:
        return webhook_url, dispatch_url, agent

    try:
        scheduler = await agent.get_action_by_type("TaskCreationInteractAction")
        if scheduler and getattr(scheduler, "task_created_webhook_url", None):
            webhook_url = scheduler.task_created_webhook_url
        if scheduler:
            try:
                base_dispatch_url = await scheduler.get_webhook_url()
                if base_dispatch_url:
                    session_id = str(conversation.session_id)
                    connector = "&" if "?" in base_dispatch_url else "?"
                    dispatch_url = (
                        f"{base_dispatch_url}{connector}conversation_id={session_id}"
                    )
            except Exception:
                pass
    except Exception:
        pass
    return webhook_url, dispatch_url, agent


async def _trigger_task_event_callback(
    conversation: Any, task_entry: Dict[str, Any], event_name: str
) -> None:
    try:
        webhook_url, dispatch_url, agent = await _resolve_event_webhook(
            conversation, event_name
        )
        if not agent or not webhook_url:
            return
        callback_name = _event_callback_name(event_name)

        async def _fire():
            try:
                payload = {
                    "agent_id": str(agent.id),
                    "conversation_id": str(conversation.session_id),
                    "task_id": task_entry.get("task_id"),
                    "task_type": task_entry.get("task_type"),
                    "description": task_entry.get("description"),
                    "status": task_entry.get("status"),
                    "next_trigger_at": task_entry.get("next_trigger_at"),
                    "dispatch_url": dispatch_url,
                    "metadata": task_entry.get("metadata", {}),
                    "timestamp": task_entry.get("updated_at")
                    or task_entry.get("created_at"),
                    "event": event_name,
                }
                redacted_target = _safe_webhook_target(webhook_url)
                logger.info(
                    "Callback: Firing %s webhook for task %s (Session: %s) to %s",
                    callback_name,
                    task_entry.get("task_id"),
                    conversation.session_id,
                    redacted_target,
                )
                response = await _post_webhook_pinned_async(webhook_url, payload)
                response.raise_for_status()
            except Exception as e:
                logger.error(
                    "Failed to fire %s webhook: %s",
                    callback_name,
                    e,
                )

        await create_task(
            _fire(), name=f"{callback_name}_webhook_{task_entry.get('task_id')}"
        )
    except Exception as e:
        logger.error("Error in %s callback: %s", event_name, e)


async def trigger_task_updated_callback(
    conversation: Any, task_entry: Dict[str, Any]
) -> None:
    await _trigger_task_event_callback(
        conversation=conversation,
        task_entry=task_entry,
        event_name="task_updated",
    )


async def trigger_task_completed_callback(
    conversation: Any, task_entry: Dict[str, Any]
) -> None:
    await _trigger_task_event_callback(
        conversation=conversation,
        task_entry=task_entry,
        event_name="task_completed",
    )


async def trigger_task_failed_callback(
    conversation: Any, task_entry: Dict[str, Any]
) -> None:
    await _trigger_task_event_callback(
        conversation=conversation,
        task_entry=task_entry,
        event_name="task_failed",
    )


async def trigger_task_cancelled_callback(
    conversation: Any, task_entry: Dict[str, Any]
) -> None:
    await _trigger_task_event_callback(
        conversation=conversation,
        task_entry=task_entry,
        event_name="task_cancelled",
    )
