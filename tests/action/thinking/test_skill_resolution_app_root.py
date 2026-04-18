"""Tests for app-root-aware skill resolution in ThinkingInteractAction."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from jvagent.action.thinking.thinking_interact_action import ThinkingInteractAction


def _make_action() -> MagicMock:
    action = MagicMock(spec=ThinkingInteractAction)
    action.skills = "-all"
    action.skills_source = "both"
    action.denied_skills = []
    return action


@pytest.mark.asyncio
async def test_discover_skill_bundles_uses_configured_app_root():
    action = _make_action()
    visitor = MagicMock()
    visitor._agent = SimpleNamespace(namespace="demo", name="assistant")

    with patch(
        "jvagent.action.thinking.thinking_interact_action.get_app_root",
        return_value="/tmp/custom-app-root",
    ), patch(
        "jvagent.action.thinking.thinking_interact_action.resolve_merged_skill_bundles",
        return_value={"resolved_skill": {"description": "resolved"}},
    ) as mocked_resolver:
        discovered = await ThinkingInteractAction._discover_skill_bundles(
            action, visitor
        )

    assert "resolved_skill" in discovered
    assert mocked_resolver.call_args.kwargs["app_root"] == "/tmp/custom-app-root"
