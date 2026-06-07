"""Skills-v2 interview framework for structured multi-turn data collection.

``InterviewAction`` registers ``interview__*`` tools; the orchestrator LLM drives
each turn via skill ``SKILL.md`` procedures. Custom skills live under
``skills/<name>/`` with ``SKILL.md`` (frontmatter ``interview:`` block) and
``scripts/custom_tools.py``.

Documentation: ``README.md``, ``CLAUDE.md``, ``docs/``.
"""

from .interview_action import InterviewAction
from .procedure import (
    compose_interview_skill_body,
    compose_interview_skill_body_from_bundle,
    get_standard_interview_procedure,
    is_interview_skill_bundle,
)

__all__ = [
    "InterviewAction",
    "compose_interview_skill_body",
    "compose_interview_skill_body_from_bundle",
    "get_standard_interview_procedure",
    "is_interview_skill_bundle",
]

from . import endpoints  # noqa: F401 — route registration side effect if added later
