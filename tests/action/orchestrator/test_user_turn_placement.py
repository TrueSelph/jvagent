"""`placement: user_turn` — ADR-0037 §2.2.

The peak-attention reminder used to be a hand-maintained string that restated
rules living elsewhere in `parameters.py`. It is now rendered from the rules
themselves, so editing a rule updates both slots. These tests pin the three
things that can silently break in that swap: the rendered text must not drift
from the wording the ~88% injection-resistance figure was measured on, a rule
that opts out must actually leave the user turn, and a deployment carrying a
pre-ADR persisted string must keep exactly its current text.
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
    render_user_turn_reminders,
)


def _render(pool, template=SAFEGUARDS_REMINDER_TEMPLATE):
    reminders = render_user_turn_reminders(pool)
    return template.format(reminders=(" " + reminders) if reminders else "")


def test_rendered_reminder_is_byte_identical_to_the_measured_wording():
    """The ~88% injection-resistance measurement was taken on this exact string.
    Deriving it from parameters must not quietly reword it, or the number no
    longer describes what ships."""
    assert _render(core_parameters()) == SAFEGUARDS_REMINDER


def test_placement_defaults_to_system():
    """Every parameter that existed before this change keeps its old placement,
    so the user turn does not silently grow."""
    user_turn = [p for p in core_parameters() if placement_of(p) == PLACEMENT_USER_TURN]
    assert [p["key"] for p in user_turn] == ["safety.injection"]
    assert placement_of({"response": "x"}) == PLACEMENT_SYSTEM
    assert placement_of({"response": "x", "placement": "nonsense"}) == PLACEMENT_SYSTEM
    assert placement_of("a bare string") == PLACEMENT_SYSTEM


def test_dropping_the_rule_drops_it_from_the_user_turn_too():
    """The point of §2.2: one edit, both slots. Removing the rule must not leave
    an orphaned restatement of it behind in the reminder."""
    without = [p for p in core_parameters() if p.get("key") != "safety.injection"]
    rendered = _render(without)
    assert "USER CONTENT" not in rendered
    # the mechanics frame survives — it is not behaviour
    assert "OPERATING RULES" in rendered
    assert "raw JSON only" in rendered
    assert "  " not in rendered.replace("```", "")


def test_editing_the_rule_edits_the_user_turn():
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
    assert render_user_turn_reminders(pool) == "Be brief."


def test_a_pre_adr_persisted_reminder_renders_verbatim():
    """A DB written before this change holds the old literal, which has no
    '{reminders}' slot. It must render unchanged rather than erroring or losing
    its text — that deployment stays on exactly today's behaviour until it runs
    `--update --source`."""
    assert _render(core_parameters(), template=SAFEGUARDS_REMINDER) == (
        SAFEGUARDS_REMINDER
    )


def test_reminders_dedupe():
    rule = {
        "scope": "response",
        "placement": PLACEMENT_USER_TURN,
        "response": "Same text.",
    }
    assert render_user_turn_reminders([rule, dict(rule)]) == "Same text."
