"""CodeExecutionAction: opt-in gating, guards, and a sandbox-confined run.

The per-user sandbox resolution (App + file interface) is patched so the test
exercises the tool surface + executor end-to-end without a live App."""

from __future__ import annotations

from jvagent.action.code_execution.code_execution_action import CodeExecutionAction
from jvagent.tooling.tool_executor import bind_dispatch_context


class _Visitor:
    def __init__(self):
        self._agent = type("A", (), {"id": "n.Agent.1"})()
        self.user_id = "u.User.1"
        self.session_id = None
        self.interaction = None
        self.channel = "web"


async def test_disabled_surfaces_no_tool():
    action = CodeExecutionAction()
    action.enabled = False
    assert await action.get_tools() == []


async def test_enabled_surfaces_bash_tool():
    action = CodeExecutionAction()
    action.enabled = True
    tools = await action.get_tools()
    assert [t.name for t in tools] == ["code_execution__bash"]
    assert tools[0].parameters_schema["required"] == ["command"]


async def test_run_bash_disabled_message():
    action = CodeExecutionAction()
    action.enabled = False
    assert "disabled" in await action.run_bash("echo hi")


async def test_run_bash_no_context():
    action = CodeExecutionAction()
    action.enabled = True
    # No dispatch visitor bound.
    out = await action.run_bash("echo hi")
    assert "no execution context" in out


async def test_run_bash_empty_command():
    action = CodeExecutionAction()
    action.enabled = True
    with bind_dispatch_context(_Visitor()):
        assert "no command" in await action.run_bash("   ")


async def test_run_bash_executes_in_sandbox(tmp_path, monkeypatch):
    action = CodeExecutionAction()
    action.enabled = True

    async def _fake_cwd(self, visitor):
        return str(tmp_path)

    monkeypatch.setattr(CodeExecutionAction, "resolve_user_cwd", _fake_cwd)
    with bind_dispatch_context(_Visitor()):
        out = await action.run_bash("echo sandboxed && pwd")
    assert "sandboxed" in out
    assert str(tmp_path) in out
    assert "[exit=0]" in out


async def test_run_bash_surfaces_sandbox_error(monkeypatch):
    action = CodeExecutionAction()
    action.enabled = True

    async def _boom(self, visitor):
        raise RuntimeError("requires local file storage")

    monkeypatch.setattr(CodeExecutionAction, "resolve_user_cwd", _boom)
    with bind_dispatch_context(_Visitor()):
        out = await action.run_bash("echo hi")
    assert "sandbox error" in out and "local file storage" in out


async def test_stage_skill_copies_into_slice(tmp_path, monkeypatch):
    # Build a fake skill dir.
    skill = tmp_path / "src_skill"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo\n---\nbody\n")
    (skill / "scripts" / "x.py").write_text("print('hi')\n")

    slice_dir = tmp_path / "slice"
    slice_dir.mkdir()

    action = CodeExecutionAction()
    action.enabled = True

    async def _fake_cwd(self, visitor):
        return str(slice_dir)

    monkeypatch.setattr(CodeExecutionAction, "resolve_user_cwd", _fake_cwd)
    rel = await action.stage_skill(_Visitor(), str(skill), "demo")
    assert rel == "staged_skills/demo"
    assert (slice_dir / "staged_skills" / "demo" / "scripts" / "x.py").exists()
