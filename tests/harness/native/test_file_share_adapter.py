"""Artifact issuance uses SDK effects and existing scoped storage/proxy services."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

pytest.importorskip("pydantic_ai_harness")
from fastapi import HTTPException
from jvspatial.storage.interfaces.local import LocalFileInterface
from jvspatial.storage.managers import URLProxyManager
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from jvagent.action.file_interface import FileInterfaceAction, sharing
from jvagent.core.app import App
from jvagent.harness.native import build_native_agent
from jvagent.harness.recovery import NativeHistoryError, history_for_new_turn
from jvagent.harness.step_store import InteractionStepStore
from jvagent.memory.conversation import Conversation
from jvagent.memory.interaction import Interaction
from jvagent.tooling.context import bind_dispatch_context


@pytest_asyncio.fixture
async def setup(tmp_path: Path, monkeypatch: Any) -> Any:
    fi = LocalFileInterface(root_dir=str(tmp_path / "files"))
    await fi.save_file("agent/caller/report.txt", b"ORIGINAL-ARTIFACT")
    action = await FileInterfaceAction.create(agent_id="agent")
    manager = URLProxyManager()
    app = SimpleNamespace(
        get_file_interface=AsyncMock(return_value=fi),
        get_proxy_manager=AsyncMock(return_value=manager),
    )
    monkeypatch.setattr(App, "get", AsyncMock(return_value=app))
    monkeypatch.setattr(
        sharing._core, "_get_file_interface", AsyncMock(return_value=fi)
    )
    monkeypatch.setattr(
        sharing,
        "get_current_server",
        lambda: SimpleNamespace(
            config=SimpleNamespace(
                file_storage=SimpleNamespace(file_storage_enabled=False)
            )
        ),
    )
    monkeypatch.setattr(sharing, "get_public_base_url", lambda: "https://files.example")
    visitor = SimpleNamespace(
        _agent=SimpleNamespace(id="agent"),
        user_id="caller",
        session_id="session",
        channel="default",
        interaction=SimpleNamespace(
            id="fixture-turn", user_id="caller", session_id="session"
        ),
    )
    return SimpleNamespace(
        fi=fi, action=action, manager=manager, visitor=visitor, root=tmp_path
    )


async def issue(
    s: Any, *, path: str = "report.txt", expires_in: int = 600, limit: int = 1024
) -> dict[str, Any]:
    with bind_dispatch_context(s.visitor):
        return await sharing.share_file(s.action, s.visitor, path, expires_in, limit)


@pytest.mark.asyncio
async def test_snapshot_does_not_follow_later_script_edits(setup: Any) -> None:
    s = setup
    result = await issue(s)
    assert datetime.fromisoformat(result["expires_at"]).tzinfo is not None
    code = result["url"].rsplit("/", 1)[1]
    await s.fi.save_file("agent/caller/report.txt", b"LATER-EDIT")
    response = await sharing.download_file(s.action, code)
    assert response.body == b"ORIGINAL-ARTIFACT"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["cache-control"] == "no-store"
    assert result["bytes"] == len(response.body)
    proxy = await s.manager.get_proxy(code)
    assert proxy.file_path.startswith(f"native_exports/{s.action.id}/")
    assert not proxy.one_time


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["expired", "disabled", "wrong-action", "tampered"])
async def test_download_refuses_invalid_capability_or_snapshot(
    setup: Any, failure: str
) -> None:
    s = setup
    result = await issue(s)
    code = result["url"].rsplit("/", 1)[1]
    proxy = await s.manager.get_proxy(code)
    target = s.action
    if failure == "expired":
        proxy.expires_at = datetime.now() - timedelta(seconds=1)
        await proxy.save()
    elif failure == "disabled":
        s.action.enabled = False
    elif failure == "wrong-action":
        target = await FileInterfaceAction.create(agent_id="other")
    else:
        await s.fi.save_file(proxy.file_path, b"CHANGED-EXPORT")
    with pytest.raises(HTTPException) as caught:
        await sharing.download_file(target, code)
    assert caught.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "symlink",
        "oversized",
        "missing-manager",
        "no-public-url",
        "generic-http",
        "no-server",
    ],
)
async def test_issuance_refuses_unsafe_source_or_configuration(
    setup: Any, monkeypatch: Any, failure: str
) -> None:
    s = setup
    path, limit = "report.txt", 1024
    if failure == "symlink":
        outside = s.root / "outside.txt"
        outside.write_text("FOREIGN")
        (s.fi.root_dir / "agent/caller/link.txt").symlink_to(outside)
        path = "link.txt"
    elif failure == "oversized":
        limit = 3
    elif failure == "missing-manager":
        (await App.get()).get_proxy_manager.return_value = None
    elif failure == "no-public-url":
        monkeypatch.setattr(sharing, "get_public_base_url", lambda: "")
    elif failure == "no-server":
        monkeypatch.setattr(sharing, "get_current_server", lambda: None)
    else:
        monkeypatch.setattr(
            sharing,
            "get_current_server",
            lambda: SimpleNamespace(
                config=SimpleNamespace(
                    file_storage=SimpleNamespace(file_storage_enabled=True)
                )
            ),
        )
    with pytest.raises(ValueError):
        await issue(s, path=path, limit=limit)
    assert not (s.fi.root_dir / "native_exports").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("expires_in", [True, 0, -1, 3601])
async def test_model_cannot_expand_expiry(setup: Any, expires_in: Any) -> None:
    with pytest.raises(ValueError):
        await issue(setup, expires_in=expires_in)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "allowed,failure",
    [(True, ""), (False, ""), (True, "after-share"), (True, "proxy-flush")],
)
async def test_sdk_permission_and_mutation_intent_precede_issuance(
    setup: Any, monkeypatch: Any, allowed: bool, failure: str
) -> None:
    s = setup
    conversation = await Conversation.create(user_id="caller", session_id="session")
    interaction = await Interaction.create(
        user_id="caller",
        session_id="session",
        utterance="Share file",
        conversation_id=conversation.id,
    )
    s.visitor.interaction = interaction
    seen: list[str] = []
    original = sharing.share_file

    async def observed(*args: Any, **kwargs: Any) -> Any:
        effect = interaction.native_steps["effects"][-1]
        assert (
            effect["status"] == "started"
            and effect["effect_summary"] == "jv:mutation:file-share:v1"
        )
        seen.append("issue")
        result = await original(*args, **kwargs)
        if failure == "after-share":
            raise RuntimeError("Failure after proxy creation")
        return result

    monkeypatch.setattr(sharing, "share_file", observed)
    if failure == "proxy-flush":
        monkeypatch.setattr(
            sharing,
            "flush_deferred_entities",
            AsyncMock(side_effect=RuntimeError("Flush failed")),
        )
    skill = s.root / "share-file"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: share-file\ndescription: Share requested artifact\nallowed-tools: [file_interface__share_file]\n---\nShare only the requested file.\n"
    )
    calls = 0

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        return (
            ModelResponse(
                parts=[
                    ToolCallPart(
                        "file_interface__share_file",
                        {"path": "report.txt"},
                        tool_call_id="share-call",
                    )
                ]
            )
            if calls == 1
            else ModelResponse(parts=[TextPart("shared")])
        )

    async def access(name: str) -> bool:
        return True

    async def tool_access(tool: Any) -> bool:
        assert tool.requires_tool_permission
        return allowed

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=skill,
        tools=await s.action.get_native_tools(),
        access_check=access,
        tool_access_check=tool_access,
        instructions="Use the skill",
        step_store=InteractionStepStore(interaction),
    )
    with bind_dispatch_context(s.visitor):
        if failure:
            with pytest.raises(RuntimeError):
                await agent.run(
                    "Share report", run_id="share-run", conversation_id="session"
                )
        elif allowed:
            assert (
                await agent.run(
                    "Share report", run_id="share-run", conversation_id="session"
                )
            ).output == "shared"
        else:
            with pytest.raises(PermissionError):
                await agent.run(
                    "Share report", run_id="share-run", conversation_id="session"
                )
    assert seen == (["issue"] if allowed else [])
    assert (s.fi.root_dir / "native_exports").exists() is allowed
    if failure:
        object.__setattr__(
            interaction, "native_run", {"run_id": "share-run", "status": "failed"}
        )
        with pytest.raises(
            NativeHistoryError, match="mutation requires reconciliation"
        ):
            await history_for_new_turn(
                interaction,
                conversation_id=conversation.id,
                user_id="caller",
                session_id="session",
            )
        assert interaction.native_steps["effects"][-1]["status"] == "failed"
