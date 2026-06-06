"""Tests for skill-prefixed custom interview tool registration."""

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
    registry._contracts["pre_alert_interview"] = load_contract(
        str(_SKILLS_DIR / "pre_alert_interview/contract.yaml")
    )
    registry._contracts["onboarding_interview"] = load_contract(
        str(_SKILLS_DIR / "onboarding_interview/contract.yaml")
    )
    action._contract_registry = registry
    return action


def test_custom_tools_use_skill_prefix():
    action = _action_with_contracts()
    tools = build_tools(action)
    names = {t.name for t in tools}

    assert "pre_alert_interview__check_tracking_status" not in names
    assert "pre_alert_interview__check_pre_alert_intent" not in names
    assert "onboarding_interview__process_id_card" in names
    assert "onboarding_interview__reset_onboarding" in names
    assert "onboarding_interview__verify_phone_number" not in names
    assert "onboarding_interview__verify_email" not in names
    assert "onboarding_interview__confirm_otp_code" not in names
    assert "onboarding_interview__send_otp" in names
    assert "onboarding_interview__extract_id_card" not in names
    assert "onboarding_interview__save_contact_number" not in names
    assert "onboarding_interview__create_account" not in names
    assert "interview__check_pre_alert_intent" not in names
    assert "interview__extract_id_card" not in names


def test_core_tools_keep_interview_prefix():
    action = _action_with_contracts()
    tools = build_tools(action)
    names = {t.name for t in tools}

    assert "interview__init" not in names
    assert "interview__set_field" in names
    assert "interview__next_question" in names
    assert "interview__next_question" in names
    assert "interview__review" in names
