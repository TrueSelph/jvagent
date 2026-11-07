"""Unadapted WhatsApp tools must not acquire native execution authority."""

from pathlib import Path

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.models.test import TestModel

from jvagent.action.whatsapp.whatsapp_action import WhatsAppAction
from jvagent.harness.native import build_native_agent
from jvagent.tooling.tool_decorator import collect_tools


@pytest.mark.parametrize(
    "name",
    [
        "whatsapp__list_templates",
        "whatsapp__send_template",
        "whatsapp__list_flows",
        "whatsapp__send_flow",
    ],
)
def test_legacy_whatsapp_tools_require_explicit_native_adaptation(
    tmp_path: Path, name: str
) -> None:
    """Even eligible skills and permissive host policy cannot qualify a tool."""
    skill = tmp_path / "audit-whatsapp"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: audit-whatsapp\ndescription: Native admission test.\n"
        f"allowed-tools:\n  - {name}\n---\nUse the declared tool.\n"
    )
    original = next(t for t in collect_tools(WhatsAppAction()) if t.name == name)

    async def allowed(_: str) -> bool:
        return True

    with pytest.raises(
        ValueError, match=f"Action is not qualified for native execution: {name}"
    ):
        build_native_agent(
            TestModel(),
            skill_directory=skill,
            tools=[original],
            available_actions=["WhatsAppAction"],
            channel="whatsapp",
            access_check=allowed,
            instructions="Offline admission test.",
        )
