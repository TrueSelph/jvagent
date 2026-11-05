"""Read-only receipt repair must never settle writes or cancelled work."""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai import ModelRetry
from pydantic_ai_harness import StepPersistence
from pydantic_ai_harness.step_persistence import ToolEffectRecord

from jvagent.harness.read_retries import ReadRetryReceipts


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["write", "cancel", "save_failure"])
async def test_uncertain_effects_and_failed_receipts_are_not_hidden(
    scenario: str,
) -> None:
    store = SimpleNamespace(
        get_tool_effect=AsyncMock(
            return_value=ToolEffectRecord(
                run_id="run", tool_call_id="call", tool_name="read", status="started"
            )
        ),
        record_tool_effect=AsyncMock(
            side_effect=(
                RuntimeError("storage unavailable")
                if scenario == "save_failure"
                else None
            )
        ),
    )
    capability = ReadRetryReceipts(StepPersistence(store=store), frozenset({"read"}))

    async def handler(args: Any) -> Any:
        if scenario == "cancel":
            raise asyncio.CancelledError()
        raise ModelRetry("Try another argument")

    expected = (
        asyncio.CancelledError
        if scenario == "cancel"
        else RuntimeError if scenario == "save_failure" else ModelRetry
    )
    with pytest.raises(expected):
        await capability.wrap_tool_execute(
            SimpleNamespace(run_id="run"),
            call=SimpleNamespace(tool_call_id="call"),
            tool_def=SimpleNamespace(name="write" if scenario == "write" else "read"),
            args={},
            handler=handler,
        )
    if scenario != "save_failure":
        store.record_tool_effect.assert_not_awaited()
