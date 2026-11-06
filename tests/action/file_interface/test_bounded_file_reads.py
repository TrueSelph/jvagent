"""Native file reads consume bounded storage and preserve caller isolation."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from jvspatial.storage.interfaces.local import LocalFileInterface

from jvagent.action.file_interface import _core
from jvagent.core.sandbox import resolve_user_sandbox_relpath


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: Any) -> LocalFileInterface:
    provider = LocalFileInterface(root_dir=str(tmp_path))

    async def get_storage(visitor: Any) -> Any:
        return provider

    monkeypatch.setattr(_core, "_get_file_interface", get_storage)
    return provider


def caller(user: str = "alice") -> Any:
    return SimpleNamespace(_agent=SimpleNamespace(id="agent"), user_id=user)


def write_file(storage: Any, content: bytes, *, user: str = "alice") -> Path:
    path = storage.root_dir / resolve_user_sandbox_relpath("agent", user) / "note.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.mark.asyncio
async def test_bounded_read_exact_limit_and_oversize(storage: Any) -> None:
    write_file(storage, b"12345678")
    assert (
        await _core.read_bounded_text_file(caller(), "note.txt", max_bytes=8)
        == "12345678"
    )
    write_file(storage, b"123456789")
    with pytest.raises(ValueError, match="byte limit"):
        await _core.read_bounded_text_file(caller(), "note.txt", max_bytes=8)


@pytest.mark.asyncio
async def test_head_reads_prefix_of_large_file_and_tail_refuses_partial(
    storage: Any,
) -> None:
    write_file(storage, b"first\r\nsecond\n" + b"x" * 100_000)
    assert (
        await _core.read_bounded_text_file(caller(), "note.txt", head=1, max_bytes=32)
        == "first\r\n"
    )
    with pytest.raises(ValueError, match="byte limit"):
        await _core.read_bounded_text_file(caller(), "note.txt", tail=1, max_bytes=32)


@pytest.mark.asyncio
async def test_real_storage_cross_user_symlink_is_rejected(storage: Any) -> None:
    foreign = write_file(storage, b"FOREIGN_SECRET", user="bob")
    own = write_file(storage, b"own")
    own.unlink()
    own.symlink_to(foreign)
    with pytest.raises(ValueError, match="sandbox"):
        await _core.read_bounded_text_file(caller(), "note.txt", max_bytes=32)


@pytest.mark.asyncio
async def test_linked_user_directory_is_rejected(storage: Any) -> None:
    foreign = write_file(storage, b"foreign", user="bob")
    own = write_file(storage, b"own")
    own.unlink()
    own.parent.rmdir()
    own.parent.symlink_to(foreign.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="sandbox"):
        await _core.read_bounded_text_file(caller(), "note.txt", max_bytes=32)


@pytest.mark.asyncio
async def test_concurrent_callers_read_their_own_same_named_file(storage: Any) -> None:
    write_file(storage, b"alice", user="alice")
    write_file(storage, b"bob", user="bob")
    results = await asyncio.gather(
        *[
            _core.read_bounded_text_file(caller(user), "note.txt", max_bytes=32)
            for user in ("alice", "bob")
        ]
    )
    assert results == ["alice", "bob"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["../bob/note.txt", "/etc/passwd", "C:\\secret", ""])
async def test_bounded_read_rejects_unsafe_paths(storage: Any, path: str) -> None:
    with pytest.raises(ValueError):
        await _core.read_bounded_text_file(caller(), path, max_bytes=32)


@pytest.mark.asyncio
async def test_reads_do_not_create_workspace(storage: Any) -> None:
    with pytest.raises((FileNotFoundError, ValueError)):
        await _core.read_bounded_text_file(caller(), "missing.txt", max_bytes=32)
    assert list(storage.root_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_stream_is_closed_on_early_head_and_cancellation(
    monkeypatch: Any,
) -> None:
    closed: list[bool] = []
    reads: list[int] = []

    class Storage:
        async def stream_file(self, key: str, *, chunk_size: int) -> Any:
            try:
                reads.append(chunk_size)
                yield b"first\nsecond\n"
                await asyncio.Event().wait()
            finally:
                closed.append(True)

    async def get_storage(visitor: Any) -> Any:
        return Storage()

    monkeypatch.setattr(_core, "_get_file_interface", get_storage)
    assert (
        await _core.read_bounded_text_file(caller(), "note.txt", head=1, max_bytes=32)
        == "first\n"
    )
    assert closed == [True] and reads == [33]
    task = asyncio.create_task(
        _core.read_bounded_text_file(caller(), "note.txt", max_bytes=32)
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == [True, True]


@pytest.mark.asyncio
async def test_growing_stream_stops_after_bounded_overread(monkeypatch: Any) -> None:
    consumed: list[int] = []
    closed: list[bool] = []

    class Storage:
        async def stream_file(self, key: str, *, chunk_size: int) -> Any:
            try:
                while True:
                    consumed.append(chunk_size)
                    yield b"x" * chunk_size
            finally:
                closed.append(True)

    async def get_storage(visitor: Any) -> Any:
        return Storage()

    monkeypatch.setattr(_core, "_get_file_interface", get_storage)
    with pytest.raises(ValueError, match="byte limit"):
        await _core.read_bounded_text_file(caller(), "note.txt", max_bytes=9000)
    assert sum(consumed) == 16384 and closed == [True]


@pytest.mark.asyncio
async def test_utf8_crlf_and_tail_across_chunks(monkeypatch: Any) -> None:
    payload = "one €\r\ntwo\nthree".encode()

    class Storage:
        async def stream_file(self, key: str, *, chunk_size: int) -> Any:
            for index in range(len(payload)):
                yield payload[index : index + 1]

    async def get_storage(visitor: Any) -> Any:
        return Storage()

    monkeypatch.setattr(_core, "_get_file_interface", get_storage)
    assert (
        await _core.read_bounded_text_file(caller(), "note.txt", max_bytes=32, head=1)
        == "one €\r\n"
    )
    assert (
        await _core.read_bounded_text_file(caller(), "note.txt", max_bytes=32, tail=2)
        == "two\nthree"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limits", [{"head": 0}, {"tail": -1}, {"head": True}, {"head": 1, "tail": 1}]
)
async def test_invalid_line_limits_do_not_degrade_to_full_read(
    storage: Any, limits: Any
) -> None:
    write_file(storage, b"content")
    with pytest.raises(ValueError):
        await _core.read_bounded_text_file(caller(), "note.txt", max_bytes=32, **limits)


@pytest.mark.asyncio
async def test_bounded_write_checks_utf8_bytes_before_creating_workspace(
    storage: Any,
) -> None:
    with pytest.raises(ValueError, match="byte limit"):
        await _core.write_bounded_text_file(caller(), "note.txt", "€€", max_bytes=4)
    assert list(storage.root_dir.iterdir()) == []
    await _core.write_bounded_text_file(caller(), "note.txt", "éé", max_bytes=4)
    assert await _core.read_bounded_text_file(caller(), "note.txt", max_bytes=4) == "éé"


@pytest.mark.asyncio
async def test_native_write_cannot_follow_cross_user_link(storage: Any) -> None:
    foreign = write_file(storage, b"foreign", user="bob")
    own = write_file(storage, b"own")
    own.unlink()
    own.symlink_to(foreign)
    with pytest.raises(ValueError, match="sandbox"):
        await _core.write_bounded_text_file(
            caller(), "note.txt", "changed", max_bytes=32
        )
    assert foreign.read_bytes() == b"foreign"
