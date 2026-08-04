"""Skill-only tool gating for the Orchestrator (ADR-0043).

A *gated* tool is one an operator has listed in ``skill_only_tools``: it stays on
the full surface (so ``find_tool`` can point at it) but refuses to run unless a
skill that declares it in ``allowed-tools`` is active. Config marks WHICH tools
are gated; ownership is derived from the skill docs, never restated in YAML, so
the two cannot drift.

The gate is installed on the tool object itself rather than checked at the loop's
dispatch site, so every dispatch path is covered — not only the one in
``loop.py``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkillGate:
    """Which skills own which gated tools, and which skills are always in force."""

    owners: Dict[str, Tuple[str, ...]]
    always_on: frozenset

    def owners_for(self, name: str) -> Tuple[str, ...]:
        """Skills declaring *name* in their ``allowed-tools`` (empty = orphan)."""
        return self.owners.get(name, ())

    def is_open(self, name: str, activated: Iterable[str]) -> bool:
        """True when an owning skill is active this turn.

        Active means: activated (``use_skill``, auto-start, or holding the
        turn-lock — all of which append to ``activated``), or ``always-active``.
        An orphan is never open: config said "only via a skill" and no skill
        provides it, so it fails closed.
        """
        owning = self.owners.get(name, ())
        if not owning:
            return False
        active = set(activated or ())
        return any(o in active or o in self.always_on for o in owning)


def build_skill_gate(gated: Set[str], docs: Iterable[Any]) -> SkillGate:
    """Index ``gated`` tools by the skills declaring them, warning on orphans.

    ``docs`` must be the already-filtered skill docs the surface will offer
    (post ``requires-actions``, post per-channel gate) — a skill the model cannot
    reach must not confer ownership.
    """
    owners: Dict[str, Tuple[str, ...]] = {}
    always_on: Set[str] = set()
    for doc in docs or ():
        name = str(getattr(doc, "name", "") or "")
        if not name:
            continue
        for tool_name in getattr(doc, "requires_tools", ()) or ():
            if tool_name in gated:
                existing = owners.get(tool_name, ())
                if name not in existing:
                    owners[tool_name] = existing + (name,)
        if getattr(doc, "always_active", False):
            always_on.add(name)
    orphans = sorted(set(gated) - set(owners))
    if orphans:
        logger.warning(
            "orchestrator: skill_only_tools matched tools no available skill "
            "declares — uncallable this turn: %s",
            orphans,
        )
    return SkillGate(owners=owners, always_on=frozenset(always_on))


def skill_only_steer(name: str, owning: Tuple[str, ...]) -> str:
    """The observation returned when a gated tool is called with no owner active."""
    if not owning:
        # End the line of attack explicitly: without this the model retries the
        # same call into the repeat-guard and loses the turn to a condition it
        # can never satisfy.
        return (
            f"({name} is not directly callable and no available skill provides "
            "it. Tell the user you cannot do that; do not retry.)"
        )
    return (
        f"({name} is only available inside a skill. Call use_skill with one of: "
        f"{', '.join(owning)} — then call {name} again.)"
    )


def install_skill_gate(
    tools: Dict[str, Any],
    gated: Set[str],
    gate: SkillGate,
    activated: List[str],
) -> None:
    """Wrap each gated tool's runner with the gate, in place.

    ``activated`` is captured by reference, so a skill activated mid-loop opens
    its tools on the next tick without re-assembling the surface.
    """
    for name in sorted(gated):
        tool = tools.get(name)
        if tool is None:
            continue
        tools[name] = replace(tool, run=_gated_runner(name, tool.run, gate, activated))


def _gated_runner(
    name: str,
    inner: Callable[[Dict[str, Any]], Awaitable[str]],
    gate: SkillGate,
    activated: List[str],
):
    """Bind one tool's guard (a factory, so the loop variable isn't captured)."""

    async def _run(args: Dict[str, Any]) -> str:
        if gate.is_open(name, activated):
            return await inner(args)
        return skill_only_steer(name, gate.owners_for(name))

    return _run


__all__ = [
    "SkillGate",
    "build_skill_gate",
    "install_skill_gate",
    "skill_only_steer",
]
