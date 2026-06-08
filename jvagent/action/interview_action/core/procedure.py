"""Interview SOP composition helpers (ADR-0020).

Runtime composition lives in ``jvagent.scaffold.sop_extend``; skills declare
``extends: action:jvagent/interview_action`` in frontmatter.
"""

from __future__ import annotations

from jvagent.scaffold.sop_extend import compose_skill_body, load_action_base_sop_body

_INTERVIEW_ACTION_REF = "jvagent/interview_action"


def get_standard_interview_procedure() -> str:
    """Load the framework-standard interview procedure from action-root SKILL.md."""
    return load_action_base_sop_body(_INTERVIEW_ACTION_REF)


def compose_interview_skill_body(custom_body: str = "") -> str:
    """Prepend standard procedure to per-skill custom markdown."""
    return compose_skill_body(get_standard_interview_procedure(), custom_body)


def compose_interview_skill_body_from_bundle(bundle: dict) -> str:
    """Compose body using explicit ``extends`` on the bundle (already composed at discovery)."""
    return str(bundle.get("content") or "").strip()


__all__ = [
    "compose_interview_skill_body",
    "compose_interview_skill_body_from_bundle",
    "get_standard_interview_procedure",
]
