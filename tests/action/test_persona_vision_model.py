"""Tests for PersonaAction vision_model_* attribute resolution and routing."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.persona.persona_action import PersonaAction


def _make_lma(name: str = "primary") -> MagicMock:
    """Return a minimal LanguageModelAction-like mock."""
    from jvagent.action.model.language.base import LanguageModelAction

    action = MagicMock(spec=LanguageModelAction)
    action.__class__ = LanguageModelAction
    action.name = name
    return action


# ---------------------------------------------------------------------------
# _get_vision_model_action()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_vision_model_action_returns_none_when_not_configured():
    """When vision_model_action_type is empty (default) the helper returns None."""
    persona = PersonaAction()
    # default is ""
    result = await persona._get_vision_model_action()
    assert result is None


@pytest.mark.asyncio
async def test_get_vision_model_action_returns_resolved_action():
    """Returns the action when vision_model_action_type is set and resolvable."""
    persona = PersonaAction()
    persona.vision_model_action_type = "OpenAILanguageModelAction"

    vision_action = _make_lma("vision")
    with patch.object(PersonaAction, "get_action", new_callable=AsyncMock) as ga:
        ga.return_value = vision_action
        result = await persona._get_vision_model_action()

    assert result is vision_action
    ga.assert_awaited_once_with("OpenAILanguageModelAction")


@pytest.mark.asyncio
async def test_get_vision_model_action_falls_back_on_wrong_type():
    """Returns None (falls back) when the resolved action is not a LanguageModelAction."""
    persona = PersonaAction()
    persona.vision_model_action_type = "SomeOtherAction"

    non_lma = MagicMock()  # not spec'd as LanguageModelAction
    with patch.object(PersonaAction, "get_action", new_callable=AsyncMock) as ga:
        ga.return_value = non_lma
        result = await persona._get_vision_model_action()

    assert result is None


@pytest.mark.asyncio
async def test_get_vision_model_action_falls_back_on_exception():
    """Returns None (falls back) when get_action raises."""
    persona = PersonaAction()
    persona.vision_model_action_type = "BrokenAction"

    with patch.object(
        PersonaAction,
        "get_action",
        new_callable=AsyncMock,
        side_effect=RuntimeError("oops"),
    ):
        result = await persona._get_vision_model_action()

    assert result is None


# ---------------------------------------------------------------------------
# _generate_response() — vision pass routing
# ---------------------------------------------------------------------------


def _make_visitor(image_urls: list) -> MagicMock:
    visitor = MagicMock()
    visitor.data = {"image_urls": image_urls}
    visitor.stream = False
    visitor.response_bus = None
    visitor.session_id = None
    return visitor


def _make_interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.utterance = "what is in this image?"
    interaction.image_interpretation = None
    interaction.save = AsyncMock()
    interaction.session_id = "sess-1"
    interaction.channel = "web"
    interaction.id = "int-1"
    interaction.user_id = "u-1"
    interaction.response = None
    interaction.record_action_execution = MagicMock()
    return interaction


@pytest.mark.asyncio
async def test_vision_pass_uses_primary_model_action_by_default():
    """When vision_model_action_type is empty, generate_image_interpretation receives
    the primary model_action (no secondary lookup)."""
    persona = PersonaAction()
    persona.vision_model_action_type = ""
    persona.vision_model = ""
    persona.vision_model_temperature = None
    persona.vision_model_max_tokens = None

    primary = _make_lma("primary")
    primary.generate = AsyncMock(return_value="plain text reply")
    primary.create_multimodal_content = MagicMock(return_value="<multi>")

    visitor = _make_visitor(["https://example.com/img.jpg"])
    interaction = _make_interaction()

    with (
        patch(
            "jvagent.action.persona.persona_action.generate_image_interpretation",
            new_callable=AsyncMock,
            return_value="image desc",
        ) as mock_interp,
        patch.object(
            PersonaAction,
            "_compose_prompt",
            new=AsyncMock(return_value="<system>"),
        ),
        patch.object(
            PersonaAction,
            "_get_conversation_history",
            new=AsyncMock(return_value=[]),
        ),
        patch.object(
            PersonaAction,
            "_pipe_response",
            new=AsyncMock(),
        ),
    ):
        await persona._generate_response(
            interaction,
            visitor,
            applicable_directives=[{"directive": "say hello"}],
            applicable_parameters=[],
            use_history=False,
            history_limit=4,
            with_utterance=True,
            with_interpretation=False,
            with_event=True,
            with_response=True,
            max_statement_length=None,
            transient=False,
            model_action=primary,
        )

    # The first arg to generate_image_interpretation must be the primary action
    args, kwargs = mock_interp.call_args
    assert args[1] is primary
    assert "model" not in kwargs
    assert "temperature" not in kwargs
    assert "max_tokens" not in kwargs


@pytest.mark.asyncio
async def test_vision_pass_uses_dedicated_model_action_when_configured():
    """When vision_model_action_type is set and resolves, generate_image_interpretation
    receives the dedicated action, not the primary one."""
    persona = PersonaAction()
    persona.vision_model_action_type = "VisionModelAction"
    persona.vision_model = "gpt-4o"
    persona.vision_model_temperature = 0.1
    persona.vision_model_max_tokens = 512

    primary = _make_lma("primary")
    primary.generate = AsyncMock(return_value="plain text reply")
    primary.create_multimodal_content = MagicMock(return_value="<multi>")

    vision = _make_lma("vision")

    visitor = _make_visitor(["https://example.com/img.jpg"])
    interaction = _make_interaction()

    with (
        patch.object(
            PersonaAction,
            "_get_vision_model_action",
            new=AsyncMock(return_value=vision),
        ),
        patch(
            "jvagent.action.persona.persona_action.generate_image_interpretation",
            new_callable=AsyncMock,
            return_value="vision desc",
        ) as mock_interp,
        patch.object(
            PersonaAction,
            "_compose_prompt",
            new=AsyncMock(return_value="<system>"),
        ),
        patch.object(
            PersonaAction,
            "_get_conversation_history",
            new=AsyncMock(return_value=[]),
        ),
        patch.object(
            PersonaAction,
            "_pipe_response",
            new=AsyncMock(),
        ),
    ):
        await persona._generate_response(
            interaction,
            visitor,
            applicable_directives=[{"directive": "say hello"}],
            applicable_parameters=[],
            use_history=False,
            history_limit=4,
            with_utterance=True,
            with_interpretation=False,
            with_event=True,
            with_response=True,
            max_statement_length=None,
            transient=False,
            model_action=primary,
        )

    args, kwargs = mock_interp.call_args
    # Dedicated vision action used for the interpretation pass
    assert args[1] is vision
    # Overrides forwarded
    assert kwargs["model"] == "gpt-4o"
    assert kwargs["temperature"] == 0.1
    assert kwargs["max_tokens"] == 512


@pytest.mark.asyncio
async def test_primary_model_action_still_used_for_main_generate():
    """Even when a dedicated vision model action is configured, the primary model_action
    is used for the main user-visible reply (model_action.generate)."""
    persona = PersonaAction()
    persona.vision_model_action_type = "VisionModelAction"
    persona.vision_model = "gpt-4o"
    persona.vision_model_temperature = None
    persona.vision_model_max_tokens = None

    primary = _make_lma("primary")
    primary.generate = AsyncMock(return_value="main reply")
    primary.create_multimodal_content = MagicMock(return_value="<multi>")

    vision = _make_lma("vision")

    visitor = _make_visitor(["https://example.com/img.jpg"])
    interaction = _make_interaction()

    with (
        patch.object(
            PersonaAction,
            "_get_vision_model_action",
            new=AsyncMock(return_value=vision),
        ),
        patch(
            "jvagent.action.persona.persona_action.generate_image_interpretation",
            new_callable=AsyncMock,
            return_value="vision desc",
        ),
        patch.object(
            PersonaAction,
            "_compose_prompt",
            new=AsyncMock(return_value="<system>"),
        ),
        patch.object(
            PersonaAction,
            "_get_conversation_history",
            new=AsyncMock(return_value=[]),
        ),
        patch.object(
            PersonaAction,
            "_pipe_response",
            new=AsyncMock(),
        ),
    ):
        result = await persona._generate_response(
            interaction,
            visitor,
            applicable_directives=[{"directive": "reply"}],
            applicable_parameters=[],
            use_history=False,
            history_limit=4,
            with_utterance=True,
            with_interpretation=False,
            with_event=True,
            with_response=True,
            max_statement_length=None,
            transient=False,
            model_action=primary,
        )

    assert result == "main reply"
    # primary.generate was called for the main reply
    primary.generate.assert_awaited()
    # The vision action's generate was NOT called (it was only passed to generate_image_interpretation)
    vision.generate.assert_not_called()
