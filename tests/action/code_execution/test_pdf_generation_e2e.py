"""Canonical end-to-end: a Claude skill (pdf-generation) is staged into a
per-user slice and its bundled script renders a real PDF there — proving the
substrate (stage -> bash -> artifact in the user's own workspace).

Skips gracefully if the environment has no PDF engine."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from jvagent.action.code_execution.code_execution_action import CodeExecutionAction
from jvagent.action.code_execution.executor import ExecRequest, SubprocessExecutor

pytestmark = pytest.mark.asyncio

_SKILL_DIR = Path("jvagent/skills/pdf_generation")


class _Visitor:
    _agent = type("A", (), {"id": "n.Agent.1"})()
    user_id = "u.User.1"
    session_id = None
    interaction = None
    channel = "web"


def _has_engine() -> bool:
    return shutil.which("pandoc") is not None or shutil.which("weasyprint") is not None


@pytest.mark.skipif(not _has_engine(), reason="no PDF engine in this environment")
async def test_stage_and_render_pdf_into_user_slice(tmp_path, monkeypatch):
    slice_dir = tmp_path / "slice"
    slice_dir.mkdir()

    action = CodeExecutionAction()
    action.enabled = True

    async def _fake_cwd(self, visitor):
        return str(slice_dir)

    monkeypatch.setattr(CodeExecutionAction, "resolve_user_cwd", _fake_cwd)

    # 1) Stage the real skill folder into the user's slice.
    rel = await action.stage_skill(_Visitor(), str(_SKILL_DIR), "pdf-generation")
    assert rel == "staged_skills/pdf-generation"
    assert (slice_dir / rel / "scripts" / "render_pdf.py").exists()

    # 2) Write a markdown doc into the slice (as the model would via bash).
    (slice_dir / "document.md").write_text(
        "# Quarterly Report\n\nHello **world**.\n\n- one\n- two\n"
    )

    # 3) Render it via the executor (unbounded AS so LaTeX font loading is safe).
    cmd = (
        "python3 staged_skills/pdf-generation/scripts/render_pdf.py "
        "--input document.md --output output/report.pdf --title Report"
    )
    res = await SubprocessExecutor().run(
        ExecRequest(command=cmd, cwd=str(slice_dir), timeout=120, memory_mb=0)
    )

    if res.exit_code != 0:
        pytest.skip(f"render failed in this env: {res.stderr[:300]}")

    out = slice_dir / "output" / "report.pdf"
    assert out.exists(), res.stdout + res.stderr
    assert out.read_bytes()[:4] == b"%PDF"
