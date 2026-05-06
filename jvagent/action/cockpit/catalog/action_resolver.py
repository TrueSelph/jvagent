"""ActionResolver and version utilities for cockpit."""

from __future__ import annotations

import logging
import re as _re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Version constraint helper
# ---------------------------------------------------------------------------


def version_satisfies(actual: str, constraint: str) -> bool:
    """Return True if *actual* satisfies simple *constraint*.

    Supported operators: ``>=``, ``>``, ``<=``, ``<``, ``==``, ``~``, ``^``.
    Bare constraint versions are treated as ``>=``.
    """
    if not actual or not constraint:
        return False

    constraint = str(constraint).strip()
    actual = str(actual).strip()

    m = _re.match(r"^\s*(>=|>|<=|<|==|~|\^)?\s*(.+)$", constraint)
    if not m:
        return False
    op = m.group(1) or ">="
    target = m.group(2).strip()

    def _parse(v: str) -> Tuple[int, ...]:
        parts: List[int] = []
        for p in v.split("."):
            try:
                parts.append(int(p))
            except ValueError:
                break
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts)

    a = _parse(actual)
    t = _parse(target)

    if op == ">=":
        return a >= t
    if op == ">":
        return a > t
    if op == "<=":
        return a <= t
    if op == "<":
        return a < t
    if op == "==":
        return a == t
    if op == "~":
        return a[0] == t[0] and a[1] >= t[1]
    if op == "^":
        return a[0] == t[0] and a >= t
    return False


# ---------------------------------------------------------------------------
# ActionResolver
# ---------------------------------------------------------------------------


class ActionResolver:
    """Resolve graph-persisted Actions by entity type for skill tool modules.

    Attached to ``visitor.action_resolver`` by the cockpit action during
    execute(). Skill tools that accept a ``visitor`` kwarg can access it via
    ``visitor.action_resolver.resolve("GoogleCalendarAction")``.

    Results are cached per entity_type for the lifetime of the resolver
    (i.e. the current interaction).
    """

    def __init__(self, agent: Any) -> None:
        self._agent = agent
        self._cache: Dict[str, Optional[Any]] = {}

    async def resolve(self, entity_type: str) -> Optional[Any]:
        """Return the first Action matching *entity_type*, or None."""
        if entity_type in self._cache:
            return self._cache[entity_type]

        action = await self._agent.get_action_by_type(entity_type)
        self._cache[entity_type] = action
        return action

    async def require(self, entity_type: str) -> Any:
        """Return the Action matching *entity_type*, raising if absent/disabled."""
        action = await self.resolve(entity_type)
        if action is None:
            raise ValueError(f"Required action '{entity_type}' not found on this agent")
        if getattr(action, "enabled", True) is False:
            raise ValueError(f"Required action '{entity_type}' exists but is disabled")
        return action

    async def validate_requirements(self, required_types: List[str]) -> List[str]:
        """Validate all required action types are present and enabled."""
        errors: List[str] = []
        for entity_type in required_types:
            try:
                await self.require(entity_type)
            except ValueError as exc:
                errors.append(str(exc))
        return errors

    async def validate_action_ref_versions(
        self, ref_constraints: Dict[str, str]
    ) -> List[str]:
        """Validate ``namespace/label`` package refs against installed action versions."""
        from jvagent.action.base import Action

        errors: List[str] = []
        agent_id = getattr(self._agent, "id", None)
        if not agent_id:
            return ["No agent id for action version validation"]

        for ref, constraint in ref_constraints.items():
            ref_key = str(ref).strip()
            cons = str(constraint).strip()
            if not ref_key or not cons:
                continue
            if "/" not in ref_key:
                errors.append(
                    f"Invalid action ref '{ref_key}' (expected namespace/label)"
                )
                continue
            ns, lbl = ref_key.split("/", 1)
            action = await Action.find_one(
                {
                    "context.agent_id": agent_id,
                    "context.namespace": ns,
                    "context.label": lbl,
                }
            )
            if not action:
                errors.append(
                    f"Action '{ref_key}' is not registered — cannot verify {cons}"
                )
                continue
            meta = action.metadata or {}
            ver = str(meta.get("version", "0.0.0")).strip() or "0.0.0"
            if not version_satisfies(ver, cons):
                errors.append(
                    f"Action '{ref_key}' version {ver} does not satisfy {cons}"
                )
        return errors
