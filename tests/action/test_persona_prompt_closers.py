"""Tests for the PersonaAction prompt closer-strip rule (Wave 9j.4).

PersonaAction.respond() composes its system prompt from
RESPONSE_PROTOCOL_PROMPT plus persona_description, directives, etc.
The respond_slim() path uses extra_system (covered by Wave 9j.3
delivery-instruction tests); respond() uses RESPONSE_PROTOCOL_PROMPT.

These tests pin the closer-strip rule shape in the protocol prompt
so a future edit doesn't silently relax it. They mirror the
engine-prompt closer tests at
tests/action/helm/reasoning/test_engine_prompt_closers.py — the two
layers must agree on the prohibited patterns.
"""

from __future__ import annotations

from jvagent.action.persona.prompts import RESPONSE_PROTOCOL_PROMPT


def test_protocol_carries_no_invitation_closers_section():
    """Wave 9j.4 added a hard-rule section to RESPONSE_PROTOCOL_PROMPT."""
    assert "### NO INVITATION CLOSERS" in RESPONSE_PROTOCOL_PROMPT


def test_protocol_lists_goodbye_style_patterns_to_strip():
    """The goodbye-style closer prohibitions match the engine prompt."""
    flat = " ".join(RESPONSE_PROTOCOL_PROMPT.split())
    for phrase in (
        "Let me know if",
        "Feel free to ask",
        "Anything else I can help with?",
        "Happy to help further",
        "Just say the word",
    ):
        assert phrase in flat, f"missing goodbye-closer pattern: {phrase!r}"


def test_protocol_lists_options_menu_patterns_to_strip():
    """Generic options-menu closer prohibitions match the engine prompt."""
    flat = " ".join(RESPONSE_PROTOCOL_PROMPT.split())
    for phrase in (
        "Want X or Y?",
        "Would you like X or Y?",
        "Do you want X or Y?",
        "Need X or Y?",
        "Should I look up",
        "Want more details or a comparison?",
    ):
        assert phrase in flat, f"missing options-menu pattern: {phrase!r}"


def test_protocol_requires_paste_test_for_forward_questions():
    """Forward questions must reference specific data from THIS response."""
    flat = " ".join(RESPONSE_PROTOCOL_PROMPT.split())
    for phrase in (
        "paste-into-another-conversation test",
        "naming SPECIFIC data",
        "A forward question that names specific data from THIS response",
    ):
        assert phrase in flat, f"missing paste-test clause: {phrase!r}"


def test_protocol_forbids_substituting_a_new_closer():
    """When a closer is stripped, persona must not substitute a new one."""
    flat = " ".join(RESPONSE_PROTOCOL_PROMPT.split())
    for phrase in (
        "Do NOT substitute a new closer",
        "Silent compliance",
        "end with no closer at all",
    ):
        assert phrase in flat, f"missing no-substitute clause: {phrase!r}"


def test_protocol_directive_step_4_orders_tail_scan():
    """Step 4 of the protocol orders a pre-output tail scan."""
    flat = " ".join(RESPONSE_PROTOCOL_PROMPT.split())
    for phrase in (
        "scan the tail of your response",
        "DROP any invitation closer",
        "even if a directive's drafted text appeared to include one",
    ):
        assert phrase in flat, f"missing step-4 tail-scan clause: {phrase!r}"
