"""HandoffAction — skill-gated capability tools (jvagent/handoff_action).

Tools: handoff__contact_details / handoff__staff_lookup / handoff__agent_escalation /
handoff__scheduled_callback / handoff__pending_questions /
handoff__save_answer. Per-mode channel, staff allowlist + random target, staff Q&A
capture into PageIndex (handoff.md, public). Staff targets/write allowlist come from
AccessControlAction HandoffAction.staff only.
"""

import json
from types import SimpleNamespace

from jvagent.action.handoff_action import HandoffAction
from jvagent.action.handoff_action.handoff_action import (
    DIRECT_CONTACT_PROMPT,
    _bold_whatsapp_ask,
    _extract_saved_answer,
    _pending_question_text,
    _render_direct_contact,
    _staff_outbound,
)
from jvagent.tooling.tool_executor import bind_dispatch_context

# Digits must pass _contact_kind (>=6) so ACA members classify as whatsapp.
_STAFF_PHONE = "5921111111"
_STAFF_PHONE_B = "5922222222"


class _Ctx:
    def __init__(self, user_id="", channel="whatsapp", conversation=None):
        self.user_id = user_id
        self.channel = channel
        self.conversation = conversation
        self.session_id = ""


class _Conversation:
    def __init__(self, contact=""):
        self.context = {"handoff_contact": contact} if contact else {}

    async def update_context(self, updates):
        self.context.update(updates)


class _PendingStore:
    """Shared durable pending_questions keyed by action id (simulates Action.get)."""

    by_action: dict = {}
    ACTION_ID = "n.HandoffAction.test"

    @classmethod
    def reset(cls):
        cls.by_action = {}

    @classmethod
    def rows_for(cls, action_id=None):
        return list(cls.by_action.get(action_id or cls.ACTION_ID, []))


def _install_aca_staff(monkeypatch, members=None):
    """Stub AccessControlAction HandoffAction.staff membership."""
    staff = list(members if members is not None else [_STAFF_PHONE])

    class _ACA:
        def policy_applies(self):
            return True

        def get_user_groups(self, action_label=None):
            assert action_label == "HandoffAction"
            return {"staff": list(staff)}

    async def _get_action(self, name, *args, **kwargs):
        assert name == "AccessControlAction"
        return _ACA()

    monkeypatch.setattr(HandoffAction, "get_action", _get_action)
    return staff


def _install_pending_store(monkeypatch, action_id=_PendingStore.ACTION_ID):
    """Stub Action save/get + cache so pending_questions is shared across instances."""
    _PendingStore.reset()
    _PendingStore.ACTION_ID = action_id

    async def _save(self):
        aid = str(getattr(self, "id", "") or action_id).strip() or action_id
        object.__setattr__(self, "id", aid)
        _PendingStore.by_action[aid] = [
            row for row in (self.pending_questions or []) if isinstance(row, dict)
        ]

    async def _get(cls, eid):
        aid = str(eid or "").strip()
        rows = _PendingStore.by_action.get(aid, [])
        fresh = SimpleNamespace(
            id=aid,
            pending_questions=list(rows),
        )
        return fresh

    async def _noop_cache(self):
        return None

    async def _noop_invalidate(self):
        await self._evict_self_cache()

    monkeypatch.setattr(HandoffAction, "save", _save)
    monkeypatch.setattr(HandoffAction, "get", classmethod(_get))
    monkeypatch.setattr(HandoffAction, "_evict_self_cache", _noop_cache)
    monkeypatch.setattr(HandoffAction, "_invalidate_pending_caches", _noop_invalidate)
    return _PendingStore


def _bind_action(action, action_id=_PendingStore.ACTION_ID):
    object.__setattr__(action, "id", action_id)
    action.pending_questions = list(_PendingStore.by_action.get(action_id, []))
    return action


def _seed_pending(action, **kwargs):
    row = {
        "id": kwargs.get("id") or f"q_{len(action.pending_questions or []) + 1}",
        "question": kwargs.get("question", ""),
        "user_channel": kwargs.get("user_channel", "default"),
        "user_contact": kwargs.get("user_contact", ""),
        "created_at": kwargs.get("created_at", ""),
    }
    rows = list(action.pending_questions or [])
    rows.append(row)
    action.pending_questions = rows
    aid = str(getattr(action, "id", "") or _PendingStore.ACTION_ID)
    object.__setattr__(action, "id", aid)
    _PendingStore.by_action[aid] = list(rows)
    return row


def test_direct_contact_prompt_uses_placeholders_not_literals():
    assert "support@company.com" not in DIRECT_CONTACT_PROMPT
    assert "+592" not in DIRECT_CONTACT_PROMPT
    assert "{handoff_email}" in DIRECT_CONTACT_PROMPT
    assert "{handoff_phone}" in DIRECT_CONTACT_PROMPT
    assert "{handoff_hours}" in DIRECT_CONTACT_PROMPT


