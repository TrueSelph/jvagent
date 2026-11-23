"""New applications enter the native path without rewriting existing agents."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from jvagent.cli.agent_commands import _handle_agent_create_command
from jvagent.cli.app_commands import _handle_app_create
from jvagent.scaffold.operations import CreateAgentContext, create_agent_in_app


def create(root: Path, *extra: str) -> None:
    _handle_app_create(
        [
            "--yes",
            "--dir",
            str(root),
            "--app-id",
            "native_test",
            "--title",
            "Native Test",
            "--description",
            "Scaffold qualification",
            "--author",
            "Tester",
            "--agent",
            "acme/first",
            "--no-git",
            *extra,
        ],
        default_cwd=str(root),
    )


def native_context(root: Path, reference: str) -> dict[str, Any]:
    document = yaml.safe_load((root / "agents" / reference / "agent.yaml").read_text())
    assert {a["action"] for a in document["actions"]} == {
        "jvagent/native_harness",
        "jvagent/reply",
    }
    return next(
        a["context"]
        for a in document["actions"]
        if a["action"] == "jvagent/native_harness"
    )


def test_cli_defaults_create_native_app_and_agent(tmp_path: Path) -> None:
    root = tmp_path / "application"
    create(root)
    first = (root / "agents/acme/first/agent.yaml").read_bytes()
    _handle_agent_create_command(["other/second"], app_root=str(root))
    assert (root / "agents/acme/first/agent.yaml").read_bytes() == first
    for reference in ("acme/first", "other/second"):
        context = native_context(root, reference)
        assert context["model"] == "openai:gpt-4.1"
        assert context["skill_directory"] == f"agents/{reference}/skills/conversation"
        assert "__JVAGENT_SCAFFOLD" not in str(context)
        assert "skill_runtime" not in context and "model_action_type" not in context
        assert (root / context["skill_directory"] / "SKILL.md").is_file()
        assert (
            "jvagent[native-harness]"
            in (root / "agents" / reference / "README.md").read_text()
        )
    assert "jvagent[native-harness]" in (root / "README.md").read_text()
    assert "JVSPATIAL_JWT_SECRET_KEY" in (root / "README.md").read_text()
    assert "extends: native" in (root / "profiles/examples/custom.yaml").read_text()


@pytest.mark.parametrize("source", ["api", "cli"])
def test_force_preserves_authored_skill(tmp_path: Path, source: str) -> None:
    root = tmp_path / "application"
    create(root)
    asset = root / "agents/acme/first/skills/conversation/SKILL.md"
    authored = asset.read_text() + "\nAn authored instruction that must survive.\n"
    asset.write_text(authored)
    if source == "cli":
        create(root, "--force")
    else:
        create_agent_in_app(
            CreateAgentContext(app_root=root, agent_spec="acme/first", force=True)
        )
    assert asset.read_text() == authored


@pytest.mark.parametrize("target", ["skills", "skill_file"])
def test_native_starter_refuses_symlink_escape(tmp_path: Path, target: str) -> None:
    root = tmp_path / "application"
    create(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    asset = root / "agents/acme/first/skills/conversation/SKILL.md"
    if target == "skills":
        asset.unlink()
        asset.parent.rmdir()
        asset.parent.parent.rmdir()
        asset.parent.parent.symlink_to(outside, target_is_directory=True)
    else:
        sentinel = outside / "SKILL.md"
        sentinel.write_text("Private external instructions")
        asset.unlink()
        asset.symlink_to(sentinel)
    before = {p.name: p.read_bytes() for p in outside.iterdir() if p.is_file()}
    with pytest.raises(ValueError, match="escapes base"):
        create_agent_in_app(
            CreateAgentContext(app_root=root, agent_spec="acme/first", force=True)
        )
    assert {p.name: p.read_bytes() for p in outside.iterdir() if p.is_file()} == before


@pytest.mark.parametrize("native", [True, False])
def test_generated_docker_dependencies_follow_execution_profile(
    tmp_path: Path, native: bool
) -> None:
    extra = ["--deployment", "aws-lambda"]
    if not native:
        extra.extend(["--profile", "minimal"])
    create(tmp_path, *extra)
    docker = (tmp_path / "Dockerfile").read_text()
    assert ('"jvagent[native-harness]"' in docker) is native


@pytest.mark.asyncio
async def test_generated_skill_is_loadable_by_official_skills(tmp_path: Path) -> None:
    pytest.importorskip("pydantic_ai_harness")
    from pydantic_ai.workspaces import LocalWorkspaceBackend
    from pydantic_ai_harness import Skills

    create(tmp_path)
    context = native_context(tmp_path, "acme/first")
    directory = tmp_path / context["skill_directory"]
    catalog = await Skills(
        ".", include=[directory.name], workspace=LocalWorkspaceBackend(directory.parent)
    ).load()
    assert len(catalog.skills) == 1 and not catalog.skipped
    assert catalog.skills[0].model_invocable
