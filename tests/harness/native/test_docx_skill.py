"""Existing DOCX assets use public Skills and the existing bounded script path."""

from pathlib import Path

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai_harness import Skills

from jvagent.harness.script_sandbox import NativeScriptSandbox
from jvagent.harness.skill_admission import admit_skill
from jvagent.harness.skill_assets import project_skill, projected_workspace

DIRECTORY = Path("examples/jvagent_app/agents/jvagent/orchestrator_agent/skills/docx")


@pytest.mark.asyncio
async def test_existing_docx_skill_admits_and_loads_officially() -> None:
    metadata, document = project_skill(DIRECTORY)
    admit_skill(
        DIRECTORY,
        metadata=metadata,
        available_actions=["CodeExecutionAction"],
        available_tools=["code_execution__bash", "file_interface__share_file"],
        channel="web",
    )
    catalog = await Skills(
        ".", include=["docx"], workspace=projected_workspace(DIRECTORY, document)
    ).load()
    assert [s.name for s in catalog.skills] == ["docx"]
    assert catalog.skills[0].model_invocable
    assert "staged_skills/docx/scripts/office/validate.py" in document


def test_docx_requires_declared_execution_action_before_loading() -> None:
    metadata, _ = project_skill(DIRECTORY)
    with pytest.raises(ValueError, match="Required Actions unavailable"):
        admit_skill(
            DIRECTORY,
            metadata=metadata,
            available_actions=[],
            available_tools=["code_execution__bash"],
            channel="web",
        )


def test_docx_scripts_and_schemas_fit_existing_staging_bounds() -> None:
    sandbox = NativeScriptSandbox(image="sha256:" + "0" * 64)
    assets = sandbox.assets([DIRECTORY])
    authored = {
        "staged_skills/docx/" + p.relative_to(DIRECTORY).as_posix(): p.read_bytes()
        for p in (DIRECTORY / "scripts").rglob("*")
        if p.is_file()
    }
    assert assets == authored
    assert "staged_skills/docx/scripts/office/validate.py" in assets
    assert any(name.endswith(".xsd") for name in assets)