def test_defaults():
    action = HandoffAction()
    assert "9:00 AM" in action.handoff_hours


def test_render_direct_contact_drops_blank_field_lines():
    rendered = _render_direct_contact(
        DIRECT_CONTACT_PROMPT, email="", phone="", hours="Mon-Fri"
    )
    assert "Email:" not in rendered
    assert "Phone" not in rendered
    assert "Office Hours: Mon-Fri" in rendered


async def test_render_helper_matches_tool(monkeypatch):
    _install_aca_staff(monkeypatch, ["1234567890", "a@b.c", "c@d.e"])
    action = HandoffAction()
    rendered = await action.render_direct_contact()
    assert "a@b.c" in rendered
    assert "c@d.e" in rendered
    assert "1234567890" in rendered


async def test_tools_are_published():
    action = HandoffAction()
    names = {t.name for t in await action.get_tools()}
    assert names == {
        "handoff__contact_details",
        "handoff__update_contact",
        "handoff__staff_lookup",
        "handoff__agent_escalation",
        "handoff__scheduled_callback",
        "handoff__pending_questions",
        "handoff__save_answer",
        "handoff__update_chunk",
    }


async def test_direct_contact_tool_returns_rendered_block(monkeypatch):
    _install_aca_staff(monkeypatch, ["1234567890", "a@b.c"])
    action = HandoffAction()
    tools = {t.name: t for t in await action.get_tools()}
    result = await tools["handoff__contact_details"].call()
    assert "Email: a@b.c" in result.content
    assert "Phone / WhatsApp: 1234567890" in result.content


async def test_update_contact_replaces_saved_reply_address(monkeypatch):
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())

    class _Agent:
        id = "n.Agent.test"

    async def _get_agent(self):
        return _Agent()

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    q1 = _seed_pending(
        action,
        id="q1",
        question="Do you offer delivery?",
        user_channel="default",
        user_contact="592000",
    )
    q2 = _seed_pending(
        action,
        id="q2",
        question="Someone else",
        user_channel="default",
        user_contact="592111",
    )
    conversation = _Conversation("592000")

    tools = {t.name: t for t in await action.get_tools()}
    missing_conversation = await tools["handoff__update_contact"].call(
        phone_number="5926431530"
    )
    assert missing_conversation.is_error
    assert conversation.context["handoff_contact"] == "592000"

    visitor = _Ctx("o.User.abc", channel="default", conversation=conversation)
    with bind_dispatch_context(visitor):
        missing = await tools["handoff__update_contact"].call()
        assert missing.is_error
        assert conversation.context["handoff_contact"] == "592000"
        assert q1["user_contact"] == "592000"

        phone = await tools["handoff__update_contact"].call(phone_number="5926431530")
        assert not phone.is_error
        assert "5926431530" in phone.content
        assert conversation.context["handoff_contact"] == "5926431530"
        assert q1["user_contact"] == "5926431530"
        assert q2["user_contact"] == "592111"

        mailed = await tools["handoff__update_contact"].call(email="customer@x.com")
        assert not mailed.is_error
        assert conversation.context["handoff_contact"] == "customer@x.com"
        assert q1["user_contact"] == "customer@x.com"
        assert q2["user_contact"] == "592111"


async def test_escalation_asks_when_no_contact(monkeypatch):
    _install_aca_staff(monkeypatch)
    action = HandoffAction()
    sent = {}

    async def _send(self, recipient, message):
        sent["to"] = recipient

    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    r = await tools["handoff__agent_escalation"].call(message="x")
    assert not r.is_error
    assert "Ask the user for a phone number or email" in r.content
    assert "handoff__agent_escalation" in r.content
    assert "to" not in sent


async def test_escalation_uses_whatsapp_sender(monkeypatch):
    _install_aca_staff(monkeypatch)
    action = HandoffAction()
    sent = {}

    async def _send(self, recipient, message):
        sent["to"] = recipient
        sent["message"] = message

    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    with bind_dispatch_context(_Ctx("592000")):
        pending = await tools["handoff__agent_escalation"].call(message="need a person")
        assert "Confirm with the user" in pending.content
        assert "592000" in pending.content
        assert "need a person" in pending.content
        assert "to" not in sent
        r = await tools["handoff__agent_escalation"].call(
            message="need a person", confirmed=True
        )
        callback = await tools["handoff__scheduled_callback"].call(
            message="call me later"
        )
    assert not r.is_error
    assert "staff member will reach out" in r.content
    assert "phone number or email" not in r.content
    assert sent["to"] == _STAFF_PHONE
    assert "Contact: 592000" in sent["message"]
    assert "need a person" in sent["message"]
    assert "Confirm with the user" in callback.content
    sent.clear()
    with bind_dispatch_context(_Ctx("592000")):
        confirmed_callback = await tools["handoff__scheduled_callback"].call(
            message="call me later", confirmed=True
        )
    assert "follow up" in confirmed_callback.content
    assert "Contact: 592000" in sent["message"]


