"""HandoffInteractAction: contact info is configurable, not hardcoded.

Pre-fix: ``DIRECT_CONTACT_PROMPT`` contained the literal strings
``support@company.com`` and ``+592 XXX XXXX``, and ``handoff_number``
defaulted to a real-looking Guyana phone number.
AUDIT-actions Wave D.
"""

from jvagent.action.handoff_interact_action.handoff_interact_action import (
    DIRECT_CONTACT_PROMPT,
    HandoffInteractAction,
)


def test_direct_contact_prompt_uses_placeholders_not_literals():
    assert "support@company.com" not in DIRECT_CONTACT_PROMPT
    assert "+592" not in DIRECT_CONTACT_PROMPT
    assert "{handoff_email}" in DIRECT_CONTACT_PROMPT
    assert "{handoff_phone}" in DIRECT_CONTACT_PROMPT
    assert "{handoff_hours}" in DIRECT_CONTACT_PROMPT


def test_handoff_number_default_is_empty():
    action = HandoffInteractAction()
    assert action.handoff_number == ""
    assert action.handoff_email == ""


def test_handoff_hours_default_is_generic():
    action = HandoffInteractAction()
    assert "9:00 AM" in action.handoff_hours
