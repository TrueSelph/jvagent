"""Tests for the engine system prompt's invitation-closer rule (Wave 9j.1).

The engine prompt forbids generic options-menu closers in the model's
``final_response`` text. These tests assert the prompt continues to
carry the explicit prohibitions so a future edit doesn't silently
relax the rule.

The model's compliance is not unit-testable; an eval rubric would
cover that. These tests pin the *prompt contract* — what we tell the
model — not the model's behavior.
"""

from __future__ import annotations

from jvagent.action.helm.reasoning.prompts import ENGINE_SYSTEM_PROMPT


def test_prompt_carries_no_invitation_closers_header():
    """The hard-rule section header survives any future edit."""
    assert "# No invitation closers (hard rule)" in ENGINE_SYSTEM_PROMPT


def test_prompt_forbids_goodbye_style_closers():
    """Wave 9j.1 preserves the original goodbye-closer prohibitions."""
    flat = " ".join(ENGINE_SYSTEM_PROMPT.split())
    for phrase in (
        "let me",
        "feel free to ask",
        "anything else I can help with?",
        "happy to help further",
    ):
        assert phrase in flat, f"missing goodbye-style prohibition: {phrase!r}"


def test_prompt_forbids_generic_options_menu_closers():
    """Wave 9j.1 added a second hard-rule clause covering options-menu closers."""
    # Normalize whitespace so line wraps in the prompt don't break the match.
    flat = " ".join(ENGINE_SYSTEM_PROMPT.split())
    for phrase in (
        "Do NOT append generic options-menu closers",
        "Want X or Y?",
        "Would you like specs or a comparison?",
        "Want more details or a recommendation?",
        "Should I look up X?",
        "menu of next-step options",
    ):
        assert phrase in flat, f"missing options-menu prohibition: {phrase!r}"


def test_prompt_requires_content_specific_forward_questions():
    """Forward questions must reference data from THIS turn's response."""
    flat = " ".join(ENGINE_SYSTEM_PROMPT.split())
    for phrase in (
        "names specific data from",
        "paste the question into a different conversation",
        "If it still fits unchanged, it is a template",
    ):
        assert phrase in flat, f"missing content-specificity test: {phrase!r}"


def test_prompt_requires_closer_shape_variety():
    """Consecutive turns must not share closer shape."""
    flat = " ".join(ENGINE_SYSTEM_PROMPT.split())
    for phrase in (
        "Vary closing shape across turns",
        "Do NOT end consecutive turns with",
        "end on the answer with no",
    ):
        assert phrase in flat, f"missing variety rule: {phrase!r}"


def test_prompt_subordinates_skills_to_engine_hard_rules():
    """Wave 9j.2: skill SOPs cannot countermand engine hard rules."""
    flat = " ".join(ENGINE_SYSTEM_PROMPT.split())
    for phrase in (
        "# Rule precedence",
        "Skill SOPs",
        "CANNOT override the engine hard rules",
        'When a skill instruction says "ask a follow-up"',
        "paste-into-another-conversation test",
        "Skill instructions to add a closing line are PERMISSIVE, not mandatory",
    ):
        assert phrase in flat, f"missing precedence rule clause: {phrase!r}"
