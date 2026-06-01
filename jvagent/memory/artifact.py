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
        default="", description='Producer, e.g. "vision", "web_fetch".'
    )
    kind: str = attribute(default="text", description="Payload kind (text, json, ...).")
    pinned: bool = attribute(
        default=False,
        description="When True, exempt from refcounted prune (durability opt-out).",
    )
    created_at: datetime = attribute(default_factory=_utcnow)
    updated_at: Optional[datetime] = attribute(default=None)

    def index_row(self) -> Dict[str, Any]:
        """Compact, payload-free entry for the prompt-side artifact index."""
        return {
            "name": self.name,
            "source": self.source,
            "tags": list(self.tags or []),
            "summary": self.summary,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


__all__ = ["Artifact", "Artifacts"]
