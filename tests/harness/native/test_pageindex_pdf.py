"""Classic PDF worker completions stay inside the admitted SDK operation."""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.messages import ModelRequest, ModelResponse
from pydantic_ai.usage import RequestUsage

from jvagent.action.pageindex import documents
from jvagent.action.pageindex.core import utils
from jvagent.action.pageindex.llm_bridge import (
    PageIndexCancelled,
    attach_native_pdf_bridge,
)
from jvagent.action.pageindex.native_pdf import NativePDFBridge


@pytest.mark.asyncio
async def test_pdf_worker_routes_sync_async_and_continuation_to_owner() -> None:
    owner = asyncio.get_running_loop()
    calls = []

    async def complete(
        prompt: str | list[Any],
        *,
        message_history: list[Any] | None = None,
        allow_partial: bool = False,
    ) -> Any:
        assert asyncio.get_running_loop() is owner
        calls.append((prompt, message_history, allow_partial))
        return SimpleNamespace(
            output="ranked",
            response=ModelResponse(
                parts=[],
                usage=RequestUsage(input_tokens=3, output_tokens=1),
                finish_reason="length" if allow_partial else "stop",
            ),
        )

    bridge = NativePDFBridge(complete)

    def worker() -> None:
        attach_native_pdf_bridge(bridge)
        try:
            assert utils.count_tokens("é") == 2
            assert utils.llm_completion(
                "legacy-model",
                "continue",
                [
                    {"role": "user", "content": "before"},
                    {"role": "assistant", "content": "part"},
                ],
                return_finish_reason=True,
            ) == ("ranked", "max_output_reached")
            assert (
                asyncio.run(utils.llm_acompletion("legacy-model", "summary"))
                == "ranked"
            )
        finally:
            attach_native_pdf_bridge(None)

    await asyncio.to_thread(worker)
    assert calls[0][0] == "continue" and calls[0][2]
    assert isinstance(calls[0][1][0], ModelRequest)
    assert isinstance(calls[0][1][1], ModelResponse)
    assert calls[1] == ("summary", [], False)


@pytest.mark.asyncio
async def test_pdf_worker_cannot_publish_after_parser_swallows_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def complete(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Budget refused")

    bridge = NativePDFBridge(complete)
    fallback = Mock(side_effect=AssertionError("Legacy transport"))
    monkeypatch.setattr(documents, "extract_native_pdf_pages", lambda _: [("text", 4)])

    def parser(*args: Any, **kwargs: Any) -> dict[str, Any]:
        for _ in range(2):
            with pytest.raises(PageIndexCancelled):
                from jvagent.action.pageindex.llm_bridge import llm_completion

                llm_completion("legacy-model", "parse", _real_impl=fallback)
        return {"structure": [{"title": "Partial"}]}

    monkeypatch.setattr(documents, "page_index_main", parser)
    with pytest.raises(RuntimeError, match="Budget refused"):
        await asyncio.to_thread(
            documents._pdf_page_index_worker,
            None,
            object(),
            log_base=None,
            model="gpt-4.1",
            toc_check_page_num=None,
            max_page_num_each_node=None,
            max_token_num_each_node=None,
            if_add_node_id=True,
            if_add_node_text=True,
            if_add_node_summary=True,
            if_add_doc_description=True,
            native_bridge=bridge,
        )
    fallback.assert_not_called()


@pytest.mark.asyncio
async def test_pdf_owner_cancellation_drains_in_flight_provider() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def complete(*args: Any, **kwargs: Any) -> Any:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    bridge = NativePDFBridge(complete)
    worker = asyncio.create_task(asyncio.to_thread(bridge.call, "parse"))
    await asyncio.wait_for(started.wait(), 2)
    await bridge.stop()
    assert cancelled.is_set()
    with pytest.raises(PageIndexCancelled):
        await worker
