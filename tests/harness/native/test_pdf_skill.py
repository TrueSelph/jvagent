"""The shipped PDF asset loads natively and keeps rendering outside the host."""

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pydantic_ai_harness")

from jvagent.harness.script_sandbox import NativeScriptSandbox
from jvagent.harness.skill_assets import library_skill_directory
from jvagent.harness.skill_catalog import NativeSkillCatalog


@pytest.mark.asyncio
async def test_shipped_pdf_skill_loads_with_matching_script_namespace() -> None:
    directory = library_skill_directory("pdf-generation")
    catalog = NativeSkillCatalog(
        primary=directory,
        directories=[],
        action_bases=None,
        available_actions=["CodeExecutionAction", "FileInterfaceAction"],
        available_tools=["code_execution__bash", "file_interface__share_file"],
        channel="default",
    )
    await catalog.preflight()
    assert catalog.primary == "pdf-generation"
    assets = NativeScriptSandbox(image="python@sha256:" + "0" * 64).assets(
        catalog.directories
    )
    path = "staged_skills/pdf-generation/scripts/render_pdf.py"
    assert path in assets
    assert path in (directory / "SKILL.md").read_text()
    assert catalog.tool_owners["file_interface__share_file"] == {"pdf-generation"}


def test_local_built_image_requires_full_content_identity() -> None:
    image = "sha256:" + "1" * 64
    assert NativeScriptSandbox(image=image).image == image
    for mutable in ("native-pdf:latest", "111111111111", "sha256:123"):
        with pytest.raises(ValueError):
            NativeScriptSandbox(image=mutable)


def test_missing_pdf_engine_fails_without_creating_an_artifact(tmp_path: Path) -> None:
    directory = library_skill_directory("pdf-generation")
    (tmp_path / "document.md").write_text("# Native PDF\n\nAn honest failure.\n")
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            str(directory / "scripts/render_pdf.py"),
            "--input",
            "document.md",
            "--output",
            "output/report.pdf",
        ],
        cwd=tmp_path,
        env={"PATH": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 1
    assert "No PDF engine available" in result.stderr
    assert not (tmp_path / "output/report.pdf").exists()
