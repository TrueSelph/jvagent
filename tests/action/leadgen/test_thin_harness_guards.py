"""Thin harness guard tests for leadgen action."""

from pathlib import Path


def test_leadgen_action_no_intent_classification():
    """LeadGenAction must not embed conversational steering logic."""
    path = (
        Path(__file__).resolve().parents[3]
        / "jvagent"
        / "action"
        / "leadgen"
        / "leadgen_action.py"
    )
    text = path.read_text(encoding="utf-8")
    forbidden = [
        "classify_intent",
        "prep_steering",
        "auto_store",
    ]
    for token in forbidden:
        assert token not in text