async def test_staff_lookup_holds_until_contact_or_decline(monkeypatch):
    _install_aca_staff(monkeypatch)
    action = HandoffAction()
    sent = {"count": 0}
    open_q = {}

    class _Question:
        def __init__(self, contact):
            self.id = "q_open"
            self.user_contact = contact
            self.question = ""

        async def update_user_contact(self, contact):
            self.user_contact = contact

        async def save(self):
            open_q["q"] = self

    async def _send(self, recipient, message):
        sent["count"] += 1
        sent["to"] = recipient
        sent["message"] = message

    async def _record(self, message, contact=""):
        sent["contact"] = contact
        sent["question"] = message
        open_q["q"] = _Question(contact)

    async def _open(self, message=""):
        return open_q.get("q")

    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    monkeypatch.setattr(HandoffAction, "_add_pending", _record)
    monkeypatch.setattr(HandoffAction, "_open_lookup", _open)
    tools = {t.name: t for t in await action.get_tools()}

    r = await tools["handoff__staff_lookup"].call(message="where do you deliver?")
    assert not r.is_error
    assert "phone number or email" in r.content
    assert "contact_declined" in r.content
    assert sent["count"] == 0
    assert "question" not in sent
    assert open_q.get("q") is None

    r = await tools["handoff__staff_lookup"].call(message="where do you deliver?")
    assert sent["count"] == 0

    r = await tools["handoff__staff_lookup"].call(
        message=(
            "The customer asked where we deliver. FAQ search returned nothing. "
            "Customer provided phone number: 592000."
        ),
        phone_numbers=["592000"],
    )
    assert "don't have that information" in r.content
    assert "check with the team" in r.content
    assert "when they respond" in r.content
    assert sent["count"] == 1
    assert open_q["q"].user_contact == "592000"
    assert "592000" not in sent["message"]
    assert "where we deliver" in sent["message"]

    r = await tools["handoff__staff_lookup"].call(
        message="The customer asked where we deliver. FAQ search found nothing useful.",
        phone_numbers=["592000"],
    )
    assert sent["count"] == 1

    open_q.clear()
    sent["count"] = 0
    r = await tools["handoff__staff_lookup"].call(
        message="where do you deliver?", contact_declined=True
    )
    assert "don't have that information" in r.content
    assert "check with the team" in r.content
    assert "when they respond" in r.content
    assert sent["count"] == 1
    assert open_q["q"].user_contact == "declined"

    with bind_dispatch_context(_Ctx("592111")):
        open_q.clear()
        r = await tools["handoff__staff_lookup"].call(message="what are your hours?")
    assert "don't have that information" in r.content
    assert "check with the team" in r.content
    assert "when they respond" in r.content
    assert "phone number or email" not in r.content


async def test_declined_lookup_does_not_reply(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())
    sent = []

    class _Agent:
        id = "n.Agent.test"

    async def _get_agent(self):
        return _Agent()

    async def _send_whatsapp(self, recipient, message):
        sent.append(("whatsapp", recipient))

    async def _send_email(self, recipients, message, subject="Human handoff request"):
        sent.append(("email", recipients))

    async def _append(self, question, answer):
        return question, answer, "n.DocumentNode.test"

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send_whatsapp)
    monkeypatch.setattr(HandoffAction, "_send_email", _send_email)
    monkeypatch.setattr(HandoffAction, "_append_and_ingest", _append)
    tools = {t.name: t for t in await action.get_tools()}
    with bind_dispatch_context(_Ctx("o.User.abc", channel="default")):
        looked = await tools["handoff__staff_lookup"].call(
            message="where do you deliver?", contact_declined=True
        )
    assert "when they respond" in looked.content
    assert sent == [("whatsapp", _STAFF_PHONE)]
    assert len(action.pending_questions) == 1
    row = action.pending_questions[0]
    assert row["user_contact"] == "declined"
    question_id = row["id"]
    sent.clear()
    with bind_dispatch_context(_Ctx(_STAFF_PHONE)):
        saved = await tools["handoff__save_answer"].call(
            question_id=question_id, answer="Yes, we deliver."
        )
    assert not saved.is_error
    assert sent == []
    assert action.pending_questions == []


