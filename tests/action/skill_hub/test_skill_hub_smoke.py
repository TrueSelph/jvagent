"""Minimal smoke: skill_hub action import and instantiate."""

from jvagent.action.skill_hub.skill_hub_action import SkillHubAction


def test_skill_hub_action_instantiates() -> None:
    action = SkillHubAction()
    assert action.__class__.__name__ == "SkillHubAction"
