"""Shared paths for interview tests that use the example signup skill."""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]

ORCHESTRATOR_AGENT_DIR = (
    _REPO_ROOT / "examples/jvagent_app/agents/jvagent/orchestrator_agent"
)
SIGNUP_INTERVIEW_SKILL_DIR = ORCHESTRATOR_AGENT_DIR / "skills/signup_interview"
