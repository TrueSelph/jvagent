"""SkillExecutive pattern (ADR-0012).

A single model-driven orchestrator (``SkillExecutiveInteractAction``, weight
``-200``) that runs the whole turn inside one ``execute()`` call: it curates
routable IAs out of the walker queue, then runs a bounded think-act-observe loop
over a unified tool surface (action tools, anchored IAs-as-tools, persona
``reply``/``respond`` tools, core tools, and skill meta-tools), with a
progressive-disclosure tool catalog to keep the prompt slim. An in-progress flow
is surfaced as routable context and continues when the model selects its tool.

Routing is tool selection; turn-lock is an emergent flow property. See
``.planning/adr/0012-skill-executive-architecture.md``.
"""

from jvagent.action.skill_executive.skill_executive_interact_action import (
    SkillExecutiveInteractAction,
)

__all__ = [
    "SkillExecutiveInteractAction",
]
