"""Skills-v2 interview framework for structured multi-turn data collection.

``InterviewAction`` registers ``interview__*`` tools; the orchestrator LLM drives
each turn via skill ``SKILL.md`` procedures. Live skills live in app-local
``agents/.../skills/<name>/`` (or optionally under action overlay paths). This
package has no ``skills/`` subdir. Reference templates are under ``examples/``
(not discovered). Declare ``extends: action:jvagent/interview_action``.

Documentation: ``README.md``, ``CLAUDE.md``, ``docs/``.
"""

from .core.procedure import (
    compose_interview_skill_body,
    compose_interview_skill_body_from_bundle,
    get_standard_interview_procedure,
)
from .interview_action import InterviewAction

__all__ = [
    "InterviewAction",
    "compose_interview_skill_body",
    "compose_interview_skill_body_from_bundle",
    "get_standard_interview_procedure",
]
