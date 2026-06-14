"""Streaming response abstraction for interactions and model actions."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class StreamWriter(Protocol):
    """Accumulate streamed chunks and finalize to a stored interaction response."""

    async def write_chunk(self, chunk: str) -> None:
        """Append one token or text chunk."""
        ...

    async def finalize(self, response: str) -> None:
        """Persist or seal the full response after streaming completes."""
        ...
