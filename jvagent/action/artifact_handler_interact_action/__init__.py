"""ArtifactHandlerInteractAction — interact-action that auto-detects media
attachments and ingests them, plus hosts the jvforge job reverse-index,
completion notification sub-callback, and LLM tool dispatch for the
artifact_handler skill.

Owns the persisted ``jvforge_job_index`` that maps a jvforge ``job_id`` to the
originating user/conversation/session/channel (and optional deferred
``pending_question``), and exposes the
``POST /api/artifact_handler_action/notify/{agent_id}`` sub-callback that jvforge
pings when an async ingest job finishes. On a "completed" ping the action
publishes a ready message (WhatsApp, no question) or enqueues a proactive
turn with ready + answer directives (WhatsApp + pending question). Web /
default leave status for ``check_ingest_status`` until a proactive outbox exists.

The action also registers LLM tools (``artifact_handler__*``) that dispatch to
the skill's ``custom_tools.py`` functions via ``VaultToolContext``.
"""

from __future__ import annotations

from . import endpoints  # noqa: F401 — register notify webhook
from .artifact_handler_interact_action import (
    ArtifactHandlerInteractAction,
    collect_visitor_media,
)

__all__ = ["ArtifactHandlerInteractAction", "collect_visitor_media"]
