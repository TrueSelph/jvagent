"""Consult staff rule covers declined contacts, quantities, and empty pending."""

from jvagent.action.handoff_action.handoff_action import _parameters_for


def test_consult_staff_rule_covers_declined_quantity_and_empty():
    text = _parameters_for("consult", staff=True, pending_rows=[])[0]["response"]
    assert "declined or missing contact" in text
    assert "same item and quantity" in text
    assert "no pending question" in text
