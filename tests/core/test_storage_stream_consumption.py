"""Published storage must not consume a whole file before its first chunk."""

from pathlib import Path
from typing import Any

import pytest
from jvspatial.storage.interfaces import local
from jvspatial.storage.interfaces.local import LocalFileInterface


@pytest.mark.asyncio
async def test_storage_stream_consumes_one_chunk_and_closes_on_early_exit(
    tmp_path: Path, monkeypatch: Any
) -> None:
    payload = b"bounded-stream-data" * 65536
    (tmp_path / "record.bin").write_bytes(payload)
    storage = LocalFileInterface(root_dir=str(tmp_path))
    real_open = open
    reads: list[int] = []
    closed: list[bool] = []

    class Reader:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.file = real_open(*args, **kwargs)

        def read(self, size: int) -> bytes:
            value = self.file.read(size)
            reads.append(len(value))
            return value

        def close(self) -> None:
            closed.append(True)
            self.file.close()

        def __enter__(self) -> "Reader":
            return self

        def __exit__(self, *args: Any) -> None:
            self.close()

    monkeypatch.setattr(local, "open", Reader, raising=False)
    stream = storage.stream_file("record.bin", chunk_size=1024)
    try:
        assert await anext(stream) == payload[:1024]
        assert reads == [1024], "first chunk buffered the remaining file"
    finally:
        await stream.aclose()
    assert closed == [True]
    assert reads == [1024]


@pytest.mark.asyncio
async def test_storage_stream_returns_exact_bytes(tmp_path: Path) -> None:
    payload = bytes(range(256)) * 512
    (tmp_path / "record.bin").write_bytes(payload)
    storage = LocalFileInterface(root_dir=str(tmp_path))
    chunks = [
        chunk async for chunk in storage.stream_file("record.bin", chunk_size=997)
    ]
    assert b"".join(chunks) == payload
    assert all(0 < len(chunk) <= 997 for chunk in chunks)