async def test_staff_lookup_reuses_saved_contact(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())
    conversation = _Conversation("5926431530")
    sent = {"count": 0, "messages": []}

    class _Agent:
        id = "n.Agent.test"

    async def _get_agent(self):
        return _Agent()

    async def _send(self, recipient, message):
        sent["count"] += 1
        sent["messages"].append(message)

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    visitor = _Ctx("o.User.abc", channel="default", conversation=conversation)
    with bind_dispatch_context(visitor):
        reused = await tools["handoff__staff_lookup"].call(
            message="Do you sell car parts"
        )
        again = await tools["handoff__staff_lookup"].call(
            message="Do you offer delivery"
        )
        repeat = await tools["handoff__staff_lookup"].call(
            message="Do you offer delivery"
        )
    assert "phone number or email" not in reused.content
    assert "when they respond" in reused.content
    assert "when they respond" in again.content
    assert sent["count"] == 2
    assert all("5926431530" not in message for message in sent["messages"])
    assert [row["question"] for row in action.pending_questions] == [
        "Do you sell car parts",
        "Do you offer delivery",
    ]
    assert {row["user_contact"] for row in action.pending_questions} == {"5926431530"}
    assert "when they respond" in repeat.content
    assert sent["count"] == 2


async def test_escalation_confirms_updated_contact(monkeypatch):
    _install_aca_staff(monkeypatch)
    action = HandoffAction()
    conversation = _Conversation("5926431530")
    sent = {}

    async def _send(self, recipient, message):
        sent["to"] = recipient
        sent["message"] = message

    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    visitor = _Ctx("o.User.abc", channel="default", conversation=conversation)
    with bind_dispatch_context(visitor):
        updated = await tools["handoff__agent_escalation"].call(
            message="need a person",
            phone_numbers=["592999"],
            confirmed=True,
        )
        assert "Confirm with the user" in updated.content
        assert "592999" in updated.content
        assert "need a person" in updated.content
        assert "to" not in sent
        assert conversation.context["handoff_contact"] == "592999"
        sent_call = await tools["handoff__agent_escalation"].call(
            message="need a person", confirmed=True
        )
    assert "staff member will reach out" in sent_call.content
    assert sent["to"] == _STAFF_PHONE
    assert "Contact: 592999" in sent["message"]


def test_channel_for_defaults_and_override():
    action = HandoffAction()
    assert action._channel_for("staff_lookup") == "whatsapp"
    action.handoff_channels = {"staff_lookup": "email"}
    assert action._channel_for("staff_lookup") == "email"
    action.handoff_channels = {"staff_lookup": "nonsense"}
    assert action._channel_for("staff_lookup") == "whatsapp"


async def test_staff_targets_and_allowlist(monkeypatch):
    _install_aca_staff(
        monkeypatch, [_STAFF_PHONE, _STAFF_PHONE_B, "a@x.com", "b@x.com"]
    )
    action = HandoffAction()
    assert await action._staff_targets("whatsapp") == [_STAFF_PHONE, _STAFF_PHONE_B]
    assert await action._staff_targets("email") == ["a@x.com", "b@x.com"]
    with bind_dispatch_context(_Ctx(_STAFF_PHONE_B)):
        assert await action._is_staff()
    with bind_dispatch_context(_Ctx("9999999999")):
        assert not await action._is_staff()
    with bind_dispatch_context(_Ctx("a@x.com", channel="email")):
        assert await action._is_staff()


async def test_is_staff_uses_access_control_group(monkeypatch):
    _install_aca_staff(monkeypatch, ["5929999999"])
    action = HandoffAction()
    with bind_dispatch_context(_Ctx("5929999999")):
        assert await action._is_staff()
    with bind_dispatch_context(_Ctx(_STAFF_PHONE)):
        assert not await action._is_staff()


async def test_staff_targets_classify_aca_members(monkeypatch):
    _install_aca_staff(monkeypatch, ["5929999999", "aca@x.com"])
    action = HandoffAction()
    assert await action._staff_targets("whatsapp") == ["5929999999"]
    assert await action._staff_targets("email") == ["aca@x.com"]
    phone, emails = await action._public_contacts()
    assert phone == "5929999999"
    assert emails == ["aca@x.com"]


async def test_staff_targets_empty_without_aca(monkeypatch):
    action = HandoffAction()

    async def _no_aca(self, *args, **kwargs):
        return None

    monkeypatch.setattr(HandoffAction, "get_action", _no_aca)
    assert await action._staff_targets("whatsapp") == []
    assert await action._staff_targets("email") == []


async def test_pending_list_is_shared_and_save_stays_staff_only(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)

    class _Agent:
        id = "n.Agent.shared"

    action = _bind_action(HandoffAction())
    other = _bind_action(HandoffAction())
    _seed_pending(
        action,
        id="q1",
        question="Do you offer delivery?",
        user_channel="whatsapp",
        user_contact="5926431530",
    )

    async def _get_agent(self):
        return _Agent()

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    listed = await action._list_pending()
    assert any(row["id"] == "q1" for row in listed)
    # Same action id: second instance refreshes and sees the shared pending list.
    other_listed = await other._list_pending()
    assert any(row["id"] == "q1" for row in other_listed)

    tools = {t.name: t for t in await action.get_tools()}
    with bind_dispatch_context(_Ctx("999")):
        listed_tool = await tools["handoff__pending_questions"].call()
        denied = await tools["handoff__save_answer"].call(
            question_id="q1", answer="Yes, we deliver."
        )
    assert not listed_tool.is_error
    assert "Do you offer delivery?" in listed_tool.content
    assert denied.is_error
    assert "only authorized staff" in denied.content


