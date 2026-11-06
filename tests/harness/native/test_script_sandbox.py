"""Native scripts use SDK workspaces, with host scope and mutation policy."""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai_harness import DockerSandbox

from jvagent.harness.native import build_native_agent
from jvagent.harness.recovery import NativeHistoryError, history_for_new_turn
from jvagent.harness.script_sandbox import SCRIPT_TOOL, NativeScriptSandbox
from jvagent.harness.step_store import InteractionStepStore
from jvagent.memory.interaction import Interaction

IMAGE = "python@sha256:" + "0" * 64


@pytest_asyncio.fixture(autouse=True)
async def clean_test_file_roots(monkeypatch: Any) -> Any:
    """Remove only roots successfully created by this test's helper."""
    import sys

    original = bound_profile
    roots: list[Path] = []

    async def tracked(*args: Any, **kwargs: Any) -> Any:
        profile, conversation = await original(*args, **kwargs)
        roots.append(profile._files.root)
        return profile, conversation

    monkeypatch.setattr(sys.modules[__name__], "bound_profile", tracked)
    yield
    for root in roots:
        shutil.rmtree(root)


async def bound_profile(image: str, root: Path | None = None) -> tuple[Any, Any]:
    from jvspatial.storage.interfaces.local import LocalFileInterface

    from tests.harness.native.test_planning import stores

    if root is None:
        configured = os.environ.get("JVAGENT_TEST_SCRIPT_ROOT")
        if not configured:
            pytest.skip("set JVAGENT_TEST_SCRIPT_ROOT to a bounded host file store")
        root = Path(configured)
    conversation, plan, spend = await stores()
    profile = NativeScriptSandbox(image=image)
    assets = profile.assets([Path("jvagent/skills/triage").resolve()])
    await profile.bind_policy(
        conversation,
        agent_id=plan.scope["agent_id"],
        user_id="caller",
        session_id=conversation.session_id,
        lock=spend.lock,
        assets=assets,
        file_interface=LocalFileInterface(root_dir=str(root)),
    )
    return profile, conversation


@pytest.mark.parametrize(
    "settings",
    [
        {"image": "python:latest"},
        {"image": ""},
        {"image": IMAGE, "timeout": 0},
        {"image": IMAGE, "timeout": float("inf")},
        {"image": IMAGE, "max_output_chars": True},
        {"image": IMAGE, "docker_args": ["--privileged"]},
    ],
)
def test_script_policy_rejects_unbounded_or_mutable_configuration(
    settings: Any,
) -> None:
    with pytest.raises(ValueError):
        NativeScriptSandbox(**settings)


@pytest.mark.parametrize("timeout", [True, -1, 61, float("nan"), "1"])
def test_model_cannot_expand_script_timeout(timeout: Any) -> None:
    with pytest.raises(ValueError):
        NativeScriptSandbox(image=IMAGE).check_arguments({"timeout_seconds": timeout})


def test_script_asset_snapshot_is_bounded_and_detached(tmp_path: Path) -> None:
    directory = tmp_path / "sample"
    scripts = directory / "scripts"
    scripts.mkdir(parents=True)
    asset = scripts / "run.py"
    asset.write_text("print(56)")
    profile = NativeScriptSandbox(image=IMAGE)
    snapshot = profile.assets([directory])
    snapshot.clear()
    asset.write_text("print(99)")
    assert profile.assets([directory]) == {
        "staged_skills/sample/scripts/run.py": b"print(56)"
    }
    asset.write_bytes(b"x" * 256_001)
    with pytest.raises(ValueError, match="bounded"):
        NativeScriptSandbox(image=IMAGE).assets([directory])


