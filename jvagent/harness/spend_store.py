"""Single-writer graph storage for official Harness spend counters."""

import asyncio
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from jvspatial import flush_deferred_entities
from pydantic import TypeAdapter
from pydantic_ai_harness.spend import SpendEntry, Spent


class ConversationSpendStore:
    """Persist counters and replay tokens on the existing Conversation.

    The host must serialize conversation execution. This adapter is not a
    cross-worker reservation service and cannot prevent in-flight overshoot.
    """

    def __init__(self, conversation: Any) -> None:
        self.conversation = conversation
        self.lock = asyncio.Lock()

    def _read(
        self, key: str, now: datetime, state: dict[str, Any] | None = None
    ) -> tuple[Spent, dict[str, Any]]:
        source = self.conversation.native_spend if state is None else state
        item = source.get(key)
        if item is None:
            return Spent(), {}
        if not isinstance(item, dict):
            raise ValueError("Corrupt native spend state")
        expiry = item.get("expires_at")
        if expiry is not None and datetime.fromisoformat(expiry) <= now:
            return Spent(), {}
        raw = item["spent"]
        if not isinstance(raw, dict) or any(
            isinstance(raw.get(key), bool)
            for key in ("usd", "tokens", "requests", "unpriced_requests")
        ):
            raise ValueError("Corrupt native spend counters")
        spent = TypeAdapter(Spent).validate_python(raw)
        if (
            not spent.usd.is_finite()
            or spent.usd < 0
            or any(
                isinstance(v, bool) or v < 0
                for v in (spent.tokens, spent.requests, spent.unpriced_requests)
            )
        ):
            raise ValueError("Invalid native spend counters")
        if not isinstance(item.get("tokens"), list):
            raise ValueError("Corrupt native spend replay ledger")
        return spent, item

    async def get_many(self, keys: Sequence[str]) -> Mapping[str, Spent]:
        async with self.lock:
            now = datetime.now(timezone.utc)
            return {key: self._read(key, now)[0] for key in keys}

    async def add_many(self, entries: Sequence[SpendEntry]) -> Mapping[str, Spent]:
        async with self.lock:
            now = datetime.now(timezone.utc)
            state = dict(self.conversation.native_spend)
            results = {}
            for entry in entries:
                if (
                    not entry.usd.is_finite()
                    or entry.usd < Decimal(0)
                    or any(
                        isinstance(v, bool) or v < 0
                        for v in (entry.tokens, entry.requests, entry.unpriced)
                    )
                ):
                    raise ValueError("Invalid native spend increment")
                # Same-key entries in this batch must see earlier increments.
                prior, item = self._read(entry.key, now, state)
                tokens = list(item.get("tokens", []))
                if entry.token is not None and entry.token in tokens:
                    results[entry.key] = prior
                    continue
                updated = Spent(
                    usd=prior.usd + entry.usd,
                    tokens=prior.tokens + entry.tokens,
                    requests=prior.requests + entry.requests,
                    unpriced_requests=prior.unpriced_requests + entry.unpriced,
                )
                if entry.token is not None:
                    tokens.append(entry.token)
                state[entry.key] = {
                    "spent": TypeAdapter(Spent).dump_python(updated, mode="json"),
                    "tokens": tokens,
                    "expires_at": (now + entry.ttl).isoformat() if entry.ttl else None,
                }
                results[entry.key] = updated
            self.conversation.native_spend = state
            await self.conversation.save()
            await flush_deferred_entities(self.conversation, strict=True)
            return results
