"""Structural Protocol contracts for optional bootstrap-time validation of action interfaces."""

from __future__ import annotations

from typing import Any, AsyncGenerator, Protocol, runtime_checkable


@runtime_checkable
class LanguageModelProvider(Protocol):
    """Minimal surface for actions that satisfy language-model responsibilities."""

    async def query_sync(self, prompt: str, **kwargs: Any) -> Any:
        """Run a single-turn completion and return a model result wrapper."""
        ...


@runtime_checkable
class VectorStoreProvider(Protocol):
    """Minimal surface for embedding-backed retrieval actions."""

    async def search(self, query: str, **kwargs: Any) -> Any:
        """Execute a retrieval query against the backing index."""
        ...


@runtime_checkable
class ChannelAdapter(Protocol):
    """Outbound messaging adapters (WhatsApp, email, etc.)."""

    async def send_message(self, **kwargs: Any) -> Any:
        """Deliver one outbound message; signature varies by channel."""
        ...