@pytest.mark.parametrize("directory_link", [False, True])
def test_script_assets_cannot_escape_through_symlinks(
    tmp_path: Path, directory_link: bool
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("private")
    directory = tmp_path / "sample"
    directory.mkdir()
    scripts = directory / "scripts"
    if directory_link:
        scripts.symlink_to(outside, target_is_directory=True)
    else:
        scripts.mkdir()
        (scripts / "secret.py").symlink_to(outside / "secret.py")
    with pytest.raises(ValueError, match="symlink|ordinary"):
        NativeScriptSandbox(image=IMAGE).assets([directory])


@pytest.mark.asyncio
async def test_script_workspace_cannot_adopt_changed_caller_or_policy(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from types import SimpleNamespace

    from jvspatial.storage.interfaces.local import LocalFileInterface

    from tests.harness.native.test_planning import stores

    monkeypatch.setattr(
        os, "statvfs", lambda path: SimpleNamespace(f_blocks=64, f_frsize=1024 * 1024)
    )

    conversation, plan, spend = await stores()
    profile = NativeScriptSandbox(image=IMAGE)
    scope = dict(
        agent_id=plan.scope["agent_id"],
        user_id="caller",
        session_id=conversation.session_id,
        lock=spend.lock,
        assets={},
        file_interface=LocalFileInterface(root_dir=str(tmp_path)),
    )
    await profile.bind_policy(conversation, **scope)
    original = dict(conversation.native_script_policy)
    await profile.bind_policy(conversation, **scope)
    for change in (
        {"user_id": "foreign"},
        {"agent_id": "foreign"},
        {"assets": {"scripts/changed.py": b"new code"}},
    ):
        with pytest.raises(ValueError):
            await profile.bind_policy(conversation, **{**scope, **change})
    with pytest.raises(ValueError, match="policy changed"):
        await NativeScriptSandbox(image=IMAGE, timeout=30).bind_policy(
            conversation, **scope
        )
    assert conversation.native_script_policy == original


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["idle", "denied", "read-only"])
async def test_script_workspace_is_lazy_and_denial_precedes_execution(
    mode: str,
) -> None:
    image = os.environ.get("JVAGENT_TEST_SCRIPT_IMAGE")
    if not image:
        pytest.skip("set JVAGENT_TEST_SCRIPT_IMAGE to a reviewed local image digest")
    profile, conversation = await bound_profile(image)
    interaction = await Interaction.create(
        user_id="caller",
        session_id=conversation.session_id,
        conversation_id=conversation.id,
        utterance=mode,
    )

    def respond(messages: Any, info: Any) -> ModelResponse:
        names = {tool.name for tool in info.function_tools}
        if mode == "read-only":
            assert SCRIPT_TOOL not in names
        if mode == "denied":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        SCRIPT_TOOL,
                        {"command": "printf unauthorized"},
                        tool_call_id="denied-script",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("hello")])

    async def denied(name: str) -> bool:
        return False

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=Path("jvagent/skills/triage").resolve(),
        tools=[],
        available_actions=["CodeExecutionAction"],
        access_check=denied,
        instructions="Use the skill",
        script_sandbox=profile,
        step_store=InteractionStepStore(interaction),
        allow_mutations=mode != "read-only",
    )
    if mode == "denied":
        with pytest.raises(PermissionError):
            await agent.run(
                "Run", run_id="lazy-script", conversation_id=conversation.session_id
            )
        assert not any(
            e["tool_name"] == SCRIPT_TOOL
            for e in interaction.native_steps.get("effects", [])
        )
    else:
        result = await agent.run(
            "Say hello", run_id="lazy-script", conversation_id=conversation.session_id
        )
        assert all(
            not message.workspace_ref
            for message in result.all_messages()
            if isinstance(message, ModelResponse)
        )


