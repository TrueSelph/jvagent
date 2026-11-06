"""Attachment admission preserves SDK content and refuses every invalid member."""

import base64
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.messages import BinaryContent, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai_harness.step_persistence import continue_run

from jvagent.action.native_harness import NativeHarnessAction
from jvagent.harness.inputs import INPUT_NOTICE, NativeInputError, native_user_prompt
from jvagent.harness.native import build_native_agent, run_native_agent
from jvagent.harness.recovery import history_for_new_turn
from jvagent.harness.step_store import InteractionStepStore
from jvagent.memory.interaction import Interaction


def attachment(
    raw: bytes = b"\x89PNG\r\n\xff", mime: str = "image/png"
) -> dict[str, str]:
    return {
        "base64": base64.b64encode(raw).decode(),
        "mime_type": mime,
        "filename": "sample",
    }


@pytest.mark.parametrize("data", [None, {}, {"other": "value"}, {"image_urls": []}])
def test_no_uploads_preserves_plain_prompt(data: Any) -> None:
    assert native_user_prompt("hello", data) == "hello"


@pytest.mark.parametrize(
    "data",
    [
        {"image_urls": "https://private.invalid/file"},
        {"image_urls": ["https://private.invalid/file"]},
        {"files": [{"url": "http://127.0.0.1/private"}]},
        {"files": [attachment(), {"base64": "invalid!", "mime_type": "image/png"}]},
        {"documents": [attachment(b"", "text/plain")]},
        {"attachments": [attachment(b"binary", "application/octet-stream")]},
        {"attachments": [attachment(b"\xff", "text/plain")]},
        {"attachments": [{**attachment(), "filename": "private\nname"}]},
        {"image_urls": [{**attachment(), "base64": "data:image/jpeg;base64,iVBORw0K"}]},
        {"image_urls": [attachment()] * 21},
    ],
)
def test_invalid_member_refuses_complete_set_without_values(data: Any) -> None:
    with pytest.raises(NativeInputError) as caught:
        native_user_prompt("read files", data)
    assert str(caught.value) == INPUT_NOTICE
    assert "private" not in str(caught.value)


def test_cross_key_and_decoded_byte_limits(monkeypatch: Any) -> None:
    from jvagent.harness import inputs

    monkeypatch.setattr(inputs, "MAX_UPLOAD_ITEM_BYTES", 8)
    monkeypatch.setattr(inputs, "MAX_TOTAL_BYTES", 12)
    for data in [
        {"files": [attachment(b"123456789")]},
        {"files": [attachment(b"1234567")], "image_urls": [attachment(b"123456")]},
        {"files": [attachment()] * 10, "documents": [attachment()] * 11},
    ]:
        with pytest.raises(NativeInputError):
            native_user_prompt("read", data)


def test_mixed_content_preserves_order_bytes_and_declared_mime() -> None:
    image = attachment()
    image["base64"] = "data:image/png;base64," + image["base64"]
    parts = native_user_prompt(
        "compare",
        {
            "image_urls": [image],
            "documents": [
                attachment(b"%PDF-example", "application/pdf"),
                attachment(b"JUNIPER-28", "text/plain"),
            ],
        },
    )
    assert parts[0] == "compare"
    assert parts[2].data == b"\x89PNG\r\n\xff" and parts[2].media_type == "image/png"
    assert parts[4].data == b"%PDF-example" and parts[4].media_type == "application/pdf"
    assert parts[6].content == "JUNIPER-28"


@pytest.mark.asyncio
async def test_rejected_input_has_own_notice_without_model_work(
    monkeypatch: Any,
) -> None:
    async def reject(self: Any, visitor: Any) -> None:
        native_user_prompt("read", {"files": ["http://127.0.0.1/private"]})

    monkeypatch.setattr(NativeHarnessAction, "_execute", reject)
    interaction = SimpleNamespace(
        native_run={},
        has_emitted=lambda: False,
        save=AsyncMock(),
        compute_usage=lambda: {},
    )
    publish = AsyncMock(return_value=True)
    facade = SimpleNamespace(
        get_responder=AsyncMock(return_value=SimpleNamespace(publish=publish))
    )
    await NativeHarnessAction.execute(facade, SimpleNamespace(interaction=interaction))
    assert interaction.native_run == {"status": "blocked", "reason": "invalid_input"}
    assert publish.call_args.args[0] == INPUT_NOTICE


@pytest.mark.asyncio
async def test_sdk_multimodal_checkpoint_and_followup(tmp_path: Path) -> None:
    skill = tmp_path / "inspect"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: inspect\ndescription: Read attachments\n---\nRead supplied attachments; do not guess.\n"
    )
    seen = []

    def model(messages: Any, info: Any) -> ModelResponse:
        seen.extend(messages)
        return ModelResponse(parts=[TextPart("inspected")])

    interaction = Interaction(
        conversation_id="conversation",
        user_id="caller",
        session_id="session",
        utterance="Inspect",
    )
    await interaction.save()
    store = InteractionStepStore(interaction, agent_id="agent")

    async def allow(name: str) -> bool:
        return True

    agent = build_native_agent(
        FunctionModel(model),
        skill_directory=skill,
        tools=[],
        access_check=allow,
        instructions="Read user content.",
        step_store=store,
    )
    prompt = native_user_prompt("Inspect", {"image_urls": [attachment()]})
    result = await run_native_agent(
        agent, prompt, conversation_id="session", run_id=interaction.id
    )
    assert result.output == "inspected"
    messages = await continue_run(store, run_id=interaction.id)
    binary = [
        c
        for m in messages
        for p in m.parts
        if isinstance(p, UserPromptPart) and isinstance(p.content, list)
        for c in p.content
        if isinstance(c, BinaryContent)
    ]
    assert len(binary) == 1 and binary[0].data == b"\x89PNG\r\n\xff"
    interaction.native_run = {"run_id": interaction.id, "status": "delivered"}
    await interaction.save()
    loaded = await Interaction.get(interaction.id)
    restored = await history_for_new_turn(
        loaded, conversation_id="conversation", user_id="caller", session_id="session"
    )
    seen.clear()
    await run_native_agent(
        agent,
        "What did I send?",
        conversation_id="session",
        run_id="followup",
        message_history=restored.messages,
    )
    assert any(
        isinstance(c, BinaryContent) and c.data == binary[0].data
        for m in seen
        for p in m.parts
        if isinstance(p, UserPromptPart) and isinstance(p.content, list)
        for c in p.content
    )


@pytest.mark.parametrize(
    "policy",
    [None, True, "image/png", ["image/png", "image/png"], ["application/private"], [1]],
)
def test_invalid_host_media_policy_is_a_configuration_error(policy: Any) -> None:
    from jvagent.harness.inputs import validate_input_media_types

    with pytest.raises(ValueError, match="input_media_types"):
        validate_input_media_types(policy)


def test_empty_host_policy_refuses_upload_without_widening_from_client_data() -> None:
    data = {"image_urls": [attachment()], "input_media_types": ["image/png"]}
    with pytest.raises(NativeInputError):
        native_user_prompt("read", data, allowed_media_types=[])
    assert native_user_prompt("hello", {}, allowed_media_types=[]) == "hello"
    assert isinstance(
        native_user_prompt("read", data, allowed_media_types=["image/png"])[2],
        BinaryContent,
    )
