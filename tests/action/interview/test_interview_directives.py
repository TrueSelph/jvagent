"""Processors surface user CONTENT via the injected `directives` sink.

A pre/post processor's bare ``response_directive`` carries one question; content
the user must SEE (option lists, tables, summaries) goes through the
``InterviewDirectives`` sink, which queues onto interaction.directives so
ReplyAction composes it into the reply. Putting such content in a ``tell_user``
note loses it — the note is model-only guidance and egress strips it.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview.directives import ACTIVATION_PHASE, InterviewDirectives
from jvagent.action.interview.hooks import HookExecutionContext, call_hook
from jvagent.action.interview.responses import tell_user


def test_tell_user_frames_and_queues_on_interaction():
    interaction = MagicMock()
    interaction.add_directive = MagicMock(return_value=True)

    sink = InterviewDirectives(interaction)
    assert sink.available is True
    ok = sink.tell_user("Available slots:\n- Mon 9am\n- Sat 10am")

    assert ok is True
    interaction.add_directive.assert_called_once()
    framed, author = interaction.add_directive.call_args.args
    assert framed.startswith("Tell the user:")
    assert "Sat 10am" in framed  # content preserved (not a stripped note)
    assert author == "InterviewAction"


def test_tell_user_does_not_double_frame():
    interaction = MagicMock()
    interaction.add_directive = MagicMock(return_value=True)
    InterviewDirectives(interaction).tell_user("Tell the user: pick one")
    framed = interaction.add_directive.call_args.args[0]
    assert framed == "Tell the user: pick one"


def test_add_passes_directive_verbatim_with_custom_source():
    interaction = MagicMock()
    interaction.add_directive = MagicMock(return_value=True)
    InterviewDirectives(interaction).add("Call interview__review.", source="SomeAction")
    assert interaction.add_directive.call_args.args == (
        "Call interview__review.",
        "SomeAction",
    )


def test_active_only_on_activation_phase():
    """A directive belongs to the interaction of the turn that ACTIVATES its field.
    A pre_processor also fires while storing the answer (and validators / branch /
    post fire while advancing) — emitting then would land the directive on a
    different field's interaction. So the sink is active ONLY on the activation
    run and inert on every other run."""
    interaction = MagicMock()
    interaction.add_directive = MagicMock(return_value=True)

    assert InterviewDirectives(interaction, phase=ACTIVATION_PHASE).available is True

    for phase in ("store", "validate", "branch", "post", "advance", "review"):
        sink = InterviewDirectives(interaction, phase=phase)
        assert sink.available is False, phase
        assert sink.tell_user("Here are the slots:\n- Mon 9am") is False, phase
    interaction.add_directive.assert_not_called()


@pytest.mark.asyncio
async def test_call_hook_sink_inert_off_activation_run():
    """call_hook defaults to the inert (advance) phase, so a pre_processor that
    re-runs while storing the answer cannot re-queue its content. Only the explicit
    activation dispatch (run_pre_processors) lets the sink queue."""
    interaction = MagicMock()
    interaction.add_directive = MagicMock(return_value=True)
    visitor = SimpleNamespace(interaction=interaction)

    async def processor(directives, **kwargs):
        directives.tell_user("Here are the slots:\n- Mon 9am")
        return {"ok": True}

    # Default dispatch (store / validate / branch / post / handlers): inert.
    await call_hook(processor, visitor=visitor)
    interaction.add_directive.assert_not_called()

    # Activation dispatch: queues.
    await call_hook(processor, visitor=visitor, phase=ACTIVATION_PHASE)
    interaction.add_directive.assert_called_once()


def test_noop_without_interaction_or_empty_content():
    assert InterviewDirectives(None).available is False
    assert InterviewDirectives(None).tell_user("x") is False
    interaction = MagicMock()
    interaction.add_directive = MagicMock(return_value=True)
    # Empty / whitespace content never reaches the interaction.
    assert InterviewDirectives(interaction).tell_user("   ") is False
    interaction.add_directive.assert_not_called()


@pytest.mark.asyncio
async def test_call_hook_injects_directives_bound_to_interaction():
    """A processor that declares a `directives` param receives a sink wired to
    visitor.interaction — the standard, first-class queue path (no reaching into
    visitor internals from the processor)."""
    interaction = MagicMock()
    interaction.add_directive = MagicMock(return_value=True)
    visitor = SimpleNamespace(interaction=interaction)

    captured = {}

    async def processor(directives, **kwargs):
        captured["sink"] = directives
        directives.tell_user("Here are the slots:\n- Mon 9am")
        return {"ok": True}

    result = await call_hook(processor, visitor=visitor, phase=ACTIVATION_PHASE)

    assert result == {"ok": True}
    assert isinstance(captured["sink"], InterviewDirectives)
    framed = interaction.add_directive.call_args.args[0]
    assert "Mon 9am" in framed


@pytest.mark.asyncio
async def test_ctx_is_the_common_interface_always_injected():
    """A hook may declare a single `ctx` param — always injected, never None (no
    null-guard), exposing inputs and the directives output."""
    interaction = MagicMock()
    interaction.add_directive = MagicMock(return_value=True)
    visitor = SimpleNamespace(interaction=interaction)

    captured = {}

    async def processor(ctx):  # NB: no default, no null check
        captured["ctx"] = ctx
        ctx.tell_user("Here are the slots:\n- Mon 9am")
        return {"ok": True}

    result = await call_hook(processor, visitor=visitor, phase=ACTIVATION_PHASE)

    assert result == {"ok": True}
    ctx = captured["ctx"]
    assert isinstance(ctx, HookExecutionContext)
    assert ctx.visitor is visitor
    assert ctx.directives.available is True
    framed = interaction.add_directive.call_args.args[0]
    assert "Mon 9am" in framed


@pytest.mark.asyncio
async def test_ctx_exposes_validator_input_value():
    """Validators share the same `ctx` interface; the raw value is ctx.value."""
    captured = {}

    async def validator(ctx):
        captured["value"] = ctx.value
        return {"valid": True, "value": ctx.value}

    await call_hook(validator, value="Monday 9am")
    assert captured["value"] == "Monday 9am"


@pytest.mark.asyncio
async def test_ctx_tell_user_inert_off_activation_run():
    """ctx.tell_user defaults inert (advance phase), so a hook that re-runs while
    storing the answer cannot re-queue content — same guarantee as the bare sink."""
    interaction = MagicMock()
    interaction.add_directive = MagicMock(return_value=True)
    visitor = SimpleNamespace(interaction=interaction)

    async def processor(ctx):
        ctx.tell_user("Here are the slots:\n- Mon 9am")
        return {"ok": True}

    await call_hook(processor, visitor=visitor)  # default ADVANCE_PHASE
    interaction.add_directive.assert_not_called()


def test_content_in_directive_survives_egress_strip():
    """The regression: content placed in a tell_user note is stripped at egress
    (everything after the U+2063 marker). A queued directive carries it as plain
    user-facing text with no marker, so nothing is stripped."""
    note_directive = tell_user("Pick a slot:", note="- Mon 9am\n- Sat 10am")
    assert "Sat 10am" not in note_directive.split("⁣", 1)[0]

    interaction = MagicMock()
    interaction.add_directive = MagicMock(return_value=True)
    InterviewDirectives(interaction).tell_user("- Mon 9am\n- Sat 10am")
    framed = interaction.add_directive.call_args.args[0]
    assert "⁣" not in framed  # no marker → nothing stripped
    assert "Sat 10am" in framed
