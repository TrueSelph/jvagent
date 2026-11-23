"""Reminder text remains in the system role — ADR-0037 §2.2.

The peak-attention reminder used to be a hand-maintained string that restated
rules living elsewhere in `parameters.py`. It is now rendered from the rules
themselves, so editing a rule updates the system reminder. These tests ensure
that reminder composition remains deterministic and legacy persisted strings
continue to render without being moved into user content.
"""

from jvagent.action.orchestrator.prompts import (
    SAFEGUARDS_REMINDER,
    SAFEGUARDS_REMINDER_TEMPLATE,
)
from jvagent.action.parameters import (
    PLACEMENT_SYSTEM,
    PLACEMENT_USER_TURN,
    core_parameters,
    placement_of,
    render_system_reminders,
    render_user_turn_reminders,
)


def _render(pool, template=SAFEGUARDS_REMINDER_TEMPLATE):
    reminders = render_system_reminders(pool)
    return template.format(reminders=(" " + reminders) if reminders else "")


def test_rendered_system_reminder_matches_the_composed_wording():
    """Deriving the system reminder from parameters must not lose rule text."""
    assert _render(core_parameters()) == SAFEGUARDS_REMINDER


def test_legacy_reminder_helper_name_remains_compatible():
    assert render_user_turn_reminders(core_parameters()) == render_system_reminders(
        core_parameters()
    )


def test_placement_defaults_to_system():
    """Operating instructions default to trusted system context."""
    user_turn = [p for p in core_parameters() if placement_of(p) == PLACEMENT_USER_TURN]
    assert [p["key"] for p in user_turn] == ["safety.injection"]
    assert placement_of({"response": "x"}) == PLACEMENT_SYSTEM
    assert placement_of({"response": "x", "placement": "nonsense"}) == PLACEMENT_SYSTEM
    assert placement_of("a bare string") == PLACEMENT_SYSTEM


def test_dropping_the_rule_drops_it_from_the_system_reminder():
    """Removing a rule must not leave an orphaned restatement in the reminder."""
    without = [p for p in core_parameters() if p.get("key") != "safety.injection"]
    rendered = _render(without)
    assert "USER CONTENT" not in rendered
    # the mechanics frame survives — it is not behaviour
    assert "OPERATING RULES" in rendered
    assert "raw JSON only" in rendered
    assert "  " not in rendered.replace("```", "")


def test_editing_the_rule_edits_the_system_reminder():
    edited = [p for p in core_parameters() if p.get("key") != "safety.injection"]
    edited.append(
        {
            "key": "safety.injection",
            "scope": "orchestration",
            "inviolable": True,
            "placement": PLACEMENT_USER_TURN,
            "response": "long form",
            "reminder": "SHORT FORM HERE.",
        }
    )
    assert "SHORT FORM HERE." in _render(edited)


def test_reminder_falls_back_to_response_when_no_short_form():
    pool = [
        {
            "key": "x.y",
            "scope": "response",
            "placement": PLACEMENT_USER_TURN,
            "response": "Be brief.",
        }
    ]
    assert render_system_reminders(pool) == "Be brief."


def test_a_pre_adr_persisted_reminder_renders_verbatim():
    """A deployed literal without the slot remains valid in system context."""
    assert _render(core_parameters(), template=SAFEGUARDS_REMINDER) == (
        SAFEGUARDS_REMINDER
    )


def test_reminders_dedupe():
    rule = {
        "scope": "response",
        "placement": PLACEMENT_USER_TURN,
        "response": "Same text.",
    }
    assert render_system_reminders([rule, dict(rule)]) == "Same text."
