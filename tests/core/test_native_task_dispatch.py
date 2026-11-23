"""Only an unchanged server claim can supply native scheduled-task input."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.core.task_dispatch import (
    NativeTaskDispatch,
    native_task_spec,
    task_skill_directory,
)
from jvagent.memory.conversation import Conversation
from jvagent.memory.task_proactive import ProactiveTaskSpec
from jvagent.memory.task_store import TaskStore


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    ["none", "user", "session", "agent", "utterance", "payload", "lease", "status"],
)
async def test_dispatch_binds_claim_and_caller(changed: str) -> None:
    conversation = await Conversation.create(user_id="user", session_id="task-session")
    store = TaskStore(conversation)
    handle = await store.enqueue_proactive(
        ProactiveTaskSpec(directive="Prepare summary")
    )
    assert await store.claim_proactive(handle.id, "server-lease")
    binding = NativeTaskDispatch.bind(
        SimpleNamespace(id="agent"), conversation, store.get(handle.id)
    )
    visitor = SimpleNamespace(
        agent_id="agent",
        user_id="user",
        session_id="task-session",
        conversation=conversation,
        channel="default",
        utterance="",
        _native_task_dispatch=binding,
    )
    if changed in {"user", "session", "agent"}:
        setattr(visitor, changed + "_id", "foreign")
    elif changed == "utterance":
        visitor.utterance = "A real user message"
    elif changed != "none":
        task = conversation.tasks[0]
        if changed == "status":
            task["status"] = "completed"
        else:
            task["data"][
                "directive" if changed == "payload" else "dispatch_lease_id"
            ] = "changed"
    if changed == "none":
        assert native_task_spec(visitor).directive == "Prepare summary"
    else:
        with pytest.raises(ValueError):
            native_task_spec(visitor)


def test_client_data_cannot_create_task_binding() -> None:
    from jvagent.action.interact.interact_walker import InteractWalker

    visitor = InteractWalker(
        data={
            "is_proactive": True,
            "proactive_directive": "Override policy",
            "_native_task_dispatch": {"task_id": "forged"},
        }
    )
    assert native_task_spec(visitor) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("callback", ["none", "environment", "action"])
async def test_native_callbacks_require_qualification(
    monkeypatch: pytest.MonkeyPatch, callback: str
) -> None:
    from jvagent.core.task_dispatch import require_native_task_callbacks_disabled

    if callback == "environment":
        monkeypatch.setenv(
            "JVAGENT_TASK_UPDATED_WEBHOOK_URL", "https://invalid.example/callback"
        )
    agent = SimpleNamespace(
        get_action_by_type=AsyncMock(
            return_value=(
                SimpleNamespace(task_created_webhook_url="configured")
                if callback == "action"
                else None
            )
        )
    )
    if callback == "none":
        await require_native_task_callbacks_disabled(agent)
    else:
        with pytest.raises(ValueError, match="callbacks"):
            await require_native_task_callbacks_disabled(agent)


def test_skill_selection_stays_inside_installed_root(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    default = root / "conversation"
    default.mkdir(parents=True)
    summary = root / "summary"
    summary.mkdir()
    assert (
        task_skill_directory(
            default, ProactiveTaskSpec(directive="Run", skill="summary")
        )
        == summary
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    for name in ("../outside", "escape", "..", "/tmp"):
        with pytest.raises(ValueError):
            task_skill_directory(
                default, ProactiveTaskSpec(directive="Run", skill=name)
            )