async def test_pending_crud_add_list_update_remove(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)

    class _Agent:
        id = "n.Agent.shared"

    writer = _bind_action(HandoffAction())
    reader = _bind_action(HandoffAction())

    async def _get_agent(self):
        return _Agent()

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    with bind_dispatch_context(_Ctx("5926431530", channel="whatsapp")):
        created = await writer._add_pending("Do you offer delivery?", "5926431530")
    assert len(writer.pending_questions) == 1
    assert created["id"] == writer.pending_questions[0]["id"]

    listed = await reader._list_pending()
    assert len(listed) == 1
    assert listed[0]["question"] == "Do you offer delivery?"
    assert listed[0]["user_contact"] == "5926431530"

    await reader._update_pending(created["id"], user_contact="5920001111")
    assert writer.pending_questions[0]["user_contact"] == "5920001111"
    assert (await reader._list_pending())[0]["user_contact"] == "5920001111"

    tools = {t.name: t for t in await reader.get_tools()}
    with bind_dispatch_context(_Ctx("999")):
        denied = await tools["handoff__save_answer"].call(
            question_id=created["id"], answer="Yes."
        )
    assert denied.is_error
    assert "only authorized staff" in denied.content

    async def _append(self, question, answer):
        return question, answer, "n.DocumentNode.test"

    monkeypatch.setattr(HandoffAction, "_append_and_ingest", _append)
    with bind_dispatch_context(_Ctx(_STAFF_PHONE)):
        saved = await tools["handoff__save_answer"].call(
            question_id=created["id"], answer="Yes, we deliver for 5000."
        )
    assert not saved.is_error
    assert await reader._list_pending() == []


async def test_add_pending_raises_when_not_listable(monkeypatch):
    _install_pending_store(monkeypatch)

    class _Agent:
        id = "n.Agent.shared"

    action = _bind_action(HandoffAction())

    async def _get_agent(self):
        return _Agent()

    async def _save_no_persist(self):
        # Simulate write that never becomes visible to Action.get.
        aid = str(getattr(self, "id", "") or _PendingStore.ACTION_ID)
        object.__setattr__(self, "id", aid)
        _PendingStore.by_action[aid] = []

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    monkeypatch.setattr(HandoffAction, "save", _save_no_persist)
    with bind_dispatch_context(_Ctx("5926431530", channel="whatsapp")):
        try:
            await action._add_pending("Do you offer delivery?", "5926431530")
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "not listable" in str(exc)


def test_action_contributes_orchestration_routing_parameter():
    from jvagent.action.parameters import orchestration_parameters, render_parameters

    action = HandoffAction()
    params = orchestration_parameters(action.parameters)
    assert params and params[0].get("key") == "handoff_routing"
    rendered = render_parameters(params)
    assert "handoff__staff_lookup" in rendered
    assert "handoff__contact_details" in rendered
    assert "use_skill" in rendered
    assert params[1].get("key") == "handoff_staff_answer"
    assert "handoff__pending_questions" in rendered
    assert "handoff__save_answer" in rendered


def test_extract_saved_answer_strips_prefix_and_prefers_longer_utterance():
    assert (
        _extract_saved_answer("save answer: no we do not sell cars")
        == "no we do not sell cars"
    )
    assert _extract_saved_answer("Answer: we do not sell cars") == "we do not sell cars"
    assert (
        _extract_saved_answer("no we do", "save answer: no we do not sell cars")
        == "no we do not sell cars"
    )
    assert (
        _extract_saved_answer(
            "no we do not sell cars", "save answer: no we do not sell cars"
        )
        == "no we do not sell cars"
    )


def test_pending_question_text_keeps_ask_drops_handling_notes():
    full = (
        "Customer asked if we sell car parts. "
        "No information found in the FAQ or catalog."
    )
    assert _pending_question_text(full) == "Customer asked if we sell car parts."
    assert (
        _pending_question_text("Customer asked for the company's location.")
        == "Customer asked for the company's location."
    )


def test_bold_whatsapp_ask_wraps_core():
    assert (
        _bold_whatsapp_ask("Customer asked if we sell car parts.")
        == "Customer asked if *we sell car parts*."
    )
    assert (
        _bold_whatsapp_ask("Customer asked for the company's location.")
        == "Customer asked for *the company's location*."
    )


def test_staff_outbound_lookup_bolds_ask_and_keeps_notes():
    body = _staff_outbound(
        "staff_lookup",
        "Customer asked if we sell car parts. No information found in the FAQ.",
        None,
        None,
        "",
    )
    assert body.startswith("Customer asked if *we sell car parts*.")
    assert "No information found in the FAQ." in body


