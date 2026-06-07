"""Skills-v2 interview framework for structured multi-turn data collection.

``InterviewAction`` registers ``interview__*`` tools; the orchestrator LLM drives
each turn via skill ``SKILL.md`` procedures. Custom skills live under
``skills/<name>/`` with ``interview.yaml``, ``SKILL.md``, and
``scripts/custom_tools.py``.

Documentation: ``README.md``, ``CLAUDE.md``, ``docs/``.
"""

from .interview_action import InterviewAction

__all__ = ["InterviewAction"]

from . import endpoints  # noqa: F401 — route registration side effect if added later