@pytest.mark.asyncio
async def test_official_docker_script_execution_history_and_failed_effect() -> None:
    image = os.environ.get("JVAGENT_TEST_SCRIPT_IMAGE")
    if not image:
        pytest.skip("set JVAGENT_TEST_SCRIPT_IMAGE to a reviewed local image digest")
    profile, conversation = await bound_profile(image)
    directory = Path("jvagent/skills/triage").resolve()
    calls = 0
    outputs: list[str] = []

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        SCRIPT_TOOL,
                        {
                            "command": 'printf \'SCRIPT-83\' > proof.txt; python staged_skills/triage/scripts/prioritize.py <<\'EOF\'\n[{"title":"low","severity":1},{"title":"high","severity":5}]\nEOF',
                        },
                        tool_call_id="first-write",
                    )
                ]
            )
        outputs.extend(
            str(p.content)
            for m in messages
            for p in m.parts
            if isinstance(p, ToolReturnPart)
        )
        return ModelResponse(parts=[TextPart("ranked")])

    async def allowed(name: str) -> bool:
        return True

    async def interaction() -> Interaction:
        return await Interaction.create(
            user_id="caller",
            session_id=conversation.session_id,
            conversation_id=conversation.id,
            utterance="rank",
        )

    first = await interaction()
    refs = set()
    try:
        agent = build_native_agent(
            FunctionModel(respond),
            skill_directory=directory,
            tools=[],
            available_actions=["CodeExecutionAction"],
            access_check=allowed,
            instructions="Use the reviewed script",
            script_sandbox=profile,
            step_store=InteractionStepStore(first),
        )
        result = await agent.run(
            "Rank", run_id="script-run", conversation_id=conversation.session_id
        )
        refs.update(
            m.workspace_ref
            for m in result.all_messages()
            if isinstance(m, ModelResponse) and m.workspace_ref
        )
        assert (
            len(refs) == 1
            and '"high"' in outputs[-1]
            and outputs[-1].index('"high"') < outputs[-1].index('"low"')
        )
        effect = await InteractionStepStore(first).get_tool_effect(
            run_id="script-run", tool_call_id="first-write"
        )
        assert (
            effect.status == "completed"
            and effect.effect_summary == "jv:mutation:script:v1"
        )
        object.__setattr__(
            first, "native_run", {"run_id": "script-run", "status": "failed"}
        )
        with pytest.raises(
            NativeHistoryError, match="mutation requires reconciliation"
        ):
            await history_for_new_turn(
                first,
                conversation_id=conversation.id,
                user_id="caller",
                session_id=conversation.session_id,
            )

        def read(messages: Any, info: Any) -> ModelResponse:
            returns = [
                p
                for m in messages
                for p in m.parts
                if isinstance(p, ToolReturnPart) and p.tool_call_id == "read-proof"
            ]
            if not returns:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            SCRIPT_TOOL,
                            {"command": "cat proof.txt"},
                            tool_call_id="read-proof",
                        )
                    ]
                )
            assert "SCRIPT-83" in str(returns[-1].content)
            return ModelResponse(parts=[TextPart("preserved")])

        second = await interaction()
        # A non-root SDK command must also work after tmpfs mounts are rebuilt.
        subprocess.run(
            ["docker", "stop", "--timeout", "3", next(iter(refs)).id],
            check=True,
            capture_output=True,
        )
        next_agent = build_native_agent(
            FunctionModel(read),
            skill_directory=directory,
            tools=[],
            available_actions=["CodeExecutionAction"],
            access_check=allowed,
            instructions="Read the file",
            script_sandbox=profile,
            step_store=InteractionStepStore(second),
        )
        continued = await next_agent.run(
            "Read",
            run_id="script-next",
            conversation_id=conversation.session_id,
            message_history=result.all_messages(),
        )
        assert continued.output == "preserved"
        next_refs = {
            m.workspace_ref
            for m in continued.all_messages()
            if isinstance(m, ModelResponse) and m.workspace_ref
        }
        assert next_refs == refs
    finally:
        for ref in refs:
            await DockerSandbox().destroy(ref)


@pytest.mark.asyncio
async def test_parallel_sdk_commands_share_one_complete_script_stage(
    monkeypatch: Any,
) -> None:
    from pydantic_ai.workspaces import Workspace

    image = os.environ.get("JVAGENT_TEST_SCRIPT_IMAGE")
    if not image:
        pytest.skip("set JVAGENT_TEST_SCRIPT_IMAGE to a reviewed local image digest")
    profile, conversation = await bound_profile(image)
    path = "staged_skills/triage/scripts/prioritize.py"
    writes: list[str] = []
    original = Workspace.write_bytes

    async def record(self: Any, filename: str, data: bytes) -> None:
        if filename == path:
            writes.append(filename)
        await original(self, filename, data)

    monkeypatch.setattr(Workspace, "write_bytes", record)
    calls = 0

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        SCRIPT_TOOL,
                        {
                            "command": f'python {path} <<\'EOF\'\n[{{"title":"one","severity":1}}]\nEOF'
                        },
                        tool_call_id=f"parallel-{i}",
                    )
                    for i in range(3)
                ]
            )
        returns = [
            p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)
        ]
        assert len(returns) == 3 and all('"one"' in str(p.content) for p in returns)
        return ModelResponse(parts=[TextPart("done")])

    async def allowed(name: str) -> bool:
        return True

    interaction = await Interaction.create(
        user_id="caller",
        session_id=conversation.session_id,
        conversation_id=conversation.id,
        utterance="rank",
    )
    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=Path("jvagent/skills/triage").resolve(),
        tools=[],
        available_actions=["CodeExecutionAction"],
        access_check=allowed,
        instructions="Use the skill",
        script_sandbox=profile,
        step_store=InteractionStepStore(interaction),
    )
    result = await agent.run(
        "Rank", run_id="parallel-script", conversation_id=conversation.session_id
    )
    try:
        assert writes == [path]
        assert (
            len(
                [
                    e
                    for e in interaction.native_steps["effects"]
                    if e["effect_summary"] == "jv:mutation:script:v1"
                    and e["status"] == "completed"
                ]
            )
            == 3
        )
    finally:
        refs = {
            m.workspace_ref
            for m in result.all_messages()
            if isinstance(m, ModelResponse) and m.workspace_ref
        }
        for ref in refs:
            await DockerSandbox().destroy(ref)
