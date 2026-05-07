"""Tests for the converse fast-path in ``deliver_via_persona``.

When the cockpit's conversational gate fires it calls
``deliver_via_persona`` with ``mode="respond"``, ``content=None``, and a
short directive. Pre-Phase-5 this routed through ``action.respond()`` →
``PersonaAction.respond()`` (full compose with parameter injection +
directive composition + DB write). The fast path now routes through
``PersonaAction.respond_slim`` with the directive folded into the system
prompt — saving ~100-300ms of CPU + ~95% of the system prompt size.

These tests verify:

- Directive without content → respond_slim with extra_system=directive
- Content present → falls back to legacy ``action.respond()`` path
  (so engine final-response delivery is unaffected)
- No directive, no content → still falls back to ``action.respond()``
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.cockpit.delivery.persona_delivery import deliver_via_persona

pytestmark = pytest.mark.asyncio


def _make_visitor():
    visitor = MagicMock()
    visitor.interaction = MagicMock()
    visitor.interaction.id = "int_1"
    visitor.add_directive = AsyncMock()
    visitor.conversation = MagicMock()
    visitor.conversation.get_interaction_history = AsyncMock(return_value=[])
    return visitor


def _make_action(persona):
    action = MagicMock()
    action.get_action = AsyncMock(return_value=persona)
    action.respond = AsyncMock()
    action.publish = AsyncMock()
    return action


def _make_persona():
    persona = MagicMock()
    persona.enabled = True
    persona.respond_slim = AsyncMock(return_value="hello back")
    return persona


async def test_converse_fast_path_calls_respond_slim_with_directive() -> None:
    """Directive + no content → respond_slim path with extra_system=directive."""
    persona = _make_persona()
    action = _make_action(persona)
    visitor = _make_visitor()

    await deliver_via_persona(
        action,
        visitor,
        content=None,
        response_mode="respond",
        directive="Reply briefly in character.",
        history_limit=2,
        use_history=True,
    )

    # respond_slim called once, action.respond() never reached.
    persona.respond_slim.assert_awaited_once()
    action.respond.assert_not_called()
    # Directive is passed via extra_system kwarg (not as a directive on the interaction).
    kwargs = persona.respond_slim.await_args.kwargs
    assert kwargs.get("extra_system") == "Reply briefly in character."
    # No directive added to the interaction — the legacy path would have done that.
    visitor.add_directive.assert_not_called()


async def test_converse_fast_path_loads_history_when_use_history_true() -> None:
    persona = _make_persona()
    action = _make_action(persona)
    visitor = _make_visitor()
    visitor.conversation.get_interaction_history = AsyncMock(
        return_value=[
            {"role": "user", "content": "hi earlier"},
            {"role": "assistant", "content": "hey"},
        ]
    )

    await deliver_via_persona(
        action,
        visitor,
        content=None,
        response_mode="respond",
        directive="Reply briefly.",
        history_limit=4,
        use_history=True,
    )

    persona.respond_slim.assert_awaited_once()
    history = persona.respond_slim.await_args.kwargs.get("history")
    assert isinstance(history, list)
    assert len(history) == 2
    # Pair preserved as (role, content).
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "hi earlier"


async def test_converse_fast_path_skipped_when_persona_unavailable() -> None:
    """No persona → legacy ``action.respond()`` still runs."""
    action = _make_action(persona=None)
    visitor = _make_visitor()

    await deliver_via_persona(
        action,
        visitor,
        content=None,
        response_mode="respond",
        directive="Reply briefly.",
    )

    action.respond.assert_awaited_once()
    visitor.add_directive.assert_awaited_once()


async def test_publish_mode_with_content_uses_respond_slim_existing_path() -> None:
    """Content + publish mode → respond_slim called with prompt=content (legacy slim path)."""
    persona = _make_persona()
    action = _make_action(persona)
    visitor = _make_visitor()

    await deliver_via_persona(
        action,
        visitor,
        content="The answer is 42 because of the long calculation.",
        response_mode="publish",
    )

    persona.respond_slim.assert_awaited_once()
    kwargs = persona.respond_slim.await_args.kwargs
    assert kwargs.get("prompt") == "The answer is 42 because of the long calculation."
    # The publish path does NOT pass extra_system (only the converse path does).
    assert kwargs.get("extra_system") is None


async def test_respond_mode_with_content_uses_legacy_action_respond() -> None:
    """Content + respond mode → legacy ``action.respond()`` so accumulated
    directives on the interaction still drive the persona compose."""
    persona = _make_persona()
    action = _make_action(persona)
    visitor = _make_visitor()

    await deliver_via_persona(
        action,
        visitor,
        content=(
            "Here is the engine's final answer with enough characters to "
            "stay above the degenerate threshold."
        ),
        response_mode="respond",
    )

    action.respond.assert_awaited_once()
    persona.respond_slim.assert_not_called()
    # ``Tell the user: ...`` directive was added so PersonaAction.respond()
    # has something to compose.
    visitor.add_directive.assert_awaited_once()


async def test_respond_mode_without_directive_or_content_falls_through() -> None:
    """No directive + no content → legacy ``action.respond()`` (drives from
    interaction's accumulated directives — IA-only finalize path)."""
    persona = _make_persona()
    action = _make_action(persona)
    visitor = _make_visitor()

    await deliver_via_persona(
        action,
        visitor,
        content=None,
        response_mode="respond",
        directive=None,
    )

    action.respond.assert_awaited_once()
    persona.respond_slim.assert_not_called()
