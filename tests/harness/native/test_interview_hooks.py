"""Reviewed extension ABI, source freshness and public SDK domain execution."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml

pytest.importorskip("pydantic_ai_harness")

from jvagent.action.interview import InterviewAction
from jvagent.action.interview.hooks import clear_module_cache, load_hook_function
from jvagent.action.interview.native import qualify_spec
from jvagent.action.interview.native_hooks import (
    approved_source,
    bind_native_hooks,
    native_hook_digest,
)
from jvagent.action.interview.session import load_session
from jvagent.action.interview.spec import load_interview_spec_from_skill
from tests.harness.native.test_interview import host, turn

APP = (
    Path(__file__).resolve().parents[3]
    / "examples/jvagent_app/agents/jvagent/orchestrator_agent"
)
SIGNUP = APP / "skills/signup-interview"


def package(
    tmp_path: Path,
    *,
    code: str = "async def check(ctx):\n    return ctx.valid(value=ctx.value)\n",
) -> tuple[Path, Any]:
    directory = tmp_path / "profile"
    (directory / "scripts").mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "---\n"
        + yaml.safe_dump(
            {
                "name": "profile",
                "description": "Profile",
                "trust_tier": "trusted",
                "extends": "action:jvagent/interview",
                "interview": {
                    "fields": [{"key": "name", "prompt": "Name?", "validator": "check"}]
                },
            }
        )
        + "---\nUse the interview tools.\n"
    )
    (directory / "scripts/custom_tools.py").write_text(code)
    return directory, load_interview_spec_from_skill(directory)


def test_self_declared_trust_does_not_admit_native_hooks(tmp_path: Path) -> None:
    directory, spec = package(
        tmp_path,
        code="raise RuntimeError('must not import')\nasync def check(ctx): pass\n",
    )
    with pytest.raises(ValueError, match="host-reviewed"):
        qualify_spec(spec)
    assert native_hook_digest(directory).startswith("sha256:")


@pytest.mark.parametrize("target", ["SKILL.md", "scripts/custom_tools.py"])
def test_source_change_rejects_even_after_module_cache(
    tmp_path: Path, target: str
) -> None:
    directory, spec = package(tmp_path)
    digest = native_hook_digest(directory)
    qualify_spec(spec, hook_digest=digest)
    with bind_native_hooks(spec, digest):
        assert callable(load_hook_function(spec, "check"))
        path = directory / target
        path.write_text(path.read_text() + "\n# Changed after approval\n")
        with pytest.raises(ValueError, match="changed after review"):
            load_hook_function(spec, "check")


@pytest.mark.parametrize(
    "code",
    [
        "async def missing(ctx): pass\n",
        "async def check(visitor): pass\n",
        "async def check(ctx, **kwargs): pass\n",
        "invalid Python source!",
    ],
)
def test_bad_hook_contract_rejects_before_execution(tmp_path: Path, code: str) -> None:
    directory, spec = package(tmp_path, code=code)
    with pytest.raises(ValueError):
        qualify_spec(spec, hook_digest=native_hook_digest(directory))


def test_symlinked_source_rejects(tmp_path: Path) -> None:
    directory, _ = package(tmp_path)
    path = directory / "scripts/custom_tools.py"
    elsewhere = tmp_path / "outside.py"
    path.rename(elsewhere)
    path.symlink_to(elsewhere)
    with pytest.raises(ValueError, match="non-symlink"):
        native_hook_digest(directory)


def test_import_error_is_fatal_and_redacted(tmp_path: Path) -> None:
    directory, spec = package(
        tmp_path,
        code="raise RuntimeError('private import detail')\nasync def check(ctx): pass\n",
    )
    digest = native_hook_digest(directory)
    with (
        bind_native_hooks(spec, digest),
        pytest.raises(RuntimeError, match="module failed to load") as caught,
    ):
        load_hook_function(spec, "check")
    assert "private import detail" not in str(caught.value)


def test_changed_declaration_requires_reload_even_with_new_approval(
    tmp_path: Path,
) -> None:
    directory, spec = package(tmp_path)
    document = directory / "SKILL.md"
    document.write_text(document.read_text().replace("Name?", "Updated name?"))
    with pytest.raises(ValueError, match="reload the Action"):
        approved_source(spec, native_hook_digest(directory))


def test_native_loader_failure_cannot_silently_skip_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, spec = package(tmp_path)
    digest = native_hook_digest(directory)
    clear_module_cache()
    monkeypatch.setattr("importlib.util.spec_from_file_location", lambda *a: None)
    with (
        bind_native_hooks(spec, digest),
        pytest.raises(RuntimeError, match="module failed to load"),
    ):
        load_hook_function(spec, "check")


@pytest.mark.asyncio
async def test_completion_hook_failure_does_not_close_session_or_task(
    tmp_path: Path,
) -> None:
    directory, _ = package(
        tmp_path,
        code=(
            "async def check(ctx): return ctx.valid(value=ctx.value)\n"
            "async def finish(ctx): return ctx.tool_response(ok=False, status='error', error='Not ready')\n"
        ),
    )
    document = directory / "SKILL.md"
    _, frontmatter, body = document.read_text().split("---", 2)
    data = yaml.safe_load(frontmatter)
    data["interview"]["handlers"] = {"complete": "finish"}
    document.write_text("---\n" + yaml.safe_dump(data) + "---\n" + body)
    action = InterviewAction(
        native_hook_digests={"profile": native_hook_digest(directory)}
    )
    action._registry.discover([str(tmp_path)])
    visitor = await host()
    tools = await action.get_native_tools()
    bases = await action.get_native_skill_bases()
    await turn(
        directory,
        visitor,
        tools,
        [
            ("interview__start", {"interview_type": "profile"}),
            ("interview__set_fields", {"fields": {"name": "Casey River"}}),
            ("interview__review", {}),
        ],
        bases,
    )
    await turn(directory, visitor, tools, [("interview__complete", {})], bases)
    assert load_session(visitor.conversation) is not None
    assert visitor.tasks.list()[0].status == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["start", "update"])
async def test_other_discovered_interview_cannot_bypass_selected_skill(
    tmp_path: Path, operation: str
) -> None:
    directory, _ = package(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    (other / "SKILL.md").write_text(
        "---\n"
        + yaml.safe_dump(
            {
                "name": "other",
                "description": "Other private workflow",
                "extends": "action:jvagent/interview",
                "interview": {"fields": [{"key": "secret", "prompt": "Secret?"}]},
            }
        )
        + "---\nUse the interview tools.\n"
    )
    action = InterviewAction(
        native_hook_digests={"profile": native_hook_digest(directory)}
    )
    action._registry.discover([str(tmp_path)])
    visitor = await host()
    tools = await action.get_native_tools()
    bases = await action.get_native_skill_bases()
    if operation == "update":
        await turn(
            other,
            visitor,
            tools,
            [("interview__start", {"interview_type": "other"})],
            bases,
        )
    commands = (
        [("interview__start", {"interview_type": "other"})]
        if operation == "start"
        else [("interview__set_fields", {"fields": {"secret": "unselected write"}})]
    )
    _, store = await turn(directory, visitor, tools, commands, bases)
    session = load_session(visitor.conversation)
    assert session is None if operation == "start" else session.fields == {}
    assert visitor.interaction.native_steps.get("effects", []) == []
    assert not await store.list_unresolved_tool_effects(run_id=visitor.interaction.id)


@pytest.mark.asyncio
async def test_selected_unapproved_hooks_refuse_even_when_other_tools_exist(
    tmp_path: Path,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from pydantic_ai.models.test import TestModel

    from jvagent.harness.native import build_native_agent
    from jvagent.harness.step_store import InteractionStepStore

    directory, _ = package(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    (other / "SKILL.md").write_text(
        "---\n"
        + yaml.safe_dump(
            {
                "name": "other",
                "description": "Other",
                "extends": "action:jvagent/interview",
                "interview": {"fields": [{"key": "name", "prompt": "Name?"}]},
            }
        )
        + "---\nHelp.\n"
    )
    action = InterviewAction()
    action._registry.discover([str(tmp_path)])
    tools = await action.get_native_tools()
    assert any(tool.name == "interview__start" for tool in tools)
    with pytest.raises(ValueError, match="no admitted domain"):
        build_native_agent(
            TestModel(),
            skill_directory=directory,
            tools=tools,
            step_store=InteractionStepStore(
                SimpleNamespace(native_steps={}, save=AsyncMock())
            ),
            action_skill_bases=await action.get_native_skill_bases(),
            available_actions=["InterviewAction"],
            access_check=lambda name: True,
            instructions="Help.",
        )


@pytest.mark.asyncio
async def test_source_drift_after_admission_never_executes_hook(tmp_path: Path) -> None:
    directory, _ = package(tmp_path)
    action = InterviewAction(
        native_hook_digests={"profile": native_hook_digest(directory)}
    )
    action._registry.discover([str(tmp_path)])
    tools = await action.get_native_tools()
    path = directory / "scripts/custom_tools.py"
    path.write_text(
        "raise AssertionError('must not import changed code')\n" + path.read_text()
    )
    visitor = await host()
    with pytest.raises(ValueError, match="changed after review"):
        await turn(
            directory,
            visitor,
            tools,
            [("interview__start", {"interview_type": "profile"})],
            await action.get_native_skill_bases(),
        )
    assert load_session(visitor.conversation) is None
    assert visitor.tasks.list() == []


@pytest.mark.asyncio
async def test_approval_scope_is_task_local_and_resets(tmp_path: Path) -> None:
    directory, spec = package(tmp_path)
    digest = native_hook_digest(directory)

    async def approved() -> None:
        with bind_native_hooks(spec, digest):
            await asyncio.sleep(0)
            assert callable(load_hook_function(spec, "check"))

    async def denied() -> None:
        with bind_native_hooks(spec, None):
            await asyncio.sleep(0)
            with pytest.raises(ValueError, match="host-reviewed"):
                load_hook_function(spec, "check")

    await asyncio.gather(approved(), denied())
    with bind_native_hooks(spec, None), pytest.raises(ValueError):
        load_hook_function(spec, "check")
    # Legacy authoring/trust remains an independent API outside native scope.
    assert callable(load_hook_function(spec, "check"))


@pytest.mark.asyncio
async def test_existing_signup_hooks_execute_inside_sdk_mutations() -> None:
    clear_module_cache()
    action = InterviewAction(
        native_hook_digests={"signup-interview": native_hook_digest(SIGNUP)}
    )
    action._registry.discover([str(APP / "skills")])
    tools = await action.get_native_tools()
    bases = await action.get_native_skill_bases()
    visitor = await host()
    await turn(
        SIGNUP,
        visitor,
        tools,
        [
            ("interview__start", {"interview_type": "signup-interview"}),
            ("interview__set_fields", {"fields": {"user_name": "ok"}}),
        ],
        bases,
    )
    session = load_session(visitor.conversation)
    assert session.fields == {}
    await turn(
        SIGNUP,
        visitor,
        tools,
        [
            (
                "interview__set_fields",
                {
                    "fields": {
                        "user_name": "Casey River",
                        "available_times": "Saturday at 9",
                        "training_format": "virtual",
                        "user_email": "casey@mail.com",
                        "employer_name": "Orbit Fixtures",
                    }
                },
            ),
            ("interview__skip_field", {"field_key": "phone_number"}),
            ("interview__review", {}),
        ],
        bases,
    )
    session = load_session(visitor.conversation)
    assert session.get_value("available_times") == "Saturday 9:00 AM - 12:00 PM"
    assert session.get_value("training_format") == "Virtual"
    assert session.get_value("employer_name") == "Orbit Fixtures"
    assert "special updates" in str(visitor.interaction.native_steps)
    await turn(SIGNUP, visitor, tools, [("interview__complete", {})], bases)
    assert load_session(visitor.conversation) is None
    assert visitor.tasks.list()[0].status == "completed"
    assert all(
        effect["status"] == "completed"
        for effect in visitor.interaction.native_steps["effects"]
    )


@pytest.mark.asyncio
async def test_sdk_start_enum_hides_unconfigured_interviews(tmp_path: Path) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    from jvagent.harness.native import build_native_agent
    from jvagent.harness.step_store import InteractionStepStore

    directory, _ = package(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    (other / "SKILL.md").write_text(
        "---\n"
        + yaml.safe_dump(
            {
                "name": "other",
                "description": "Other",
                "extends": "action:jvagent/interview",
                "interview": {"fields": [{"key": "name", "prompt": "Name?"}]},
            }
        )
        + "---\nHelp.\n"
    )
    action = InterviewAction(
        native_hook_digests={"profile": native_hook_digest(directory)}
    )
    action._registry.discover([str(tmp_path)])

    def respond(messages: Any, info: Any) -> ModelResponse:
        tool = next(t for t in info.function_tools if t.name == "interview__start")
        assert tool.parameters_json_schema["properties"]["interview_type"]["enum"] == [
            "profile"
        ]
        return ModelResponse(parts=[TextPart("done")])

    async def access(name: str) -> bool:
        return True

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=directory,
        tools=await action.get_native_tools(),
        step_store=InteractionStepStore(
            SimpleNamespace(native_steps={}, save=AsyncMock())
        ),
        action_skill_bases=await action.get_native_skill_bases(),
        available_actions=["InterviewAction"],
        access_check=access,
        instructions="Help.",
    )
    assert (await agent.run("Help")).output == "done"
