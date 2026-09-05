"""What reaches the model, asserted on a graph loaded from the database.

Every test here corresponds to something that actually went wrong and that the
in-memory unit tests could not see. They assert on captured wire text, never on
a helper's return value — a helper can be right while the wiring that calls it
is not, which is precisely how the grounding guard shipped broken with a green
suite.
"""

from __future__ import annotations

import pytest

from jvagent.action.orchestrator.prompts import (
    LENGTH_LIMIT_PROMPT,
    MEMORY_PROMPT,
    SAFEGUARDS_REMINDER,
    TOOL_USE_POLICY,
)

pytestmark = pytest.mark.asyncio


# --- persisted-attribute contract -----------------------------------------


async def test_attributes_removed_by_adr_0037_are_gone_from_the_persisted_node(wire):
    """A removed attribute must not survive on a node read back from the DB.

    The inverse — a stale persisted value beating a new code default — is why
    `--update --source` exists, and why asserting on a constructed object proves
    nothing about what serves traffic.
    """
    ex = wire.orchestrator
    for gone in (
        "memory_prompt",
        "tool_use_policy_prompt",
        "length_limit_prompt",
        "enforce_grounded_claims",
        "enforce_grounded_specifics",
    ):
        assert not hasattr(ex, gone), f"{gone} is still on the persisted node"


async def test_the_reminder_template_survived_persistence_with_its_slot(wire):
    """Without the `{reminders}` slot the user-turn rules cannot render at all,
    and the failure is silent — the prompt still looks reasonable."""
    assert "{reminders}" in (wire.orchestrator.safeguards_reminder or "")


# --- inline placement reaches the wire ------------------------------------


async def test_inline_rules_render_into_the_system_prompt(wire):
    cap = await wire.capture(
        "what can you do?", block_raw_tool_invocation=True, max_statement_length=600
    )
    assert TOOL_USE_POLICY in cap.system
    assert MEMORY_PROMPT in cap.system
    assert LENGTH_LIMIT_PROMPT.format(max_chars=600) in cap.system


async def test_inline_rules_are_not_double_rendered(wire):
    """`placement: inline` exists so a rule keeps its measured position AND
    stays out of the generic OPERATING RULES bullet list. Rendering it twice
    would be invisible in a unit test and cost tokens on every tick."""
    cap = await wire.capture(
        "what can you do?", block_raw_tool_invocation=True, max_statement_length=600
    )
    assert cap.system.count(TOOL_USE_POLICY) == 1
    assert cap.system.count(MEMORY_PROMPT) == 1


async def test_a_gated_rule_is_absent_when_its_gate_is_off(wire):
    """`tools.selection` is gated by a flag that also gates real code. Proving
    the gate means capturing both states, not assuming one."""
    off = await wire.capture("hi", block_raw_tool_invocation=False)
    on = await wire.capture("hi", block_raw_tool_invocation=True)
    assert TOOL_USE_POLICY not in off.system
    assert TOOL_USE_POLICY in on.system


# --- user-turn placement ---------------------------------------------------


async def test_the_user_turn_reminder_is_the_measured_string(wire):
    """The ~88% injection-resistance figure was measured on this exact wording
    under the JSON protocol. If the rendered text drifts, the number stops
    describing what ships."""
    cap = await wire.capture("hello there", tool_protocol="json")
    assert SAFEGUARDS_REMINDER in cap.user


async def test_the_native_reminder_keeps_the_behavioural_half(wire):
    """Under the native protocol (ADR-0044) the same behavioural reminder rides
    the user turn; only the JSON mechanics ("Return raw JSON only …") are gone,
    since the provider's tool-calling API carries the decision."""
    cap = await wire.capture("hello there", tool_protocol="native")
    behavioural = SAFEGUARDS_REMINDER.split(" Return raw JSON only")[0]
    assert behavioural in cap.user
    assert "Return raw JSON only" not in cap.user
    assert "Steps taken this turn" not in cap.user


async def test_no_unfilled_template_slot_reaches_the_model(wire):
    """A literal `{reminders}` on the wire means the render silently failed."""
    cap = await wire.capture("hello there")
    assert "{reminders}" not in cap.whole
    assert "{parameters_section}" not in cap.whole
    assert "{tools_section}" not in cap.whole


# --- the ADR-0037 central claim, end to end -------------------------------


async def test_deleting_a_rule_removes_its_text_from_the_wire(wire):
    """ "Delete the parameter and its prompt text goes with it" is the whole
    promise of the parameter surface. Tested here against the real prompt
    rather than against `parameter_text()`, because the helper being right is
    not the same as the render site calling it."""
    from jvagent.action.parameters import orchestrator_core_parameters

    kept = await wire.capture("hi", block_raw_tool_invocation=True)
    assert MEMORY_PROMPT in kept.system

    without = [
        p
        for p in orchestrator_core_parameters()
        if p.get("key") != "memory.search_first"
    ]
    dropped = await wire.capture(
        "hi", parameters=without, block_raw_tool_invocation=True
    )
    assert MEMORY_PROMPT not in dropped.system
    # and the rest of the prompt is otherwise intact
    assert TOOL_USE_POLICY in dropped.system


async def test_an_operator_override_replaces_the_core_text_on_the_wire(wire):
    """Overriding by key from agent.yaml is the documented way to change a rule
    now that the attributes are gone. If that does not reach the prompt, the
    migration note in configuration-keys.md is a lie."""
    from jvagent.action.parameters import orchestrator_core_parameters

    override = [
        p
        for p in orchestrator_core_parameters()
        if p.get("key") != "memory.search_first"
    ] + [
        {
            "key": "memory.search_first",
            "scope": "orchestration",
            "placement": "inline",
            "tier": "agent",
            "response": "REMEMBER THINGS PLEASE.",
        }
    ]
    cap = await wire.capture("hi", parameters=override, block_raw_tool_invocation=True)
    assert "REMEMBER THINGS PLEASE." in cap.system
    assert MEMORY_PROMPT not in cap.system
