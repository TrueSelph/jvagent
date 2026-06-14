"""Conversation-scoped artifact memory (ADR-0021).

An ``Artifact`` is an adjunct contextual reference (e.g. a vision
interpretation, a file analysis, a web-fetch extract) that the orchestrator can
back-reference across turns. Artifacts live in a single ``Artifacts`` branch/
registry node hanging off the ``Conversation`` (queryable in one traversal),
and associate to the producing ``Interaction(s)`` via a generic edge
(``PRODUCED`` semantics). Lifecycle is **refcounted** to those interactions:
when an interaction is pruned, an artifact it solely produced is reaped unless
``pinned`` (see ``Conversation._reap_artifacts_for``). This keeps the registry
bounded by the existing ``interaction_limit`` rolling window — no separate
artifact-pruning system.

Graph shape::

    Conversation ──▶ Artifacts (branch) ──▶ Artifact*     (CONTAINS / registry membership)
    Interaction  ──▶ Artifact*                             (PRODUCED / provenance; many-to-many)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from jvspatial.core import Node
from jvspatial.core.annotations import attribute


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Artifacts(Node):
    """Branch/registry node grouping a Conversation's ``Artifact`` nodes.

    One per conversation, lazily created on first ``add_artifact``. Membership
    is the ``Artifacts ──▶ Artifact`` edge; query via
    ``branch.nodes(node=Artifact, direction="out")``.
    """

    label: str = attribute(default="artifacts")


class Artifact(Node):
    """A conversation-scoped adjunct context item (ADR-0021)."""

    name: str = attribute(default="", description="Stable handle used by get_artifact.")
    data: str = attribute(
        default="", description="Full payload (e.g. the interpretation text)."
    )
    summary: str = attribute(
        default="",
        description="Short line surfaced in the artifact index (no payload).",
    )
    tags: List[str] = attribute(default_factory=list)
    source: str = attribute(
        default="", description='Producer, e.g. "vision", "upload", "web_fetch".'
    )
    kind: str = attribute(
        default="text", description="Payload kind (text, json, image, file, ...)."
    )
    pinned: bool = attribute(
        default=False,
        description="When True, exempt from refcounted prune (durability opt-out).",
    )
    # File-backed artifacts (ADR-0021 S4): an uploaded file's bytes live in the
    # configured file storage (per-user sandbox), NOT inline on the node — keeping
    # the graph lean. ``path`` is the storage-relative key; ``data`` holds a
    # readable descriptor (and the decoded text for text files). The bytes are
    # reaped with the artifact (see ``Conversation._reap_artifacts_for``).
    filename: str = attribute(default="", description="Original upload filename.")
    mime: str = attribute(default="", description="MIME type of the stored file.")
    size: int = attribute(default=0, description="Stored file size in bytes.")
    path: str = attribute(
        default="",
        description="Storage-relative key where the bytes live (empty = no file).",
    )
    created_at: datetime = attribute(default_factory=_utcnow)
    updated_at: Optional[datetime] = attribute(default=None)

    def index_row(self) -> Dict[str, Any]:
        """Compact, payload-free entry for the prompt-side artifact index."""
        row: Dict[str, Any] = {
            "name": self.name,
            "source": self.source,
            "kind": self.kind,
            "tags": list(self.tags or []),
            "summary": self.summary,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if self.filename:
            row["filename"] = self.filename
        if self.mime:
            row["mime"] = self.mime
        return row


__all__ = ["Artifact", "Artifacts"]
