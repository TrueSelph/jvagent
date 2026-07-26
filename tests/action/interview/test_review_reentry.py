"""interview__review must not re-present after the user has replied.

A user who answered "Looks good" at the confirmation step was shown a reply
consisting of the single word "Confirm". The model had re-called
interview__review (which the confirmation directive explicitly forbids), been
handed the same "reply 'Confirm' or 'Yes' to continue" prompt, and compressed it
to the bare control token — a word meant for the USER to say.
"""

from __future__ import annotations

import json

from jvagent.action.interview.engine import (
    REVIEW_PRESENTED_MARKER_KEY,
    handle_review,
)


class _Spec:
    confirm = "manual"
    name = "signup"

    class handlers:
        review = None
        complete = None


class _Session:
    def __init__(self, marker):
        from jvagent.action.interview.session import InterviewStatus

        self.status = InterviewStatus.REVIEW
        self.context = {REVIEW_PRESENTED_MARKER_KEY: marker}
        self.skipped_fields = set()

    def get_collected_summary(self):
        return {"user_name": "Eldon Marks"}


def _visitor(interaction_id):
    class I:
        id = interaction_id

    class V:
        interaction = I()

    return V()


class _Action:
    def __init__(self, session):
        self._session = session

    async def _get_session_and_contract(self, visitor):
        return self._session, _Spec()

    def _load_fn(self, spec):
        return None

    async def _save_session(self, session, visitor):
        return None


async def test_review_refuses_to_re_present_on_a_later_turn(monkeypatch):
    """The user has already seen the summary and replied; the tool must hand the
    model the next step, not the same prompt."""
    import jvagent.action.interview.engine as engine

    async def _keys(*a, **k):
        return ["user_name"]

    monkeypatch.setattr(engine, "compute_review_field_keys", _keys)

    session = _Session(marker="interaction-1")
    import jvagent.action.interview.engine as eng

    session.context[eng.REVIEW_PRESENTED_FIELDS_KEY] = eng._fields_fingerprint(
        session.get_collected_summary()
    )
    out = await handle_review(_Action(session), _visitor("interaction-2"))
    payload = json.loads(out)

    assert payload["already_presented"] is True
    directive = payload["response_directive"]
    assert "interview__complete" in directive
    assert "do not repeat the confirmation prompt" in directive.lower()
    # It must not carry a user-facing relay ("Tell the user…"), or the loop would
    # deliver this guidance to the visitor verbatim.
    assert not directive.lower().startswith("tell the user")
    # And it must name the failure it exists to prevent.
    assert "bare 'Confirm'" in directive


async def test_same_turn_review_still_presents_the_summary(monkeypatch):
    """First presentation (same interaction) is untouched."""
    import jvagent.action.interview.engine as engine

    async def _keys(*a, **k):
        return ["user_name"]

    monkeypatch.setattr(engine, "compute_review_field_keys", _keys)
    monkeypatch.setattr(engine, "build_review_summary", lambda *a, **k: "SUMMARY")

    session = _Session(marker="interaction-1")
    out = await handle_review(_Action(session), _visitor("interaction-1"))
    payload = json.loads(out)

    assert payload.get("already_presented") is None
    assert "SUMMARY" in payload["response_directive"]


async def test_a_correction_re_presents_the_updated_summary(monkeypatch):
    """Re-entry is only a repeat when nothing changed. After the user corrects a
    field they must see the UPDATED summary, not be told to stop re-presenting."""
    import jvagent.action.interview.engine as engine

    async def _keys(*a, **k):
        return ["user_name"]

    monkeypatch.setattr(engine, "compute_review_field_keys", _keys)
    monkeypatch.setattr(engine, "build_review_summary", lambda *a, **k: "UPDATED")

    session = _Session(marker="interaction-1")
    # Presented with the OLD values...
    session.context[engine.REVIEW_PRESENTED_FIELDS_KEY] = engine._fields_fingerprint(
        {"user_name": "Typo"}
    )
    # ...and the session now holds a corrected value.
    out = await handle_review(_Action(session), _visitor("interaction-2"))
    payload = json.loads(out)

    assert payload.get("already_presented") is None
    assert "UPDATED" in payload["response_directive"]


async def test_legacy_session_without_a_fingerprint_is_still_guarded(monkeypatch):
    """A session whose review was presented by a build predating the fingerprint
    has no stored value. Reading that absence as "changed" disabled the guard for
    every in-flight interview — which is how the bare "Confirm" reached a user a
    second time, after the first fix was already live."""
    import jvagent.action.interview.engine as engine

    async def _keys(*a, **k):
        return ["user_name"]

    monkeypatch.setattr(engine, "compute_review_field_keys", _keys)
    monkeypatch.setattr(engine, "build_review_summary", lambda *a, **k: "SUMMARY")

    session = _Session(marker="interaction-1")
    session.context.pop(engine.REVIEW_PRESENTED_FIELDS_KEY, None)  # legacy stamp
    out = await handle_review(_Action(session), _visitor("interaction-2"))
    payload = json.loads(out)

    assert payload["already_presented"] is True
    assert "interview__complete" in payload["response_directive"]
