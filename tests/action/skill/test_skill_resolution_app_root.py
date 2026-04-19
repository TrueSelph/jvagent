"""Tests for app-root-aware skill resolution via SkillCatalog."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.skill.skill_catalog import SkillCatalog


@pytest.mark.asyncio
async def test_discover_skill_bundles_uses_configured_app_root():
    visitor = MagicMock()
    visitor._agent = SimpleNamespace(namespace="demo", name="assistant")

    with patch(
        "jvagent.action.skill.skill_catalog.get_app_root",
        return_value="/tmp/custom-app-root",
    ), patch(
        "jvagent.action.skill.skill_catalog.resolve_merged_skill_bundles",
        return_value={"resolved_skill": {"description": "resolved"}},
    ) as mocked_resolver:
        catalog = await SkillCatalog.discover(
            visitor=visitor,
            skills_selector="-all",
            skills_source="both",
        )

    assert "resolved_skill" in catalog.skills
    assert mocked_resolver.call_args.kwargs["app_root"] == "/tmp/custom-app-root"
