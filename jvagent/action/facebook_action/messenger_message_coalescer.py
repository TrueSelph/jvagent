"""Debounce Messenger webhook deliveries per sender so caption + media become one interaction."""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from jvagent.action.facebook_action.facebook_api import FacebookAPI

logger = logging.getLogger(__name__)


def _attachment_dedupe_key(att: Any) -> Optional[str]:
    if not isinstance(att, dict):
        return None
    url = FacebookAPI._messenger_attachment_url(att)
    if url:
        return f"url:{url}"
    pair = FacebookAPI._messenger_attachment_location_coords(att)
    if pair is not None:
        return f"loc:{pair[0]},{pair[1]}"
    return f"id:{id(att)}"


def merge_messenger_events(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge buffered webhook events into one synthetic event (same shape as ``iter_messenger`` items)."""
    if not events:
        return {}
    if len(events) == 1:
        sole = dict(events[0])
        mid = sole.get("mid")
        if mid and not sole.get("merged_mids"):
            sole["merged_mids"] = [str(mid)]
        return sole

    sorted_events = sorted(
        events,
        key=lambda e: int(e.get("timestamp") or 0),
    )
    parts: List[str] = []
    for e in sorted_events:
        t = str(e.get("message") or "").strip()
        if t:
            parts.append(t)
    combined_text = " | ".join(parts)

    merged_attachments: List[Dict[str, Any]] = []
    seen_keys: Set[str] = set()
    for e in sorted_events:
        raw = e.get("attachments") or []
        if not isinstance(raw, list):
            continue
        for att in raw:
            key = _attachment_dedupe_key(att)
            if key is None:
                merged_attachments.append(att)  # type: ignore[arg-type]
                continue
            if key in seen_keys:
                continue
            seen_keys.add(key)
            if isinstance(att, dict):
                merged_attachments.append(att)

    reply_to: Optional[Dict[str, Any]] = None
    for e in reversed(sorted_events):
        r = e.get("reply_to")
        if isinstance(r, dict) and r:
            reply_to = r
            break

    last = sorted_events[-1]
    merged_mids = [str(e.get("mid") or "") for e in sorted_events if e.get("mid")]

    return {
        "sender_name": str(last.get("sender_name") or ""),
        "sender_id": str(last.get("sender_id") or ""),
        "page_id": str(last.get("page_id") or ""),
        "message_type": "message",
        "message": combined_text,
        "attachments": merged_attachments,
        "caption": "",
        "mid": str(last.get("mid") or "") if last.get("mid") else "",
        "parent_message_id": "",
        "timestamp": int(last.get("timestamp") or 0),
        "reply_to": reply_to,
        "data": last.get("data"),
        "messaging": last.get("messaging"),
        "merged_mids": merged_mids,
    }


class MessengerMessageCoalescer:
    """Per-(buffer_key) debounce: append events, flush after quiet period."""

    _locks: Dict[str, asyncio.Lock] = {}
    _buffers: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def _lock(cls, key: str) -> asyncio.Lock:
        if key not in cls._locks:
            cls._locks[key] = asyncio.Lock()
        return cls._locks[key]

    @classmethod
    async def schedule_merge(
        cls,
        buffer_key: str,
        event: Dict[str, Any],
        window_seconds: float,
        on_flush: Callable[[Dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Queue ``event``; invoke ``on_flush`` with merged payload after ``window_seconds`` quiet."""
        if window_seconds <= 0:
            await on_flush(dict(event))
            return

        lock = cls._lock(buffer_key)
        async with lock:
            buf = cls._buffers.get(buffer_key)
            if buf is None:
                buf = {"events": [], "task": None}
                cls._buffers[buffer_key] = buf
            buf["events"].append(event)
            old = buf.get("task")
            if old is not None:
                old.cancel()
            buf["task"] = asyncio.create_task(
                cls._flush_after_quiet(buffer_key, window_seconds, on_flush),
                name=f"messenger_coalesce_{buffer_key[:40]}",
            )

    @classmethod
    async def _flush_after_quiet(
        cls,
        buffer_key: str,
        window_seconds: float,
        on_flush: Callable[[Dict[str, Any]], Awaitable[None]],
    ) -> None:
        try:
            await asyncio.sleep(window_seconds)
        except asyncio.CancelledError:
            return

        lock = cls._lock(buffer_key)
        async with lock:
            buf = cls._buffers.pop(buffer_key, None)
            if not buf:
                return
            buf["task"] = None
            events: List[Dict[str, Any]] = buf.get("events") or []
        if not events:
            return
        merged = merge_messenger_events(events)
        try:
            await on_flush(merged)
        except Exception as e:
            logger.error(
                "Messenger coalescer flush failed for %s: %s",
                buffer_key,
                e,
                exc_info=True,
            )
