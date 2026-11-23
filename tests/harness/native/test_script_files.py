"""The SDK workspace must prove its existing caller file-service binding."""

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from jvspatial.storage.interfaces.local import LocalFileInterface
from pydantic_ai.workspaces import LocalWorkspaceBackend, Workspace

from jvagent.harness.script_files import NativeScriptFiles
from jvagent.harness.script_sandbox import NativeScriptSandbox

IMAGE = "python@sha256:" + "0" * 64


@pytest.fixture
def bounded(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        os, "statvfs", lambda path: SimpleNamespace(f_blocks=64, f_frsize=1024 * 1024)
    )


@pytest.mark.asyncio
async def test_unbounded_filesystem_is_refused_before_marker_write(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        os, "statvfs", lambda path: SimpleNamespace(f_blocks=512, f_frsize=1024 * 1024)
    )
    with pytest.raises(ValueError, match="host-bounded"):
        await NativeScriptFiles.admit(
            LocalFileInterface(root_dir=str(tmp_path)),
            agent_id="agent",
            user_id="caller",
            image=IMAGE,
        )
    assert not list(tmp_path.rglob("_jv_workspace_binding.txt"))


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["caller/other", "caller:other", ".caller"])
async def test_storage_segment_aliases_are_not_mount_authority(
    tmp_path: Path, bounded: None, identity: str
) -> None:
    with pytest.raises(ValueError, match="unambiguous"):
        await NativeScriptFiles.admit(
            LocalFileInterface(root_dir=str(tmp_path)),
            agent_id="agent",
            user_id=identity,
            image=IMAGE,
        )


@pytest.mark.asyncio
async def test_user_root_symlink_is_refused(tmp_path: Path, bounded: None) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "files"
    (root / "agent").mkdir(parents=True)
    (root / "agent/caller").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        await NativeScriptFiles.admit(
            LocalFileInterface(root_dir=str(root)),
            agent_id="agent",
            user_id="caller",
            image=IMAGE,
        )
    assert not list(outside.iterdir())


@pytest.mark.asyncio
async def test_other_user_workspace_cannot_pass_mount_binding(
    tmp_path: Path, bounded: None
) -> None:
    fi = LocalFileInterface(root_dir=str(tmp_path))
    first = await NativeScriptFiles.admit(
        fi, agent_id="agent", user_id="first", image=IMAGE
    )
    second = await NativeScriptFiles.admit(
        fi, agent_id="agent", user_id="second", image=IMAGE
    )
    with pytest.raises(ValueError, match="admitted user's files"):
        await first.stage(
            Workspace(LocalWorkspaceBackend(second.root)),
            {"staged_skills/example/scripts/run.py": b"print(1)"},
        )
    assert not (second.root / "staged_skills").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["content", "symlink"])
async def test_staged_scripts_are_not_silently_replaced(
    tmp_path: Path, bounded: None, change: str
) -> None:
    files = await NativeScriptFiles.admit(
        LocalFileInterface(root_dir=str(tmp_path)),
        agent_id="agent",
        user_id="caller",
        image=IMAGE,
    )
    workspace = Workspace(LocalWorkspaceBackend(files.root))
    name = "staged_skills/example/scripts/run.py"
    expected = b"print(1)"
    await files.stage(workspace, {name: expected})
    path = files.root / name
    if change == "content":
        path.write_bytes(b"print(9)")
    else:
        path.unlink()
        other = tmp_path / "other.py"
        other.write_bytes(expected)
        path.symlink_to(other)
    with pytest.raises(ValueError, match="migration|symbolic"):
        await files.stage(workspace, {name: expected})


@pytest.mark.asyncio
async def test_old_tmpfs_policy_is_not_rebound_or_rewritten(tmp_path: Path) -> None:
    from tests.harness.native.test_planning import stores

    conversation, plan, spend = await stores()
    original = {"version": "sdk-docker-script-v1"}
    object.__setattr__(conversation, "native_script_policy", original)
    await conversation.save()
    with pytest.raises(ValueError, match="migration"):
        await NativeScriptSandbox(image=IMAGE).bind_policy(
            conversation,
            agent_id=plan.scope["agent_id"],
            user_id="caller",
            session_id=conversation.session_id,
            lock=spend.lock,
            assets={},
            file_interface=LocalFileInterface(root_dir=str(tmp_path)),
        )
    assert conversation.native_script_policy == original
    assert not (tmp_path / plan.scope["agent_id"]).exists()
