"""Standard interview SOP loading and composition for orchestrator skill discovery."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .interview_loader import INTERVIEW_FRONTMATTER_KEY

logger = logging.getLogger(__name__)

_INTERVIEW_ACTION = "InterviewAction"
_PROCEDURE_FILE = Path(__file__).resolve().parent / "sop" / "standard_procedure.md"


@lru_cache(maxsize=1)
def get_standard_interview_procedure() -> str:
    """Load the framework-standard interview procedure (memoized)."""
    try:
        return _PROCEDURE_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.error(
            "Failed to load standard interview procedure from %s: %s",
            _PROCEDURE_FILE,
            exc,
        )
        return (
            "# Standard Interview Procedure\n\n"
            "Use interview tools; ask only from `next_questions`; "
            "call `interview__review` before `interview__complete`."
        )


def compose_interview_skill_body(custom_body: str = "") -> str:
    """Prepend standard procedure to per-skill custom markdown."""
    standard = get_standard_interview_procedure()
    custom = (custom_body or "").strip()
    if not custom:
        return standard
    return f"{standard}\n\n{custom}"


def is_interview_skill_bundle(bundle: Mapping[str, Any]) -> bool:
    """True when bundle is an InterviewAction skill with frontmatter interview spec."""
    requires = bundle.get("requires_actions") or ()
    if isinstance(requires, str):
        requires = (requires,)
    if _INTERVIEW_ACTION not in {str(r).strip() for r in requires if str(r).strip()}:
        return False
    interview = bundle.get(INTERVIEW_FRONTMATTER_KEY)
    return isinstance(interview, dict) and bool(interview)


def compose_interview_skill_body_from_bundle(bundle: Mapping[str, Any]) -> str:
    """Compose full SkillDoc body from a parsed skill bundle."""
    return compose_interview_skill_body(str(bundle.get("content") or ""))
