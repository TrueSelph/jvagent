"""Tests confirming interview__init is no longer an LLM-callable tool."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from jvagent.action.interview_action.contract_loader import (
    ContractRegistry,
    load_contract,
)
from jvagent.action.interview_action.tools import build_tools

_SKILLS_DIR = Path(__file__).resolve().parent / "fixtures/skills"


def _action_with_contracts():
    action = MagicMock()
    registry = ContractRegistry()
    registry._contracts["onboarding_interview"] = load_contract(
        str(_SKILLS_DIR / "onboarding_interview/contract.yaml")
    )
    registry._contracts["pre_alert_interview"] = load_contract(
        str(_SKILLS_DIR / "pre_alert_interview/contract.yaml")
    )
    action._contract_registry = registry
    return action


def test_interview_init_tool_removed_from_surface():
    action = _action_with_contracts()
    names = {t.name for t in build_tools(action)}
    assert "interview__init" not in names
    assert "interview__set_field" in names
    assert "interview__next_question" in names
