"""Backward-compatible re-exports for interview SOP composition (ADR-0020).

Runtime composition now lives in ``jvagent.scaffold.sop_extend``; skills declare
``extends: action:jvagent/interview_action`` in frontmatter instead of implicit
injection via ``requires-actions``.
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


def is_interview_skill_bundle(bundle: dict) -> bool:
    """Deprecated: interview detection via requires-actions is no longer used for SOP compose."""
    return False


__all__ = [
    "compose_interview_skill_body",
    "compose_interview_skill_body_from_bundle",
    "get_standard_interview_procedure",
    "is_interview_skill_bundle",
]
