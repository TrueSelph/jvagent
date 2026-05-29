"""Capability registry (ADR-0010 §2.1) — the Executive's map of its centers.

One tiered registry unifies the things the agent can do: rails ``InteractAction``
pathways (``kind="ia"``) and skills (``kind="skill"``). The Executive reads a
token-bounded **routing view** to decide what to activate; the IA / Skills
centers read **execution views** to act; the deterministic reflex path uses the
**anchors** to short-circuit the Executive entirely.

Each capability names the **center** that handles it (e.g. an anchored IA is
handled by the IA center). This keeps the depth-1 star: the reflex/Executive
activate a *center*, and the center runs the underlying IA/skill.

This module is pattern-local and imports nothing from other patterns.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Capability:
    """One thing the agent can do, and which center handles it."""

    id: str
    kind: str  # "ia" | "skill"
    center: str  # center that handles this capability (e.g. "IACenter")
    summary: str = ""
    anchors: Tuple[str, ...] = ()
    anchor_patterns: Tuple[str, ...] = ()  # regex strings (case-insensitive)
    latency_class: str = "quick"
    turn_lock: bool = False
    tier: int = 0
    handle: Any = None  # IA package/class name or skill definition

    def routing_line(self) -> Dict[str, Any]:
        """Compact dict for the Executive routing prompt (no execution handle)."""
        return {
            "id": self.id,
            "kind": self.kind,
            "center": self.center,
            "summary": self.summary,
            "anchors": list(self.anchors),
            "latency_class": self.latency_class,
        }

    def matches(self, utterance_lower: str) -> bool:
        """True if an exact/substring anchor or a regex pattern matches."""
        for a in self.anchors:
            al = a.strip().lower()
            if al and al in utterance_lower:
                return True
        for pat in self.anchor_patterns:
            try:
                if re.search(pat, utterance_lower, re.IGNORECASE):
                    return True
            except re.error as exc:
                logger.debug("registry: bad anchor pattern %r: %s", pat, exc)
        return False


@dataclass
class CapabilityRegistry:
    """An ordered collection of :class:`Capability` with views + anchor match."""

    capabilities: List[Capability] = field(default_factory=list)

    def all(self) -> List[Capability]:
        return list(self.capabilities)

    def by_kind(self, kind: str) -> List[Capability]:
        return [c for c in self.capabilities if c.kind == kind]

    def by_id(self, cap_id: str) -> Optional[Capability]:
        for c in self.capabilities:
            if c.id == cap_id:
                return c
        return None

    def routing_view(self, *, max_tier: Optional[int] = None) -> List[Dict[str, Any]]:
        """Tier-filtered compact list for the Executive's routing prompt.

        ``max_tier=None`` includes everything; otherwise only capabilities with
        ``tier <= max_tier`` (lower tier = more prominent / cheaper to surface).
        """
        caps = sorted(self.capabilities, key=lambda c: (c.tier, c.id))
        if max_tier is not None:
            caps = [c for c in caps if c.tier <= max_tier]
        return [c.routing_line() for c in caps]

    def execution_view(self, kind: str) -> List[Capability]:
        """Full capabilities of a kind for the handling center."""
        return self.by_kind(kind)

    def match_anchor(self, utterance: str) -> Optional[Capability]:
        """Deterministic anchor match (lowest tier wins). ``None`` on no match."""
        u = (utterance or "").strip().lower()
        if not u:
            return None
        for cap in sorted(self.capabilities, key=lambda c: (c.tier, c.id)):
            if cap.matches(u):
                return cap
        return None


def build_registry_from_agent(
    agent: Any,
    *,
    ia_center: str,
    enabled_actions: Optional[List[Any]] = None,
) -> CapabilityRegistry:
    """Build a registry of anchored rails IAs from the agent's action set.

    Enumerates ``InteractAction``s that declare anchors and are NOT pattern
    orchestrators (Executive/Bridge/Cockpit), mapping each to the IA center.
    Skills enumeration is added in a later milestone. Best-effort: never
    raises; returns whatever it could resolve.

    ``enabled_actions`` may be supplied directly (tests); otherwise the caller
    is expected to pre-resolve them and pass them in, since enumeration is
    async at the agent level.
    """
    caps: List[Capability] = []
    actions = enabled_actions or []
    for action in actions:
        try:
            anchors = list(getattr(action, "anchors", []) or [])
            if not anchors:
                continue
            manifest = None
            get_manifest = getattr(action, "get_manifest", None)
            if callable(get_manifest):
                try:
                    manifest = get_manifest()
                except Exception:
                    manifest = None
            if manifest is not None and getattr(
                manifest, "pattern_orchestrator", False
            ):
                continue
            if manifest is not None and not getattr(
                manifest, "routable_by_anchor", True
            ):
                continue
            name = action.__class__.__name__
            caps.append(
                Capability(
                    id=name,
                    kind="ia",
                    center=ia_center,
                    summary=(getattr(manifest, "purpose", "") if manifest else "")
                    or getattr(action, "description", ""),
                    anchors=tuple(anchors),
                    latency_class=(
                        getattr(manifest, "latency_class", "quick")
                        if manifest
                        else "quick"
                    ),
                    turn_lock=(
                        getattr(manifest, "turn_lock", False) if manifest else False
                    ),
                    handle=name,
                )
            )
        except Exception as exc:  # pragma: no cover (defensive)
            logger.debug("registry: skipping action during build: %s", exc)
    return CapabilityRegistry(capabilities=caps)


__all__ = ["Capability", "CapabilityRegistry", "build_registry_from_agent"]
