"""Compact vault history events for document save and pending-question answers.

Written onto interactions so a later turn (with ``with_event``) can pin
``pageindex__search`` to the last vault ``doc_name``.
"""

from __future__ import annotations

import inspect
from typing import Any, Optional

_VAULT_ACTION_NAME = "ArtifactHandlerInteractAction"


def saved_document_event(
    doc_name: str,
    *,
    pending_question: Optional[str] = None,
    status: str = "processing",
) -> str:
    """One-line event when a document is accepted for ingest."""
    name = (doc_name or "").strip()
    status_word = (status or "processing").strip() or "processing"
    line = f"Saved document {name}. Status: {status_word}."
    question = (pending_question or "").strip()
    if question:
        line += f" Pending question: {question}"
    return line


def answered_pending_event(doc_name: str, pending_question: str) -> str:
    """One-line event when a deferred question is answered from a ready doc."""
    name = (doc_name or "").strip()
    question = (pending_question or "").strip()
    return (
        f"Answered pending question on document {name}. "
        "Use this doc_name for follow-ups about this image/document. "
        f"Question: {question}"
    )


async def record_vault_event(target: Any, event: str) -> None:
    """Append *event* on an interaction (or visitor.interaction) and save.

    No-ops on missing interaction or invalid event. Uses a fixed action name
    so tool-dispatch turns still attribute the event to the vault action.
    """
    text = (event or "").strip()
    if not text or target is None:
        return
    interaction = target
    adder = getattr(interaction, "add_event", None)
    if not callable(adder):
        interaction = getattr(target, "interaction", None)
        adder = getattr(interaction, "add_event", None) if interaction else None
    if interaction is None or not callable(adder):
        return
    try:
        added = adder(text, _VAULT_ACTION_NAME)
    except Exception:
        return
    if added is False:
        return
    saver = getattr(interaction, "save", None)
    if not callable(saver):
        return
    try:
        result = saver()
        if inspect.isawaitable(result):
            await result
    except Exception:
        pass