async def test_staff_lookup_stores_question_only_sends_bold_notes(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())
    sent = {}

    class _Agent:
        id = "n.Agent.test"

    async def _get_agent(self):
        return _Agent()

    async def _send(self, recipient, message):
        sent["to"] = recipient
        sent["message"] = message

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    with bind_dispatch_context(_Ctx("o.User.abc", channel="default")):
        result = await tools["handoff__staff_lookup"].call(
            message=(
                "Customer asked for the company's location. "
                "No information found in the knowledge base."
            ),
            phone_numbers=["5926431530"],
        )
    assert not result.is_error
    assert len(action.pending_questions) == 1
    assert (
        action.pending_questions[0]["question"]
        == "Customer asked for the company's location."
    )
    assert "No information found" not in action.pending_questions[0]["question"]
    assert sent["to"] == _STAFF_PHONE
    assert "Customer asked for *the company's location*." in sent["message"]
    assert "No information found in the knowledge base." in sent["message"]


async def test_resolve_saves_cleaned_answer_and_thanks(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())
    _seed_pending(
        action,
        id="q1",
        question="Do you sell cars?",
    )
    saved = {}

    class _Agent:
        id = "n.Agent.test"

    async def _get_agent(self):
        return _Agent()

    async def _append(self, question, answer):
        saved["ingested"] = (question, answer)
        return question, answer, "n.DocumentNode.test"

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    monkeypatch.setattr(HandoffAction, "_append_and_ingest", _append)

    tools = {t.name: t for t in await action.get_tools()}
    visitor = _Ctx(_STAFF_PHONE)
    visitor.utterance = (
        "save answer: no we do not sell cars and we do not take trade-ins"
    )
    with bind_dispatch_context(visitor):
        result = await tools["handoff__save_answer"].call(
            question_id="q1",
            answer="save answer: no we do",
        )
    assert not result.is_error
    expected = "no we do not sell cars and we do not take trade-ins"
    assert action.pending_questions == []
    assert await action._list_pending() == []
    assert saved["ingested"] == ("Do you sell cars?", expected)
    assert "Your answer is saved and will be used in the future" in result.content
    assert "Do you sell cars?" not in result.content


async def test_save_answer_whatsapp_replies_to_customer(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())
    _seed_pending(
        action,
        id="q1",
        question="Customer asked if we sell cars.",
        user_channel="whatsapp",
        user_contact="592111",
    )
    sent = {}

    class _Agent:
        id = "n.Agent.test"

    async def _get_agent(self):
        return _Agent()

    async def _append(self, question, answer):
        return "Do you sell cars?", "No, we do not sell cars.", "n.DocumentNode.test"

    async def _send(self, recipient, message):
        sent["to"] = recipient
        sent["message"] = message

    async def _reply(self, question, answer):
        return f"You asked: {question}\n\n{answer}"

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    monkeypatch.setattr(HandoffAction, "_append_and_ingest", _append)
    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    monkeypatch.setattr(HandoffAction, "_customer_reply", _reply)
    tools = {t.name: t for t in await action.get_tools()}
    with bind_dispatch_context(_Ctx(_STAFF_PHONE)):
        result = await tools["handoff__save_answer"].call(
            question_id="q1", answer="No, we do not sell cars."
        )
    assert not result.is_error
    assert sent["to"] == "592111"
    assert "Do you sell cars?" in sent["message"]
    assert "No, we do not sell cars." in sent["message"]


async def test_save_answer_replies_to_provided_contact(monkeypatch):
    _install_aca_staff(monkeypatch)

    async def _append(self, question, answer):
        return "Do you sell cars?", "No, we do not sell cars.", "n.DocumentNode.test"

    async def _reply(self, question, answer):
        return f"You asked: {question}\n\n{answer}"

    monkeypatch.setattr(HandoffAction, "_append_and_ingest", _append)
    monkeypatch.setattr(HandoffAction, "_customer_reply", _reply)

    async def _run(contact):
        _install_pending_store(monkeypatch)
        action = _bind_action(HandoffAction())
        _seed_pending(
            action,
            id="q1",
            question="Customer asked if we sell cars.",
            user_channel="default",
            user_contact=contact,
        )
        sent = {}

        class _Agent:
            id = "n.Agent.test"

        async def _get_agent(self):
            return _Agent()

        async def _send_whatsapp(self, recipient, message):
            sent["whatsapp"] = (recipient, message)

        async def _send_email(
            self, recipients, message, subject="Human handoff request"
        ):
            sent["email"] = (recipients, message, subject)

        monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
        monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send_whatsapp)
        monkeypatch.setattr(HandoffAction, "_send_email", _send_email)
        tools = {t.name: t for t in await action.get_tools()}
        with bind_dispatch_context(_Ctx(_STAFF_PHONE)):
            result = await tools["handoff__save_answer"].call(
                question_id="q1", answer="No, we do not sell cars."
            )
        assert not result.is_error
        return sent

    phone = await _run("592000")
    assert phone["whatsapp"][0] == "592000"
    assert "Do you sell cars?" in phone["whatsapp"][1]
    assert "email" not in phone

    mailed = await _run("customer@x.com")
    assert mailed["email"][0] == ["customer@x.com"]
    assert mailed["email"][2] == "Do you sell cars?"
    assert "No, we do not sell cars." in mailed["email"][1]
    assert "whatsapp" not in mailed

    declined = await _run("declined")
    assert declined == {}


