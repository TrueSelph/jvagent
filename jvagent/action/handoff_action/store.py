"""Pending staff questions for :class:`~jvagent.action.handoff_action.HandoffAction`.

Agent-scoped (not conversation-scoped) so a staff member answering in their own
thread can find the question a customer asked elsewhere.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator, List, Optional

from jvspatial.core import Node
from jvspatial.core.annotations import attribute
from jvspatial.core.context import GraphContext

logger = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_RESOLVED = "resolved"


@asynccontextmanager
async def _on_prime() -> AsyncIterator[Optional[GraphContext]]:
    """Pin HandoffQuestion reads/writes to the prime graph database.

    A turn can swap the default context (PageIndex does this). Questions must
    stay on the same database as the agent — same pattern as WhatsApp/Email.
    """
    from jvspatial.core.context import get_default_context, scoped_default_context_async
    from jvspatial.db import get_prime_database

    current = get_default_context()
    try:
        prime_db = get_prime_database()
    except Exception:
        logger.warning("HandoffQuestion: prime database unavailable", exc_info=True)
        yield current
        return
    if getattr(current, "database", None) is prime_db:
        yield current
        return
    prime_ctx = GraphContext(database=prime_db)
    async with scoped_default_context_async(prime_ctx):
        yield prime_ctx


class HandoffQuestion(Node):
    """One customer question awaiting a staff answer."""

    agent_id: str = attribute(indexed=True, default="")
    question: str = attribute(default="")
    notes: str = attribute(default="")
    user_id: str = attribute(indexed=True, default="")
    user_channel: str = attribute(default="default")
    user_contact: str = attribute(default="")
    session_id: str = attribute(default="")
    status: str = attribute(indexed=True, default=STATUS_PENDING)
    answer: str = attribute(default="")
    created_at: str = attribute(default="")
    resolved_at: str = attribute(default="")

    @classmethod
    async def create_question(
        cls,
        *,
        agent_id: str,
        question: str,
        notes: str = "",
        user_id: str = "",
        user_channel: str = "default",
        user_contact: str = "",
        session_id: str = "",
    ) -> "HandoffQuestion":
        async with _on_prime():
            created = await cls.create(
                agent_id=agent_id,
                question=question,
                notes=notes,
                user_id=user_id,
                user_channel=user_channel,
                user_contact=user_contact,
                session_id=session_id,
                status=STATUS_PENDING,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            stored = await cls.get(created.id)
        if stored is None:
            logger.warning(
                "HandoffQuestion.create_question did not persist id=%s agent_id=%r",
                getattr(created, "id", None),
                agent_id,
            )
            raise RuntimeError(
                f"handoff question {getattr(created, 'id', None)} was not saved"
            )
        return stored

    @classmethod
    async def pending(cls, agent_id: str) -> List["HandoffQuestion"]:
        """Pending questions for one agent (filter status in Python)."""
        rows: List["HandoffQuestion"] = []
        loaded: List["HandoffQuestion"] = []
        any_status = 0
        all_questions = 0
        try:
            async with _on_prime():
                rows = list(await cls.find(agent_id=agent_id) or [])
                loaded = [
                    row
                    for row in rows
                    if str(getattr(row, "status", "") or "") == STATUS_PENDING
                ]
                if not loaded:
                    any_status = len(rows)
                    all_questions = await cls.count()
        except Exception:
            logger.error(
                "HandoffQuestion.pending failed for agent %s", agent_id, exc_info=True
            )
            raise
        logger.warning(
            "HandoffQuestion.pending agent_id=%r status=%s loaded=%s scanned=%s",
            agent_id,
            STATUS_PENDING,
            len(loaded),
            len(rows),
        )
        if not loaded:
            logger.warning(
                "HandoffQuestion.pending empty agent_id=%r "
                "any_status=%s all_questions=%s",
                agent_id,
                any_status,
                all_questions,
            )
        return loaded

    @classmethod
    async def get_question(cls, question_id: str) -> Optional["HandoffQuestion"]:
        try:
            async with _on_prime():
                return await cls.get(question_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("HandoffQuestion.get_question failed: %s", exc)
            return None

    async def mark_resolved(self, answer: str) -> None:
        async with _on_prime() as ctx:
            if ctx is not None:
                self._graph_context = ctx
            self.status = STATUS_RESOLVED
            self.answer = answer
            self.resolved_at = datetime.now(timezone.utc).isoformat()
            await self.save()

    async def update_user_contact(self, contact: str) -> None:
        async with _on_prime() as ctx:
            if ctx is not None:
                self._graph_context = ctx
            self.user_contact = (contact or "").strip()
            await self.save()


__all__ = ["HandoffQuestion", "STATUS_PENDING", "STATUS_RESOLVED"]
