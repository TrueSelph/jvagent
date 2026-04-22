"""EvidenceLog: immutable raw evidence store for skill loop runs.

Every tool call/result pair produced during an agentic loop is appended to
the EvidenceLog.  The log is the *source of truth* for raw tool output and is
never truncated or summarized.

Model-facing context (the message list passed to the LLM) is a derived,
compacted view of this log; the compactor (see ``context_compactor.py``) may
drop or summarize older messages from the message list but will never remove
entries from the EvidenceLog.

Persistence
-----------
The log is stored in ``Conversation.context["_skill_evidence_log"]`` as a
JSON-serializable list.  Call ``persist_to(conversation)`` after the loop
completes (SkillAction handles this automatically).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

_CONTEXT_KEY = "_skill_evidence_log"


@dataclass
class EvidenceEntry:
    """One raw tool invocation record.

    Attributes:
        entry_id: Unique identifier for this evidence record.
        iteration: Loop iteration that produced this entry.
        tool_call_id: Provider-assigned tool call ID.
        tool_name: Tool name (namespaced, e.g. ``myskill__search``).
        input_fingerprint: Short hash of the serialised input arguments.
        content: Full raw tool result content (never truncated here).
        timestamp: ISO-8601 UTC timestamp of result receipt.
        is_error: Whether the result begins with ``"Error:"``.
    """

    entry_id: str
    iteration: int
    tool_call_id: str
    tool_name: str
    input_fingerprint: str
    content: str
    timestamp: str
    is_error: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvidenceEntry":
        return cls(
            entry_id=str(data.get("entry_id", "")),
            iteration=int(data.get("iteration", 0)),
            tool_call_id=str(data.get("tool_call_id", "")),
            tool_name=str(data.get("tool_name", "")),
            input_fingerprint=str(data.get("input_fingerprint", "")),
            content=str(data.get("content", "")),
            timestamp=str(data.get("timestamp", "")),
            is_error=bool(data.get("is_error", False)),
        )


def _fingerprint(args: str) -> str:
    """4-byte hex fingerprint of serialised tool arguments."""
    return hashlib.blake2b(args.encode(), digest_size=4).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvidenceLog:
    """Append-only in-memory evidence store for one SkillAction run.

    All entries are accumulated in memory during the run; call
    ``persist_to`` at the end to flush to the Conversation node.
    """

    def __init__(self) -> None:
        self._entries: List[EvidenceEntry] = []

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def append(
        self,
        *,
        iteration: int,
        tool_call_id: str,
        tool_name: str,
        input_args: str,
        content: str,
    ) -> EvidenceEntry:
        """Append a new raw tool result entry.

        Args:
            iteration: Current loop iteration.
            tool_call_id: Provider-assigned call ID.
            tool_name: Tool name as seen by the LLM.
            input_args: Raw JSON string of tool arguments (for fingerprinting).
            content: Full tool result text.

        Returns:
            The created EvidenceEntry.
        """
        idx = len(self._entries)
        fp = _fingerprint(input_args or "")
        entry_id = f"{tool_call_id or 'ev'}:{idx}:{fp}"
        entry = EvidenceEntry(
            entry_id=entry_id,
            iteration=iteration,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            input_fingerprint=fp,
            content=content,
            timestamp=_now_iso(),
            is_error=content.startswith("Error:"),
        )
        self._entries.append(entry)
        return entry

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[EvidenceEntry]:
        return iter(self._entries)

    def by_tool_call_id(self, tool_call_id: str) -> Optional[EvidenceEntry]:
        """Return the first entry matching a tool_call_id."""
        for entry in self._entries:
            if entry.tool_call_id == tool_call_id:
                return entry
        return None

    def for_iteration(self, iteration: int) -> List[EvidenceEntry]:
        """Return all entries produced during a given iteration."""
        return [e for e in self._entries if e.iteration == iteration]

    def successful(self) -> List[EvidenceEntry]:
        """Return entries that are NOT errors."""
        return [e for e in self._entries if not e.is_error]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def persist_to(self, conversation: Any) -> None:
        """Append in-memory entries to the conversation's evidence log.

        Existing entries in the conversation context are preserved; new
        entries from this run are appended.  This method does NOT call
        ``conversation.save()``; the caller must do that.

        Args:
            conversation: jvspatial Conversation node.
        """
        context = getattr(conversation, "context", None)
        if not isinstance(context, dict):
            logger.warning("EvidenceLog.persist_to: conversation has no context dict")
            return
        existing: List[Dict[str, Any]] = list(context.get(_CONTEXT_KEY) or [])
        new_entries = [e.to_dict() for e in self._entries]
        context[_CONTEXT_KEY] = existing + new_entries

    @classmethod
    def load_from(cls, conversation: Any) -> "EvidenceLog":
        """Reconstruct a log from persisted conversation context.

        Useful for inspection / auditing after a run.

        Args:
            conversation: jvspatial Conversation node.

        Returns:
            EvidenceLog populated with all previously persisted entries.
        """
        log = cls()
        context = getattr(conversation, "context", None)
        if not isinstance(context, dict):
            return log
        raw_entries = context.get(_CONTEXT_KEY) or []
        for raw in raw_entries:
            if isinstance(raw, dict):
                try:
                    log._entries.append(EvidenceEntry.from_dict(raw))
                except Exception as exc:
                    logger.warning(
                        "EvidenceLog.load_from: skipping malformed entry: %s", exc
                    )
        return log
