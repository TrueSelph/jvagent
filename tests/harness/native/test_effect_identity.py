"""A provider call-ID collision must not erase durable mutation facts."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic import TypeAdapter
from pydantic_ai_harness.step_persistence import ToolEffectRecord

from jvagent.harness.mutation_tools import MutationOutcomeUnknown
from jvagent.harness.step_store import InteractionStepStore


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["tool", "summary", "restart", "key"])
async def test_effect_identity_and_mutation_facts_cannot_be_replaced(
    change: str,
) -> None:
    interaction = SimpleNamespace(native_steps={})

    async def save() -> None:
        pass

    interaction.save = save
    store = InteractionStepStore(interaction)
    original = ToolEffectRecord(
        run_id="run",
        tool_call_id="same-call",
        tool_name="write",
        status="completed",
        effect_summary="jv:mutation:file-write:v1",
        idempotency_key="scope-key",
    )
    await store.record_tool_effect(original)
    changes = {
        "tool": {"tool_name": "read"},
        "summary": {"effect_summary": None},
        "restart": {"status": "started"},
        "key": {"idempotency_key": "other"},
    }
    with pytest.raises(ValueError, match="effect"):
        await store.record_tool_effect(replace(original, **changes[change]))
    assert (
        await store.get_tool_effect(run_id="run", tool_call_id="same-call") == original
    )


@pytest.mark.asyncio
async def test_effect_annotation_finish_and_identical_retry_are_supported() -> None:
    interaction = SimpleNamespace(native_steps={})

    async def save() -> None:
        pass

    interaction.save = save
    store = InteractionStepStore(interaction)
    initial = ToolEffectRecord(
        run_id="run", tool_call_id="call", tool_name="write", status="started"
    )
    await store.record_tool_effect(initial)
    marked = replace(
        initial, effect_summary="jv:mutation:file-write:v1", idempotency_key="key"
    )
    await store.record_tool_effect(marked)
    final = replace(marked, status="completed")
    await store.record_tool_effect(final)
    await store.record_tool_effect(final)
    assert len(interaction.native_steps["effects"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("run", ["run", "child-run"])
async def test_semantic_effect_key_cannot_be_claimed_by_another_call(run: str) -> None:
    interaction = SimpleNamespace(native_steps={})

    async def save() -> None:
        pass

    interaction.save = save
    store = InteractionStepStore(interaction)
    original = ToolEffectRecord(
        run_id="run",
        tool_call_id="first",
        tool_name="send",
        status="completed",
        effect_summary="jv:mutation:whatsapp-send:v1",
        idempotency_key="semantic-key",
    )
    await store.record_tool_effect(original)
    with pytest.raises(MutationOutcomeUnknown, match="already claimed"):
        await store.record_tool_effect(
            replace(original, run_id=run, tool_call_id="second")
        )
    assert interaction.native_steps["effects"] == [
        __import__("pydantic")
        .TypeAdapter(ToolEffectRecord)
        .dump_python(original, mode="json")
    ]
