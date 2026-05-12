"""Tests for ``deliver_via_persona``'s mode/content decision matrix.

Decision matrix (see ``persona_delivery.py`` for the canonical version):

- ``response_mode="respond"`` → ``visitor.add_directive(...)`` (either the
  caller-supplied directive or a synthesized ``Tell the user: ...``) then
  ``action.respond(visitor, ...)``. ``persona.respond_slim`` is NOT used —
  the full ``PersonaAction.respond()`` compose path is required so any
  directives accumulated on the interaction are honored.
- ``response_mode="publish"`` + non-degenerate ``content`` →
  ``persona.respond_slim(prompt=user_utterance, extra_system=<delivery
  instruction wrapping the content>, history=[])``. The content is folded
  into ``extra_system`` so the persona reshapes it for natural delivery
  instead of treating it as the user's request.
- ``response_mode="respond"`` + neither directive nor content →
  ``action.respond()`` drives from interaction-accumulated directives only.
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


async def test_respond_mode_with_directive_adds_directive_and_calls_respond() -> None:
    """Directive + no content → ``add_directive(directive)`` then ``action.respond()``.

    ``respond_slim`` is not used: respond mode requires the full
    PersonaAction.respond() compose so directives accumulated on the
    interaction (this one plus any prior ones) all drive the final reply.
    """
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

    visitor.add_directive.assert_awaited_once_with("Reply briefly in character.")
    action.respond.assert_awaited_once()
    persona.respond_slim.assert_not_called()
    # ``action.respond()`` is invoked with the caller's history flags.
    kwargs = action.respond.await_args.kwargs
    assert kwargs.get("use_history") is True
    assert kwargs.get("history_limit") == 2


async def test_respond_mode_forwards_history_flags_to_action_respond() -> None:
    """``use_history`` + ``history_limit`` flow through to ``action.respond()``.

    ``persona.respond_slim`` is not part of the respond-mode path, so
    history is loaded by ``PersonaAction.respond()`` itself rather than
    by this helper.
    """
    persona = _make_persona()
    action = _make_action(persona)
    visitor = _make_visitor()

    await deliver_via_persona(
        action,
        visitor,
        content=None,
        response_mode="respond",
        directive="Reply briefly.",
        history_limit=4,
        use_history=True,
    )

    action.respond.assert_awaited_once()
    kwargs = action.respond.await_args.kwargs
    assert kwargs.get("use_history") is True
    assert kwargs.get("history_limit") == 4
    persona.respond_slim.assert_not_called()


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


async def test_publish_mode_with_content_uses_respond_slim_delivery_path() -> None:
    """Content + publish mode → ``respond_slim`` with content folded into ``extra_system``.

    The user's utterance becomes ``prompt`` (so the model still sees what
    they asked); the engine-produced ``content`` is wrapped in a delivery
    instruction and passed via ``extra_system`` so the persona reshapes it
    naturally instead of treating it as a fresh user message.
    """
    persona = _make_persona()
    action = _make_action(persona)
    visitor = _make_visitor()
    visitor.interaction.utterance = "Why is 6 times 7 special?"

    content = "The answer is 42 because of the long calculation."
    await deliver_via_persona(
        action,
        visitor,
        content=content,
        response_mode="publish",
    )

    persona.respond_slim.assert_awaited_once()
    kwargs = persona.respond_slim.await_args.kwargs
    # ``prompt`` is the user's utterance, not the engine's content.
    assert kwargs.get("prompt") == "Why is 6 times 7 special?"
    # ``extra_system`` carries the delivery instruction with the content appended.
    extra_system = kwargs.get("extra_system")
    assert isinstance(extra_system, str)
    assert content in extra_system
    assert "Deliver it naturally in your voice" in extra_system
    # No directive added — publish path does not route through ``add_directive``.
    visitor.add_directive.assert_not_called()


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