async def test_append_and_ingest_writes_short_qa_and_passes_text(monkeypatch, tmp_path):
    json_path = tmp_path / "handoff.json"
    monkeypatch.setattr(
        "jvagent.action.handoff_action.handoff_action._handoff_json_path",
        lambda: json_path,
    )

    condense_calls = []

    async def _condense(self, question, answer):
        condense_calls.append((question, answer))
        return "Do you sell cars?", "No, we do not sell cars."

    captured = {}

    async def _export(collection_name="default", doc_name=None, root_id=None):
        return {
            "roots": [
                {
                    "id": "n.DocumentRootNode.existing",
                    "entity": "DocumentRootNode",
                    "type_code": "n",
                    "edge_ids": ["e.DocumentContentEdge.existing"],
                    "doc_name": "handoff.md",
                    "collection_name": collection_name,
                    "metadata": {"access": "public"},
                    "chunks": 1,
                }
            ],
            "nodes": [
                {
                    "id": "n.DocumentNode.existing",
                    "entity": "DocumentNode",
                    "title": "Where are you located?",
                }
            ],
            "edges": [
                {
                    "id": "e.DocumentContentEdge.existing",
                    "entity": "DocumentContentEdge",
                    "source": "n.DocumentRootNode.existing",
                    "target": "n.DocumentNode.existing",
                }
            ],
        }

    async def _delete(doc_name, collection_name="default"):
        captured["deleted"] = doc_name
        captured["collection"] = collection_name
        return True

    async def _import(data, purge=False, collection_name=None):
        captured["graph"] = data
        captured["purge"] = purge
        captured["import_collection"] = collection_name

    monkeypatch.setattr("jvagent.action.pageindex.documents.export_documents", _export)
    monkeypatch.setattr("jvagent.action.pageindex.documents.delete_document", _delete)
    monkeypatch.setattr("jvagent.action.pageindex.documents.import_documents", _import)

    class _Agent:
        id = "n.Agent.test"

    action = HandoffAction()

    async def _get_agent(self):
        return _Agent()

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    monkeypatch.setattr(HandoffAction, "_condense_qa", _condense)

    short_q, short_a, chunk_id = await action._append_and_ingest(
        "Customer asked if we sell cars. Please confirm if we sell vehicles.",
        "No, we do not sell cars.",
    )
    assert condense_calls == [
        (
            "Customer asked if we sell cars. Please confirm if we sell vehicles.",
            "No, we do not sell cars.",
        )
    ]
    assert short_q == "Do you sell cars?"
    assert short_a == "No, we do not sell cars."
    assert chunk_id.startswith("n.DocumentNode.")
    assert chunk_id != "n.DocumentNode.existing"
    assert not (tmp_path / "handoff.md").exists()
    assert captured["deleted"] == "handoff.md"
    assert captured["collection"] == "n.Agent.test"
    assert captured["purge"] is False
    graph = captured["graph"]
    assert graph["roots"][0]["doc_name"] == "handoff.md"
    assert graph["roots"][0]["metadata"]["access"] == "public"
    assert graph["roots"][0]["collection_name"] == "n.Agent.test"
    titles = {node["title"] for node in graph["nodes"]}
    assert titles == {"Where are you located?", "Do you sell cars?"}
    assert any(node["id"] == "n.DocumentNode.existing" for node in graph["nodes"])
    assert len(graph["edges"]) == 2
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert {node["title"] for node in saved["nodes"]} == titles
    assert saved["roots"][0]["doc_name"] == "handoff.md"


class _Interaction:
    def __init__(self):
        self.events = []

    def add_event(self, event, action_name):
        self.events.append((event, action_name))
        return True

    async def save(self):
        return self


async def test_save_answer_records_chunk_event(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())
    _seed_pending(
        action,
        id="q1",
        question="Customer asked if we sell cars.",
        user_channel="default",
        user_contact="",
    )
    interaction = _Interaction()

    class _Agent:
        id = "n.Agent.test"

    async def _get_agent(self):
        return _Agent()

    async def _append(self, question, answer):
        return "Do you sell cars?", "No, we do not sell cars.", "n.DocumentNode.abc"

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    monkeypatch.setattr(HandoffAction, "_append_and_ingest", _append)
    tools = {t.name: t for t in await action.get_tools()}
    visitor = _Ctx(_STAFF_PHONE)
    visitor.interaction = interaction
    with bind_dispatch_context(visitor):
        result = await tools["handoff__save_answer"].call(
            question_id="q1", answer="No, we do not sell cars."
        )
    assert not result.is_error
    assert interaction.events == [
        ('Handoff chunk n.DocumentNode.abc "Do you sell cars?".', "HandoffAction")
    ]


