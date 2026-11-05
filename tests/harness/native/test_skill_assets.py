"""Inherited SOPs use official Skills, with monotone JV declaration policy."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from jvagent.harness.skill_assets import project_skill


def skill(root: Path, name: str, fields: dict[str, Any], body: str = "") -> Path:
    directory = root / name
    directory.mkdir(exist_ok=True)
    (directory / "SKILL.md").write_text(
        "---\n"
        + yaml.safe_dump({"name": name, "description": name, **fields})
        + "---\n"
        + body
    )
    return directory


def test_inherited_tools_and_denials_cannot_be_erased(tmp_path: Path) -> None:
    skill(
        tmp_path,
        "base",
        {
            "allowed-tools": ["read", "blocked"],
            "disabled-tools": ["blocked"],
            "requires-actions": ["LookupAction"],
            "denied-channels": ["sms"],
            "allowed-channels": ["web", "chat"],
        },
        "BASE",
    )
    child = skill(
        tmp_path,
        "child",
        {
            "extends": "skill:base",
            "allowed-tools": ["extra"],
            "disabled-tools": [],
            "requires-actions": [],
            "denied-channels": [],
            "allowed-channels": ["chat"],
        },
        "CHILD",
    )
    original = (child / "SKILL.md").read_bytes()
    metadata, document = project_skill(child)
    assert metadata["allowed-tools"] == ["read", "extra"]
    assert metadata["requires-actions"] == ["LookupAction"]
    assert metadata["denied-channels"] == ["sms"]
    assert metadata["allowed-channels"] == ["chat"]
    assert "extends" not in metadata
    assert document.endswith("BASE\n\nCHILD\n")
    assert (child / "SKILL.md").read_bytes() == original


@pytest.mark.parametrize(
    "field,value",
    [
        ("task-lock", True),
        ("requires-tasks", ["flow"]),
        ("access-action", "Transfer"),
        ("allowed-groups", ["staff"]),
        ("spec", "claude"),
    ],
)
def test_child_cannot_clear_unsupported_inherited_policy(
    tmp_path: Path, field: str, value: Any
) -> None:
    skill(tmp_path, "base", {field: value})
    child = skill(tmp_path, "child", {"extends": "skill:base", field: None})
    with pytest.raises(ValueError):
        project_skill(child)


@pytest.mark.parametrize(
    "reference", ["skill:../outside", "skill:/outside", "action:missing", "base", 42]
)
def test_invalid_references_fail_closed(tmp_path: Path, reference: Any) -> None:
    child = skill(tmp_path, "child", {"extends": reference})
    with pytest.raises(ValueError):
        project_skill(child)


def test_missing_cycle_and_symlink_escape_fail_closed(tmp_path: Path) -> None:
    child = skill(tmp_path, "child", {"extends": "skill:missing"})
    with pytest.raises(FileNotFoundError):
        project_skill(child)
    skill(tmp_path, "missing", {"extends": "skill:child"})
    with pytest.raises(ValueError, match="cycle"):
        project_skill(child)
    outside = tmp_path / "external" / "secret"
    outside.mkdir(parents=True)
    (tmp_path / "missing").rename(tmp_path / "retired")
    (tmp_path / "missing").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        project_skill(child)


def test_channel_and_output_contract_cannot_be_relaxed(tmp_path: Path) -> None:
    skill(
        tmp_path,
        "base",
        {"output-contract": "evidence_required", "allowed-channels": ["web"]},
    )
    child = skill(tmp_path, "child", {"extends": "skill:base", "output-contract": None})
    with pytest.raises(ValueError, match="output contract"):
        project_skill(child)
    child = skill(
        tmp_path, "child", {"extends": "skill:base", "allowed-channels": ["sms"]}
    )
    with pytest.raises(ValueError, match="intersect"):
        project_skill(child)


def test_action_base_requires_explicit_host_mapping(tmp_path: Path) -> None:
    base = skill(tmp_path, "base", {}, "ACTION SOP")
    child = skill(tmp_path, "child", {"extends": "action:custom/base"}, "CHILD SOP")
    with pytest.raises(ValueError):
        project_skill(child)
    _, text = project_skill(child, action_bases={"custom/base": base})
    assert text.endswith("ACTION SOP\n\nCHILD SOP\n")


@pytest.mark.asyncio
async def test_official_skill_loads_projected_sop_and_checks_inherited_tools(
    tmp_path: Path,
) -> None:
    pytest.importorskip("pydantic_ai_harness")
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.workspaces import WorkspaceReadOnlyError

    from jvagent.harness.native import build_native_agent
    from jvagent.harness.skill_assets import projected_workspace
    from jvagent.tooling.tool import Tool

    skill(
        tmp_path,
        "base",
        {"allowed-tools": ["lookup"], "requires-tools": ["lookup"]},
        "BASE SOP",
    )
    child = skill(tmp_path, "child", {"extends": "skill:base"}, "CHILD SOP")
    calls = []

    async def lookup() -> str:
        calls.append("lookup")
        return "receipt"

    async def check(name: str) -> bool:
        return name == "lookup"

    def respond(messages: Any, info: Any) -> ModelResponse:
        assert "BASE SOP" in str(messages) and "CHILD SOP" in str(messages)
        assert {tool.name for tool in info.function_tools} == {
            "lookup",
            "load_capability",
        }
        if not calls:
            return ModelResponse(parts=[ToolCallPart("lookup", {})])
        return ModelResponse(parts=[TextPart("done")])

    tool = Tool("lookup", "Lookup", execute=lookup, effect_class="read")
    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=child,
        tools=[tool],
        access_check=check,
        instructions="Follow the selected skill.",
    )
    result = await agent.run("Proceed")
    assert result.output == "done" and calls == ["lookup"]
    metadata, text = project_skill(child)
    workspace = projected_workspace(child, text)
    assert await workspace.read_text("child/SKILL.md") == text
    with pytest.raises(WorkspaceReadOnlyError):
        await workspace.write_text("child/SKILL.md", "replace")
    with pytest.raises(WorkspaceReadOnlyError):
        await workspace.run(["echo", "unsafe"])
    with pytest.raises(ValueError, match="Required tools unavailable"):
        build_native_agent(
            FunctionModel(respond),
            skill_directory=child,
            tools=[],
            access_check=check,
            instructions="Host",
        )


def test_deep_and_oversized_inheritance_fail_closed(tmp_path: Path) -> None:
    for index in range(13):
        skill(
            tmp_path,
            f"level-{index}",
            {"extends": f"skill:level-{index + 1}"} if index < 12 else {},
        )
    with pytest.raises(ValueError, match="depth"):
        project_skill(tmp_path / "level-0")
    large = skill(tmp_path, "large", {}, "x" * 256_000)
    with pytest.raises(ValueError, match="size"):
        project_skill(large)


@pytest.mark.parametrize(
    "field,value", [("allowed-tools", "lookup"), ("denied-channels", [None])]
)
def test_malformed_parent_policy_cannot_be_hidden(
    tmp_path: Path, field: str, value: Any
) -> None:
    skill(tmp_path, "base", {field: value})
    child = skill(tmp_path, "child", {"extends": "skill:base", field: []})
    with pytest.raises(ValueError, match="list of nonempty"):
        project_skill(child)
