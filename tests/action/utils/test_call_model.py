"""Tests for jvagent.action.utils.call_model."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.utils.call_model import call_model


def _action_with_model(generate_return, *, model_attrs=None):
    model = MagicMock()
    model.generate = AsyncMock(return_value=generate_return)
    action = MagicMock()
    action.get_model_action = AsyncMock(return_value=model)
    action.get_action = AsyncMock(return_value=None)
    action.get_class_name = MagicMock(return_value="StubAction")
    action.model = None
    action.model_temperature = None
    action.model_max_tokens = None
    if model_attrs:
        for key, value in model_attrs.items():
            setattr(action, key, value)
    return action, model


@pytest.mark.asyncio
async def test_call_model_returns_false_when_no_model():
    action = MagicMock()
    action.get_model_action = AsyncMock(return_value=None)
    assert await call_model(action, "hi", "sys") is False


@pytest.mark.asyncio
async def test_call_model_returns_false_when_action_none():
    assert await call_model(None, "hi", "sys") is False


@pytest.mark.asyncio
async def test_call_model_text_path():
    action, model = _action_with_model("hello world")
    result = await call_model(action, "hi", "be brief")
    assert result == "hello world"
    kwargs = model.generate.await_args.kwargs
    assert kwargs["prompt"] == "hi"
    assert kwargs["system"] == "be brief"
    assert "response_format" not in kwargs


@pytest.mark.asyncio
async def test_call_model_json_mode_bare_object():
    action, model = _action_with_model('{"mode": "direct_contact", "ok": true}')
    result = await call_model(action, "x", "sys", json_response=True)
    assert result == {"mode": "direct_contact", "ok": True}
    assert model.generate.await_args.kwargs["response_format"] == {
        "type": "json_object"
    }
    assert model.generate.await_args.kwargs["stream"] is False


@pytest.mark.asyncio
async def test_call_model_json_mode_fenced():
    action, _model = _action_with_model('```json\n{"mode": "agent_escalation"}\n```')
    result = await call_model(action, "x", "sys", json_response=True)
    assert result == {"mode": "agent_escalation"}


@pytest.mark.asyncio
async def test_call_model_json_mode_embedded_object():
    action, _model = _action_with_model('Here you go: {"a": 1} done')
    result = await call_model(action, "x", "sys", json_response=True)
    assert result == {"a": 1}


@pytest.mark.asyncio
async def test_call_model_returns_none_on_exception():
    model = MagicMock()
    model.generate = AsyncMock(side_effect=RuntimeError("boom"))
    action = MagicMock()
    action.get_model_action = AsyncMock(return_value=model)
    action.get_action = AsyncMock(return_value=None)
    action.get_class_name = MagicMock(return_value="StubAction")
    assert await call_model(action, "x", "sys") is None


@pytest.mark.asyncio
async def test_call_model_passes_action_sampling_attrs():
    action, model = _action_with_model(
        "ok",
        model_attrs={
            "model": "gpt-4.1",
            "model_temperature": 0.2,
            "model_max_tokens": 64,
        },
    )
    await call_model(action, "x", "sys")
    kwargs = model.generate.await_args.kwargs
    assert kwargs["model"] == "gpt-4.1"
    assert kwargs["temperature"] == 0.2
    assert kwargs["max_tokens"] == 64


@pytest.mark.asyncio
async def test_call_model_explicit_sampling_overrides_action_attrs():
    action, model = _action_with_model(
        "ok",
        model_attrs={
            "model": "gpt-4.1",
            "model_temperature": 0.2,
            "model_max_tokens": 64,
        },
    )
    await call_model(
        action,
        "x",
        "sys",
        model="llama3.2",
        temperature=0.7,
        max_tokens=128,
    )
    kwargs = model.generate.await_args.kwargs
    assert kwargs["model"] == "llama3.2"
    assert kwargs["temperature"] == 0.7
    assert kwargs["max_tokens"] == 128


@pytest.mark.asyncio
async def test_call_model_provider_resolves_lm_action():
    ollama = MagicMock()
    ollama.generate = AsyncMock(return_value="from ollama")
    action = MagicMock()
    action.get_model_action = AsyncMock(return_value=MagicMock())
    action.get_action = AsyncMock(return_value=ollama)
    action.get_class_name = MagicMock(return_value="StubAction")
    action.model = None
    action.model_temperature = None
    action.model_max_tokens = None

    result = await call_model(action, "hi", "sys", provider="ollama")
    assert result == "from ollama"
    action.get_action.assert_awaited_with("OllamaLanguageModelAction")
    action.get_model_action.assert_not_awaited()


@pytest.mark.asyncio
async def test_call_model_unknown_provider_returns_false():
    action = MagicMock()
    action.get_model_action = AsyncMock(return_value=MagicMock())
    action.get_action = AsyncMock(return_value=None)
    assert await call_model(action, "hi", "sys", provider="not-a-provider") is False


@pytest.mark.asyncio
async def test_call_model_injected_model_action_skips_resolution():
    injected = MagicMock()
    injected.generate = AsyncMock(return_value="injected")
    action = MagicMock()
    action.get_model_action = AsyncMock()
    action.get_action = AsyncMock()
    action.get_class_name = MagicMock(return_value="StubAction")
    action.model = None
    action.model_temperature = None
    action.model_max_tokens = None

    result = await call_model(action, "hi", "sys", model_action=injected)
    assert result == "injected"
    action.get_model_action.assert_not_awaited()
    action.get_action.assert_not_awaited()


@pytest.mark.asyncio
async def test_call_model_prebuilt_history_skips_fetch():
    action, model = _action_with_model("ok")
    prebuilt = [{"role": "user", "content": "prior"}]
    with patch(
        "jvagent.action.utils.call_model._load_history",
        new=AsyncMock(),
    ) as load:
        await call_model(action, "hi", "sys", history=prebuilt, use_history=True)
    load.assert_not_awaited()
    assert model.generate.await_args.kwargs["history"] == prebuilt


@pytest.mark.asyncio
async def test_call_model_history_flags_passed_to_conversation():
    action, model = _action_with_model("ok")
    conversation = MagicMock()
    conversation.get_interaction_history = AsyncMock(
        return_value=[{"role": "user", "content": "earlier"}]
    )
    interaction = MagicMock()
    interaction.id = "ix-1"
    interaction.conversation = conversation
    interaction.response = None

    await call_model(
        action,
        "hi",
        "sys",
        use_history=True,
        interaction=interaction,
        history_limit=7,
        with_utterance=True,
        with_response=False,
        with_interpretation=True,
        with_event=False,
        max_statement_length=50,
    )

    conversation.get_interaction_history.assert_awaited_once_with(
        limit=7,
        excluded="ix-1",
        with_utterance=True,
        with_response=False,
        with_interpretation=True,
        with_event=False,
        formatted=True,
        max_statement_length=50,
    )
    assert model.generate.await_args.kwargs["history"] == [
        {"role": "user", "content": "earlier"}
    ]


@pytest.mark.asyncio
async def test_call_model_history_appends_interaction_response_for_coherence():
    action, model = _action_with_model("ok")
    conversation = MagicMock()
    conversation.get_interaction_history = AsyncMock(
        return_value=[{"role": "user", "content": "earlier"}]
    )
    interaction = MagicMock()
    interaction.id = "ix-1"
    interaction.conversation = conversation
    interaction.response = "partial draft"

    await call_model(
        action,
        "hi",
        "sys",
        use_history=True,
        interaction=interaction,
        with_response=False,
        with_interpretation=True,
    )
    hist = model.generate.await_args.kwargs["history"]
    assert hist[-1] == {"role": "assistant", "content": "partial draft"}


@pytest.mark.asyncio
async def test_call_model_history_via_get_conversation_when_no_conversation_attr():
    """Real Interaction nodes have get_conversation(), not .conversation."""
    action, model = _action_with_model("ok")
    conversation = MagicMock()
    conversation.get_interaction_history = AsyncMock(
        return_value=[
            {"role": "user", "content": "quote these links"},
            {"role": "assistant", "content": "Sure — send them over."},
        ]
    )
    interaction = MagicMock()
    interaction.id = "ix-current"
    interaction.conversation = None
    interaction.visitor = None
    interaction.response = None
    interaction.get_conversation = AsyncMock(return_value=conversation)

    await call_model(
        action,
        "hi",
        "sys",
        use_history=True,
        interaction=interaction,
        history_limit=6,
        with_utterance=True,
        with_response=True,
        with_interpretation=False,
        with_event=False,
        max_statement_length=200,
    )

    interaction.get_conversation.assert_awaited_once()
    conversation.get_interaction_history.assert_awaited_once_with(
        limit=6,
        excluded="ix-current",
        with_utterance=True,
        with_response=True,
        with_interpretation=False,
        with_event=False,
        formatted=True,
        max_statement_length=200,
    )
    assert model.generate.await_args.kwargs["history"] == [
        {"role": "user", "content": "quote these links"},
        {"role": "assistant", "content": "Sure — send them over."},
    ]


@pytest.mark.asyncio
async def test_call_model_json_forces_stream_false():
    action, model = _action_with_model('{"a": 1}')
    await call_model(action, "x", "sys", json_response=True, stream=True)
    assert model.generate.await_args.kwargs["stream"] is False