async def test_update_chunk_replaces_existing_answer(monkeypatch, tmp_path):
    _install_aca_staff(monkeypatch)
    json_path = tmp_path / "handoff.json"
    json_path.write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "id": "n.DocumentNode.existing",
                        "title": "Do you sell cars?",
                        "text": "old",
                        "summary": "old",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "jvagent.action.handoff_action.handoff_action._handoff_json_path",
        lambda: json_path,
    )
    captured = {}
    interaction = _Interaction()

    async def _condense(self, question, answer):
        captured["condense"] = (question, answer)
        return "Do you sell cars?", "No, we do not sell cars."

    async def _update(chunk_id, doc_name, collection_name, updates):
        captured["update"] = (chunk_id, doc_name, collection_name, updates)
        return {"id": chunk_id}

    async def _get_agent(self):
        class _Agent:
            id = "n.Agent.test"

        return _Agent()

    monkeypatch.setattr(HandoffAction, "_condense_qa", _condense)
    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    monkeypatch.setattr(
        "jvagent.action.pageindex.documents.update_document_chunk", _update
    )
    action = HandoffAction()
    tools = {t.name: t for t in await action.get_tools()}
    visitor = _Ctx(_STAFF_PHONE)
    visitor.interaction = interaction
    with bind_dispatch_context(visitor):
        result = await tools["handoff__update_chunk"].call(
            chunk_id="n.DocumentNode.existing",
            answer="No, we do not sell cars.",
        )
    assert not result.is_error
    assert "Your answer is updated" in result.content
    assert captured["condense"] == ("Do you sell cars?", "No, we do not sell cars.")
    chunk_id, doc_name, collection, updates = captured["update"]
    assert chunk_id == "n.DocumentNode.existing"
    assert doc_name == "handoff.md"
    assert collection == "n.Agent.test"
    assert updates["text"] == "## Do you sell cars?\n\nNo, we do not sell cars."
    assert updates["summary"] == updates["text"]
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["nodes"][0]["text"] == updates["text"]
    assert not (tmp_path / "handoff.md").exists()
    assert interaction.events[0][0] == (
        'Handoff chunk n.DocumentNode.existing "Do you sell cars?".'
    )


def test_handoff_skills_split_access_and_tools():
    from pathlib import Path

    from jvagent.action.orchestrator.orchestrator_interact_action import (
        OrchestratorInteractAction,
    )
    from jvagent.action.orchestrator.skills import SkillDoc
    from jvagent.scaffold.skill_resolve import parse_skill_bundle

    root = Path(__file__).resolve().parents[2] / "jvagent" / "skills"
    customer = parse_skill_bundle(root / "handoff", source="builtin")
    staff = parse_skill_bundle(root / "handoff_staff", source="builtin")
    assert customer["access_action"] == "HandoffAction"
    assert customer["denied_groups"] == ["staff"]
    assert staff["access_action"] == "HandoffAction"
    assert staff["allowed_groups"] == ["staff"]
    assert "handoff__staff_lookup" in customer["allowed_tools"]
    assert "handoff__save_answer" not in customer["allowed_tools"]
    assert staff["allowed_tools"] == [
        "handoff__pending_questions",
        "handoff__save_answer",
        "handoff__update_chunk",
    ]
    assert "pending customer question" in staff["description"]
    assert "cannot answer from the knowledge base" in customer["description"]

    class _ACA:
        def policy_applies(self):
            return True

        def get_user_groups(self, action_label=None):
            return {"staff": ["111"]}

    customer_doc = SkillDoc(
        name="handoff",
        description="",
        body="",
        access_action="HandoffAction",
        denied_groups=("staff",),
    )
    staff_doc = SkillDoc(
        name="handoff_staff",
        description="",
        body="",
        access_action="HandoffAction",
        allowed_groups=("staff",),
    )
    aca = _ACA()
    assert OrchestratorInteractAction._skill_access_control_allowed(
        customer_doc, "999", aca
    )
    assert not OrchestratorInteractAction._skill_access_control_allowed(
        customer_doc, "111", aca
    )
    assert OrchestratorInteractAction._skill_access_control_allowed(
        staff_doc, "111", aca
    )
    assert not OrchestratorInteractAction._skill_access_control_allowed(
        staff_doc, "999", aca
    )
    assert not OrchestratorInteractAction._skill_access_control_allowed(
        staff_doc, "111", None
    )
