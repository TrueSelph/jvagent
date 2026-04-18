"""Unit checks for canned lead-in prompt templates."""

from jvagent.action.persona.prompts import (
    CANNED_LEAD_IN_CONTEXT_PROMPT,
    SYSTEM_PROMPT_TEMPLATE,
)


def test_canned_lead_in_context_prompt_includes_text():
    out = CANNED_LEAD_IN_CONTEXT_PROMPT.format(canned_text="One sec—looking that up.")
    assert "IMMEDIATE MESSAGE ALREADY SENT" in out
    assert "One sec—looking that up." in out
    assert "Natural Transition" in out


def test_system_prompt_template_has_canned_placeholder():
    assert "{canned_lead_in_section}" in SYSTEM_PROMPT_TEMPLATE
