"""Tests for ``deliver_via_persona`` ``delivery_intent`` branching (Wave 9i.3).

The respond_slim path picks one of two ``delivery_instruction`` templates
based on ``delivery_intent``:

- ``engine_output`` (default) — treat ``content`` as a pre-composed answer.
- ``smalltalk_emit`` — treat ``content`` as a Reflex-generated placeholder
  hint; ask persona to produce a brief in-character greeting/ack.

Both instructions are appended to ``persona_description`` via
``respond_slim(extra_system=...)``. These tests assert which template was
chosen by inspecting the captured ``extra_system`` argument.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.helm.reasoning.delivery.persona_delivery import (
    deliver_via_persona,
)

pytestmark = pytest.mark.asyncio


def _make_persona():
    persona = MagicMock()
    persona.enabled = True
    persona.respond_slim = AsyncMock()
    return persona


def _make_visitor(utterance: str = "hi"):
    interaction = MagicMock()
    interaction.utterance = utterance
    visitor = MagicMock()
    visitor.interaction = interaction
    return visitor


def _make_action(persona):
    action = MagicMock()
    action.get_action = AsyncMock(return_value=persona)
    action.publish = AsyncMock()
    return action


async def test_default_delivery_intent_uses_engine_output_instruction():
    persona = _make_persona()
    action = _make_action(persona)
    visitor = _make_visitor("Tell me about Paris")

    await deliver_via_persona(
        action=action,
        visitor=visitor,
        content="Paris is the capital of France.",
        response_mode="publish",
        degenerate_response_max_chars=0,
    )

    persona.respond_slim.assert_awaited_once()
    extra_system = persona.respond_slim.call_args.kwargs["extra_system"]
    assert "You produced the following content" in extra_system
    assert "Paris is the capital of France." in extra_system
    # Smalltalk-specific phrases should NOT appear.
    assert "brief in-character" not in extra_system
    assert "placeholder ack" not in extra_system


async def test_smalltalk_intent_uses_smalltalk_instruction():
    persona = _make_persona()
    action = _make_action(persona)
    visitor = _make_visitor("Hi there!")

    await deliver_via_persona(
        action=action,
        visitor=visitor,
        content="Hi!",  # Reflex's placeholder draft
        response_mode="publish",
        degenerate_response_max_chars=0,
        delivery_intent="smalltalk_emit",
    )

    persona.respond_slim.assert_awaited_once()
    extra_system = persona.respond_slim.call_args.kwargs["extra_system"]
    assert "brief in-character" in extra_system
    assert "Hi there!" in extra_system  # user's actual utterance
    assert "Hi!" in extra_system  # placeholder draft (as hint)
    # Engine-output-specific phrases should NOT appear.
    assert "You produced the following content" not in extra_system


async def test_degenerate_skip_disabled_when_max_chars_zero():
    """``degenerate_response_max_chars=0`` → even single-char text reaches persona."""
    persona = _make_persona()
    action = _make_action(persona)
    visitor = _make_visitor("?")

    await deliver_via_persona(
        action=action,
        visitor=visitor,
        content="?",  # 1 char — would be degenerate at default threshold of 25
        response_mode="publish",
        degenerate_response_max_chars=0,
        delivery_intent="smalltalk_emit",
    )

    # respond_slim was called (not the raw publish branch).
    persona.respond_slim.assert_awaited_once()
    action.publish.assert_not_called()


async def test_degenerate_skip_active_when_max_chars_positive():
    """Positive ``degenerate_response_max_chars`` still skips persona on short text."""
    persona = _make_persona()
    action = _make_action(persona)
    visitor = _make_visitor("hi")

    await deliver_via_persona(
        action=action,
        visitor=visitor,
        content="Hi!",  # 3 chars
        response_mode="publish",
        degenerate_response_max_chars=25,
    )

    # Raw publish, not respond_slim.
    action.publish.assert_awaited_once()
    persona.respond_slim.assert_not_called()
