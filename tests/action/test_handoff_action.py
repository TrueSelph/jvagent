"""HandoffAction — one mode at a time (consult, transfer, or observe).

consult asks staff and replies later. transfer leaves the conversation.
observe stores WhatsApp group facts and enrolls group numbers. Staff
targets come from AccessControlAction HandoffAction.staff.
"""

import json
from types import SimpleNamespace

from jvagent.action.handoff_action import HandoffAction
from jvagent.action.handoff_action.handoff_action import (
    _bold_whatsapp_ask,
    _contact_kind,
    _extract_saved_answer,
    _pending_question_text,
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

        async def has_tool_access(self, user_id, tool_name, channel="default"):
            return (user_id or "").strip() in staff

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


def test_defaults():
    action = HandoffAction()
    assert action.mode == "consult"
    assert "9:00 AM" in action.handoff_hours


async def test_tools_follow_mode():
    consult = HandoffAction()
    consult_tools = await consult.get_tools()
    assert {t.name for t in consult_tools} == {
        "handoff__consult",
        "handoff__save_answer",
        "handoff__update_chunk",
    }
    gated = {t.name for t in consult_tools if t.requires_tool_permission}
    assert gated == {"handoff__save_answer", "handoff__update_chunk"}
    transfer = HandoffAction()
    transfer.mode = "transfer"
    assert {t.name for t in await transfer.get_tools()} == {"handoff__transfer"}
    observe = HandoffAction()
    observe.mode = "observe"
    observe_tools = await observe.get_tools()
    assert {t.name for t in observe_tools} == {"handoff__observe"}
    assert observe_tools[0].requires_tool_permission is True
    unknown = HandoffAction()
    unknown.mode = "nope"
    assert "handoff__consult" in {t.name for t in await unknown.get_tools()}


async def test_consult_message_rule_tells_model_to_synthesize():
    action = HandoffAction()
    tools = {t.name: t for t in await action.get_tools()}
    props = tools["handoff__consult"].parameters_schema["properties"]
    desc = props["message"]["description"]
    assert "complete, grammatically full sentence" in desc
    assert "continuation" in desc
    assert "shorthand fragments" in desc


async def test_transfer_asks_on_web_when_no_contact(monkeypatch):
    _install_aca_staff(monkeypatch)
    action = HandoffAction()
    action.mode = "transfer"
    action.customer_contact = "phone"
    sent = {}

    async def _send(self, recipient, message):
        sent["to"] = recipient

    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    with bind_dispatch_context(_Ctx("o.User.abc", channel="web")):
        r = await tools["handoff__transfer"].call(message="need a person")
    assert not r.is_error
    assert "Tell the user this" in r.content
    assert "sort that out with our team" in r.content.lower()
    assert "WhatsApp number" in r.content
    assert "email" not in r.content.lower().split("internal")[0]
    assert "handoff__transfer" in r.content
    assert "to" not in sent


async def test_transfer_web_two_step_uses_confirm_not_repeat_limitation(
    monkeypatch,
):
    _install_aca_staff(monkeypatch)
    action = HandoffAction()
    action.mode = "transfer"
    action.customer_contact = "phone"
    conversation = _Conversation()
    sent = {}

    async def _send(self, recipient, message):
        sent["to"] = recipient
        sent["message"] = message

    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    ctx = _Ctx("o.User.abc", channel="web", conversation=conversation)
    with bind_dispatch_context(ctx):
        ask = await tools["handoff__transfer"].call(message="need a person")
    assert not ask.is_error
    assert "sort that out with our team" in ask.content.lower()
    assert "to" not in sent
    assert conversation.context.get("handoff_active_mode") == "transfer"

    with bind_dispatch_context(ctx):
        confirm = await tools["handoff__transfer"].call(
            message="need a person",
            contact="5926431530",
        )
    assert not confirm.is_error
    assert "staff member will reach out" in confirm.content.lower()
    assert "sort that out with our team" not in confirm.content.lower()
    assert sent["to"] == _STAFF_PHONE
    assert "Contact: 5926431530" in sent["message"]


async def test_transfer_web_infers_contact_from_utterance_on_active_handoff(
    monkeypatch,
):
    _install_aca_staff(monkeypatch)
    action = HandoffAction()
    action.mode = "transfer"
    action.customer_contact = "phone"
    conversation = _Conversation()
    sent = {}

    async def _send(self, recipient, message):
        sent["to"] = recipient
        sent["message"] = message

    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    visitor1 = _Ctx("o.User.abc", channel="web", conversation=conversation)
    with bind_dispatch_context(visitor1):
        ask = await tools["handoff__transfer"].call(message="need office location")
    assert not ask.is_error
    assert "WhatsApp number" in ask.content
    assert "to" not in sent

    visitor2 = _Ctx("o.User.abc", channel="web", conversation=conversation)
    visitor2.utterance = "5927371531"
    with bind_dispatch_context(visitor2):
        confirm = await tools["handoff__transfer"].call(
            message=(
                "Customer wants the office location to visit tomorrow "
                "and needs staff follow-up. User provided WhatsApp number "
                "5927371531."
            ),
        )
    assert not confirm.is_error
    assert "staff member will reach out" in confirm.content.lower()
    assert "WhatsApp number" not in confirm.content.split("INTERNAL")[0]
    assert sent["to"] == _STAFF_PHONE
    assert "5927371531" in sent["message"]


async def test_transfer_web_infers_contact_from_contact_only_message(
    monkeypatch,
):
    _install_aca_staff(monkeypatch)
    action = HandoffAction()
    action.mode = "transfer"
    action.customer_contact = "phone"
    conversation = _Conversation()
    sent = {}

    async def _send(self, recipient, message):
        sent["message"] = message

    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    visitor1 = _Ctx("o.User.abc", channel="web", conversation=conversation)
    with bind_dispatch_context(visitor1):
        await tools["handoff__transfer"].call(message="need a person")

    visitor2 = _Ctx("o.User.abc", channel="web", conversation=conversation)
    with bind_dispatch_context(visitor2):
        confirm = await tools["handoff__transfer"].call(message="5927371531")
    assert not confirm.is_error
    assert "staff member will reach out" in confirm.content.lower()
    assert sent["message"] == "5927371531"
    assert conversation.context.get("handoff_contact") == "5927371531"


async def test_consult_web_infers_contact_from_utterance_on_active_handoff(
    monkeypatch,
):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())
    conversation = _Conversation()
    sent = {"count": 0, "message": ""}

    class _Agent:
        id = "n.Agent.test"

    async def _get_agent(self):
        return _Agent()

    async def _send(self, recipient, message):
        sent["count"] += 1
        sent["message"] = message

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    visitor1 = _Ctx("o.User.abc", channel="web", conversation=conversation)
    visitor1.utterance = "where is your office?"
    with bind_dispatch_context(visitor1):
        first = await tools["handoff__consult"].call(message="where is your office?")
    assert not first.is_error
    assert sent["count"] == 0

    visitor2 = _Ctx("o.User.abc", channel="web", conversation=conversation)
    visitor2.utterance = "5927371531"
    with bind_dispatch_context(visitor2):
        second = await tools["handoff__consult"].call(
            message="Customer wants the office location.",
        )
    assert not second.is_error
    assert sent["count"] == 1
    assert action.pending_questions[0]["question"] == "where is your office?"
    assert "Contact: 5927371531" in sent["message"]


async def test_transfer_notifies_staff_and_relays(monkeypatch):
    _install_aca_staff(monkeypatch)
    action = HandoffAction()
    action.mode = "transfer"
    conversation = _Conversation()
    sent = {}

    async def _send(self, recipient, message):
        sent["to"] = recipient
        sent["message"] = message

    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    with bind_dispatch_context(_Ctx("592000", conversation=conversation)):
        r = await tools["handoff__transfer"].call(message="need a person")
    assert not r.is_error
    assert "staff member will reach out" in r.content
    assert "WhatsApp number or email" not in r.content
    assert sent["to"] == _STAFF_PHONE
    assert "Contact: 592000" in sent["message"]
    assert "need a person" in sent["message"]
    assert not conversation.context.get("handoff_transferred")


async def test_transfer_ignores_placeholder_contact_uses_whatsapp_user_id(
    monkeypatch,
):
    _install_aca_staff(monkeypatch)
    action = HandoffAction()
    action.mode = "transfer"
    conversation = _Conversation()
    sent = {}

    async def _send(self, recipient, message):
        sent["message"] = message

    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    with bind_dispatch_context(_Ctx("5926431530", conversation=conversation)):
        r = await tools["handoff__transfer"].call(
            message="Customer asked for location.",
            contact="not provided",
        )
    assert not r.is_error
    assert "Contact: 5926431530" in sent["message"]
    assert conversation.context.get("handoff_contact") != "not provided"


async def test_transfer_web_resolves_contact_from_email_user_id(monkeypatch):
    _install_aca_staff(monkeypatch)
    action = HandoffAction()
    action.mode = "transfer"
    action.customer_contact = "email"
    sent = {}

    async def _send(self, recipient, message):
        sent["message"] = message

    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    with bind_dispatch_context(_Ctx("john@example.com", channel="web")):
        r = await tools["handoff__transfer"].call(message="need help")
    assert not r.is_error
    assert "staff member will reach out" in r.content
    assert "Contact: john@example.com" in sent["message"]


async def test_transfer_web_ignores_saved_email_when_customer_contact_phone(
    monkeypatch,
):
    _install_aca_staff(monkeypatch)
    action = HandoffAction()
    action.mode = "transfer"
    action.customer_contact = "phone"
    conversation = _Conversation("not-an-email@example.com")
    sent = {}

    async def _send(self, recipient, message):
        sent["to"] = recipient

    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    with bind_dispatch_context(
        _Ctx("o.User.abc", channel="web", conversation=conversation)
    ):
        r = await tools["handoff__transfer"].call(message="need help")
    assert not r.is_error
    assert "Tell the user this" in r.content
    assert "to" not in sent


async def test_transfer_web_uses_saved_contact_when_user_id_opaque(monkeypatch):
    _install_aca_staff(monkeypatch)
    action = HandoffAction()
    action.mode = "transfer"
    action.customer_contact = "phone"
    conversation = _Conversation("592999")
    sent = {}

    async def _send(self, recipient, message):
        sent["message"] = message

    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    with bind_dispatch_context(
        _Ctx("o.User.abc", channel="web", conversation=conversation)
    ):
        r = await tools["handoff__transfer"].call(message="need help")
    assert not r.is_error
    assert "Contact: 592999" in sent["message"]


async def test_transfer_ignores_poisoned_saved_contact_on_whatsapp(monkeypatch):
    _install_aca_staff(monkeypatch)
    action = HandoffAction()
    action.mode = "transfer"
    conversation = _Conversation("not provided")
    sent = {}

    async def _send(self, recipient, message):
        sent["message"] = message

    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    with bind_dispatch_context(_Ctx("592000", conversation=conversation)):
        r = await tools["handoff__transfer"].call(message="need a person")
    assert not r.is_error
    assert "Contact: 592000" in sent["message"]


async def test_consult_stores_message_without_code_refinement(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())

    class _Agent:
        id = "n.Agent.test"

    async def _get_agent(self):
        return _Agent()

    async def _send(self, recipient, message):
        pass

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    with bind_dispatch_context(_Ctx("5926431530", channel="whatsapp")):
        result = await tools["handoff__consult"].call(
            message="can u check where u are located again"
        )
    assert not result.is_error
    assert len(action.pending_questions) == 1
    assert (
        action.pending_questions[0]["question"]
        == "can u check where u are located again"
    )


async def test_consult_preserves_customer_ask_on_contact_follow_up(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())
    conversation = _Conversation()
    sent = {"count": 0, "message": ""}

    class _Agent:
        id = "n.Agent.test"

    async def _get_agent(self):
        return _Agent()

    async def _send(self, recipient, message):
        sent["count"] += 1
        sent["message"] = message

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    visitor1 = _Ctx("o.User.abc", channel="default", conversation=conversation)
    visitor1.utterance = "what is your address?"
    with bind_dispatch_context(visitor1):
        first = await tools["handoff__consult"].call(message="what is your address?")
    assert not first.is_error
    assert sent["count"] == 0
    assert conversation.context.get("handoff_active_issue") == "what is your address?"

    visitor2 = _Ctx("o.User.abc", channel="default", conversation=conversation)
    visitor2.utterance = "5927371531"
    with bind_dispatch_context(visitor2):
        second = await tools["handoff__consult"].call(
            message="Customer wants the store address.",
            contact="5927371531",
        )
    assert not second.is_error
    assert sent["count"] == 1
    assert action.pending_questions[0]["question"] == "what is your address?"
    assert "Customer wants" not in action.pending_questions[0]["question"]
    assert "what is your address?" in sent["message"]


async def test_consult_prefers_utterance_for_stored_location_ask(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())
    conversation = _Conversation()
    sent = {"count": 0}

    class _Agent:
        id = "n.Agent.test"

    async def _get_agent(self):
        return _Agent()

    async def _send(self, recipient, message):
        sent["count"] += 1

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    visitor1 = _Ctx("o.User.abc", channel="default", conversation=conversation)
    visitor1.utterance = "where r u located"
    with bind_dispatch_context(visitor1):
        await tools["handoff__consult"].call(
            message="Customer wants the store address."
        )
    assert conversation.context.get("handoff_active_issue") == "where r u located"

    visitor2 = _Ctx("o.User.abc", channel="default", conversation=conversation)
    visitor2.utterance = "5927371531"
    with bind_dispatch_context(visitor2):
        await tools["handoff__consult"].call(
            message="5927371531",
            contact="5927371531",
        )
    assert action.pending_questions[0]["question"] == "where r u located"
    assert "store address" not in action.pending_questions[0]["question"].lower()


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

    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    monkeypatch.setattr(HandoffAction, "_add_pending", _record)
    tools = {t.name: t for t in await action.get_tools()}

    r = await tools["handoff__consult"].call(message="where do you deliver?")
    assert not r.is_error
    assert "Tell the user this" in r.content
    assert "sort that out with our team" in r.content.lower()
    assert "WhatsApp number" in r.content
    assert "handoff__consult" in r.content
    assert sent["count"] == 0
    assert "question" not in sent
    assert open_q.get("q") is None

    r = await tools["handoff__consult"].call(message="where do you deliver?")
    assert sent["count"] == 0

    r = await tools["handoff__consult"].call(
        message=(
            "The customer asked where we deliver. FAQ search returned nothing. "
            "Customer provided phone number: 592000."
        ),
        contact="592000",
    )
    assert "checking with the team" in r.content
    assert sent["count"] == 1
    assert open_q["q"].user_contact == "592000"
    assert "Contact: 592000" in sent["message"]
    assert "where we deliver" in sent["message"]

    r = await tools["handoff__consult"].call(
        message="The customer asked where we deliver. FAQ search found nothing useful.",
        contact="592000",
    )
    assert sent["count"] == 2

    open_q.clear()
    sent["count"] = 0
    r = await tools["handoff__consult"].call(
        message="where do you deliver?", contact_declined=True
    )
    assert "checking with the team" in r.content
    assert sent["count"] == 1
    assert open_q["q"].user_contact == "declined"

    with bind_dispatch_context(_Ctx("592111")):
        open_q.clear()
        r = await tools["handoff__consult"].call(message="what are your hours?")
    assert "checking with the team" in r.content
    assert "WhatsApp number or email" not in r.content


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
        looked = await tools["handoff__consult"].call(
            message="where do you deliver?", contact_declined=True
        )
    assert "checking with the team" in looked.content
    assert sent == [("whatsapp", _STAFF_PHONE)]
    assert len(action.pending_questions) == 1
    row = action.pending_questions[0]
    assert row["user_contact"] == "declined"
    question_id = row["id"]
    sent.clear()
    with bind_dispatch_context(_Ctx(_STAFF_PHONE)):
        saved = await tools["handoff__save_answer"].call(
            question_ids=[question_id], answer="Yes, we deliver."
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
        reused = await tools["handoff__consult"].call(message="Do you sell car parts")
        again = await tools["handoff__consult"].call(message="Do you offer delivery")
        repeat = await tools["handoff__consult"].call(message="Do you offer delivery")
    assert "WhatsApp number or email" not in reused.content
    assert "checking with the team" in reused.content
    assert "checking with the team" in again.content
    assert sent["count"] == 3
    assert all("Contact: 5926431530" in message for message in sent["messages"])
    assert [row["question"] for row in action.pending_questions] == [
        "Do you sell car parts",
        "Do you offer delivery",
        "Do you offer delivery",
    ]
    assert {row["user_contact"] for row in action.pending_questions} == {"5926431530"}
    assert "checking with the team" in repeat.content


async def test_consult_always_appends_separate_rows(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())
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
    question = "Where are you located?"
    with bind_dispatch_context(_Ctx("5923333333", channel="whatsapp")):
        first = await tools["handoff__consult"].call(message=question)
    assert not first.is_error
    assert sent["count"] == 1
    assert len(action.pending_questions) == 1
    assert action.pending_questions[0]["user_contact"] == "5923333333"
    with bind_dispatch_context(_Ctx("5924444444", channel="whatsapp")):
        second = await tools["handoff__consult"].call(
            message=question, contact="5924444444"
        )
    assert not second.is_error
    assert sent["count"] == 2
    assert "Contact: 5924444444" in sent["messages"][-1]
    assert len(action.pending_questions) == 2
    contacts = {row["user_contact"] for row in action.pending_questions}
    assert contacts == {"5923333333", "5924444444"}
    with bind_dispatch_context(_Ctx("5923333333", channel="whatsapp")):
        third = await tools["handoff__consult"].call(
            message=question, contact="5923333333"
        )
    assert not third.is_error
    assert sent["count"] == 3
    assert len(action.pending_questions) == 3


async def test_staff_turn_parameter_lists_id_and_question_only(monkeypatch):
    from jvagent.action.parameters import render_parameters

    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())
    _seed_pending(
        action,
        id="pend_a",
        question="Customer asks where the store is located.",
        user_contact="5921111111",
    )
    _seed_pending(
        action,
        id="pend_b",
        question="What are your hours?",
        user_contact="5922222222",
    )
    params = await action.contributed_parameters(SimpleNamespace(user_id=_STAFF_PHONE))
    rendered = render_parameters(params)
    assert "PENDING QUESTIONS:" in rendered
    assert "pend_a" in rendered
    assert "pend_b" in rendered
    assert "5921111111" not in rendered
    assert "user_contact" not in rendered
    assert "created_at" not in rendered
    assert "handoff__pending_questions" not in rendered


async def test_staff_turn_parameter_empty_queue(monkeypatch):
    from jvagent.action.parameters import render_parameters

    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())
    params = await action.contributed_parameters(SimpleNamespace(user_id=_STAFF_PHONE))
    rendered = render_parameters(params)
    assert "PENDING QUESTIONS: []" in rendered
    assert "do not save" in rendered.lower()


async def test_save_answer_group_one_ingest_multi_reply(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())
    customer_sends = []

    class _Agent:
        id = "n.Agent.test"

    async def _get_agent(self):
        return _Agent()

    async def _send_whatsapp(self, recipient, message):
        if recipient != _STAFF_PHONE:
            customer_sends.append(recipient)

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send_whatsapp)

    async def _customer_reply(self, q, a):
        return f"Answer: {a}"

    monkeypatch.setattr(HandoffAction, "_customer_reply", _customer_reply)
    ingest_calls = {"count": 0}

    async def _append_once(self, question, answer):
        ingest_calls["count"] += 1
        return question, answer, "n.DocumentNode.test"

    monkeypatch.setattr(HandoffAction, "_append_and_ingest", _append_once)
    tools = {t.name: t for t in await action.get_tools()}
    question = "Where are you located?"
    with bind_dispatch_context(_Ctx("5923333333", channel="whatsapp")):
        await tools["handoff__consult"].call(message=question)
    with bind_dispatch_context(_Ctx("5924444444", channel="whatsapp")):
        await tools["handoff__consult"].call(
            message="Where are you located? I need the store location.",
            contact="5924444444",
        )
    assert len(action.pending_questions) == 2
    id_a = action.pending_questions[0]["id"]
    id_b = action.pending_questions[1]["id"]
    with bind_dispatch_context(_Ctx(_STAFF_PHONE)):
        saved = await tools["handoff__save_answer"].call(
            question_ids=[id_a, id_b], answer="123 Main St"
        )
    assert not saved.is_error
    assert ingest_calls["count"] == 1
    assert sorted(customer_sends) == ["5923333333", "5924444444"]
    assert action.pending_questions == []


async def test_transfer_sends_web_contact(monkeypatch):
    _install_aca_staff(monkeypatch)
    action = HandoffAction()
    action.mode = "transfer"
    conversation = _Conversation()
    sent = {}

    async def _send(self, recipient, message):
        sent["to"] = recipient
        sent["message"] = message

    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    visitor = _Ctx("o.User.abc", channel="default", conversation=conversation)
    with bind_dispatch_context(visitor):
        sent_call = await tools["handoff__transfer"].call(
            message="need a person", contact="592999"
        )
    assert "staff member will reach out" in sent_call.content
    assert sent["to"] == _STAFF_PHONE
    assert "Contact: 592999" in sent["message"]
    assert conversation.context["handoff_contact"] == "592999"
    assert not conversation.context.get("handoff_transferred")


def test_channel_for_defaults_and_override():
    action = HandoffAction()
    assert action._channel_for("consult") == "whatsapp"
    action.handoff_channels = {"consult": "email"}
    assert action._channel_for("consult") == "email"
    action.handoff_channels = {"consult": "nonsense"}
    assert action._channel_for("consult") == "whatsapp"


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

    aca = _aca_for_tools()
    tools = {t.name: t for t in await action.get_tools()}
    with bind_dispatch_context(_Ctx("999")):
        denied = await _run_gated_tool(
            tools["handoff__save_answer"],
            aca=aca,
            user_id="999",
            question_ids=["q1"],
            answer="Yes, we deliver.",
        )
    assert not denied.is_error
    assert "cannot save answers" in denied.content
    assert "Do not tell the user" in denied.content


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
    assert created["id"].startswith("pend_")
    assert created["id"] == writer.pending_questions[0]["id"]

    listed = await reader._list_pending()
    assert len(listed) == 1
    assert listed[0]["question"] == "Do you offer delivery?"
    assert listed[0]["user_contact"] == "5926431530"

    await reader._update_pending(created["id"], user_contact="5920001111")
    assert writer.pending_questions[0]["user_contact"] == "5920001111"
    assert (await reader._list_pending())[0]["user_contact"] == "5920001111"

    aca = _aca_for_tools()
    tools = {t.name: t for t in await reader.get_tools()}
    with bind_dispatch_context(_Ctx("999")):
        denied = await _run_gated_tool(
            tools["handoff__save_answer"],
            aca=aca,
            user_id="999",
            question_ids=[created["id"]],
            answer="Yes.",
        )
    assert not denied.is_error
    assert "cannot save answers" in denied.content

    async def _append(self, question, answer):
        return question, answer, "n.DocumentNode.test"

    monkeypatch.setattr(HandoffAction, "_append_and_ingest", _append)
    with bind_dispatch_context(_Ctx(_STAFF_PHONE)):
        saved = await tools["handoff__save_answer"].call(
            question_ids=[created["id"]], answer="Yes, we deliver for 5000."
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

    consult = HandoffAction()
    params = orchestration_parameters(consult.parameters)
    assert len(params) == 1
    assert params[0].get("key") == "handoff_consult"
    rendered = render_parameters(params)
    assert "handoff__consult" in rendered
    assert "handoff__update_chunk" not in rendered
    assert "overrides the active skill" in rendered.lower()
    assert "call handoff__consult now" in rendered.lower()
    assert "do not reply in text" in rendered.lower()
    assert "do not ask permission" in rendered.lower()
    assert "use_skill" not in rendered

    transfer = HandoffAction()
    transfer.mode = "transfer"
    transfer_params = orchestration_parameters(transfer.parameters)
    assert transfer_params[0].get("key") == "handoff_transfer"
    transfer_rendered = render_parameters(transfer_params)
    assert "handoff__transfer" in transfer_rendered
    assert "knowledge base" in transfer_rendered.lower()
    assert "overrides the active skill" in transfer_rendered.lower()
    assert transfer_params[0].get("condition") == params[0].get("condition")

    observe = HandoffAction()
    observe.mode = "observe"
    observe_params = orchestration_parameters(observe.parameters)
    assert observe_params[0].get("key") == "handoff_observe"
    rendered_observe = render_parameters(observe_params).lower()
    assert "handoff__observe" in rendered_observe
    assert "never send a message" not in rendered_observe


def test_yaml_parameters_override_mode_defaults():
    action = HandoffAction()
    action.mode = "transfer"
    custom = [
        {
            "scope": "orchestration",
            "key": "custom_handoff",
            "condition": "the conversation should move to staff",
            "response": "Call handoff__transfer.",
        }
    ]
    action.parameters = custom
    assert action.parameters == custom
    action.parameters = []
    assert action.parameters[0]["key"] == "handoff_transfer"


async def test_contributed_parameters_follow_staff_access(monkeypatch):
    from jvagent.action.parameters import render_parameters

    action = HandoffAction()

    async def _members(self):
        return [_STAFF_PHONE]

    monkeypatch.setattr(HandoffAction, "_aca_staff_members", _members)
    staff = await action.contributed_parameters(SimpleNamespace(user_id=_STAFF_PHONE))
    rendered = render_parameters(staff)
    assert "PENDING QUESTIONS:" in rendered
    assert "corr-" in rendered
    assert "not a question id or a chunk id" in rendered
    assert "handoff__pending_questions" not in rendered

    customer = await action.contributed_parameters(SimpleNamespace(user_id="999"))
    customer_text = render_parameters(customer)
    assert "call handoff__consult now" in customer_text.lower()
    assert "handoff__update_chunk" not in customer_text

    custom = [
        {
            "scope": "orchestration",
            "key": "custom_handoff",
            "condition": "the conversation should move to staff",
            "response": "Call handoff__transfer.",
        }
    ]
    action.parameters = custom
    assert (
        await action.contributed_parameters(SimpleNamespace(user_id=_STAFF_PHONE))
        == custom
    )

    action.parameters = []
    action.mode = "transfer"
    transfer_staff = await action.contributed_parameters(
        SimpleNamespace(user_id=_STAFF_PHONE)
    )
    transfer_staff_text = render_parameters(transfer_staff)
    assert transfer_staff[0].get("key") == "handoff_transfer_staff"
    assert "do not call handoff__transfer" in transfer_staff_text.lower()

    transfer_customer = await action.contributed_parameters(
        SimpleNamespace(user_id="999")
    )
    transfer_customer_text = render_parameters(transfer_customer)
    assert "handoff__transfer" in transfer_customer_text
    assert "knowledge base" in transfer_customer_text.lower()


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
        "consult",
        "Customer asked if we sell car parts. No information found in the FAQ.",
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
        result = await tools["handoff__consult"].call(
            message=(
                "Customer asked for the company's location. "
                "No information found in the knowledge base."
            ),
            contact="5926431530",
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
            question_ids=["q1"],
            answer="save answer: no we do",
        )
    assert not result.is_error
    expected = "no we do not sell cars and we do not take trade-ins"
    assert action.pending_questions == []
    assert await action._list_pending() == []
    assert saved["ingested"] == ("Do you sell cars?", expected)
    assert "Tell the user:" in result.content
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
            question_ids=["q1"], answer="No, we do not sell cars."
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
                question_ids=["q1"], answer="No, we do not sell cars."
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
            question_ids=["q1"], answer="No, we do not sell cars."
        )
    assert not result.is_error
    assert interaction.events == [
        ('Handoff chunk n.DocumentNode.abc "Do you sell cars?".', "HandoffAction"),
        ('Handoff completed: save_answer "Do you sell cars?".', "HandoffAction"),
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
    assert "Tell the user:" in result.content
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
    assert interaction.events[1][0] == (
        'Handoff completed: update_chunk "Do you sell cars?".'
    )


def test_handoff_namespace_is_registered_as_trusted():
    from jvagent.action.orchestrator import constants
    from jvagent.action.orchestrator.constants import is_untrusted_directive_source

    constants._TRUSTED_DIRECTIVE_PREFIXES_DYNAMIC.add("handoff__")
    assert not is_untrusted_directive_source("handoff__consult")
    assert not is_untrusted_directive_source("handoff__save_answer")
    assert is_untrusted_directive_source("somepkg__tool")


async def test_consult_records_completed_event(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())
    interaction = _Interaction()

    class _Agent:
        id = "n.Agent.test"

    async def _get_agent(self):
        return _Agent()

    async def _send(self, recipient, message):
        return None

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    visitor = _Ctx("o.User.abc", channel="default", conversation=_Conversation())
    visitor.interaction = interaction
    with bind_dispatch_context(visitor):
        looked = await tools["handoff__consult"].call(
            message="where do you deliver?", contact_declined=True
        )
    assert "checking with the team" in looked.content
    assert interaction.events == [
        (
            "Handoff started: sending the customer's query to staff for "
            "consultation.",
            "HandoffAction",
        ),
        (
            "Handoff completed: the customer's query was sent to staff for "
            "consultation. That request is closed — do not re-handle or look "
            "up the previous request again unless user reask it again. Always run the matching skills/tools first to check for fresh data before falling back to handoff__consult; focus on the current request.",
            "HandoffAction",
        ),
    ]


async def test_transfer_records_completed_event(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())
    action.mode = "transfer"
    interaction = _Interaction()

    class _Agent:
        id = "n.Agent.test"

    async def _get_agent(self):
        return _Agent()

    async def _send(self, recipient, message):
        return None

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    visitor = _Ctx("5926431530", channel="whatsapp", conversation=_Conversation())
    visitor.interaction = interaction
    with bind_dispatch_context(visitor):
        looked = await tools["handoff__transfer"].call(
            message="customer needs a person"
        )
    assert not looked.is_error
    assert interaction.events == [
        ("Handoff started: forwarding the issue summary to staff.", "HandoffAction"),
        (
            "Handoff completed: the issue summary was forwarded to staff. "
            "This request is closed — keep helping on later messages only.",
            "HandoffAction",
        ),
    ]


def test_contact_kind_whatsapp_group_chat_id():
    assert _contact_kind("120363428616636917") == "whatsapp_group"
    assert _contact_kind("5926431530") == "whatsapp"


async def test_consult_relay_carries_topic_not_canned_sentence(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())

    class _Agent:
        id = "n.Agent.test"

    async def _get_agent(self):
        return _Agent()

    async def _send(self, recipient, message):
        return None

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}

    with bind_dispatch_context(
        _Ctx("592000", channel="whatsapp", conversation=_Conversation())
    ):
        first = await tools["handoff__consult"].call(message="where do you deliver?")
    with bind_dispatch_context(
        _Ctx("592111", channel="whatsapp", conversation=_Conversation())
    ):
        second = await tools["handoff__consult"].call(message="what are your hours?")
    # Steering, not a canned sentence: each relay names this request's topic.
    assert "where do you deliver?" in first.content
    assert "what are your hours?" in second.content
    assert first.content != second.content
    assert "sort that out with our team" not in first.content
    assert "as soon as they respond" not in first.content
    assert "INTERNAL" not in first.content


async def test_consult_relay_literal_when_close_configured(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())
    action.consult_close = "I'll follow up shortly."

    class _Agent:
        id = "n.Agent.test"

    async def _get_agent(self):
        return _Agent()

    async def _send(self, recipient, message):
        return None

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    with bind_dispatch_context(
        _Ctx("592000", channel="whatsapp", conversation=_Conversation())
    ):
        result = await tools["handoff__consult"].call(message="where do you deliver?")
    # Explicit yaml close forces the deterministic literal path.
    assert "I'll follow up shortly." in result.content
    assert "checking with the team" not in result.content


async def test_consult_from_whatsapp_group_uses_author_not_group_id(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())
    sent = {}

    async def _send(self, recipient, message):
        sent["to"] = recipient
        sent["message"] = message

    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    group_id = "120363428616636917"
    author = "5923333333"
    visitor = _Ctx(group_id, channel="whatsapp")
    visitor.data = {
        "whatsapp_payload": {
            "isGroup": True,
            "sender": group_id,
            "author": author,
        }
    }
    with bind_dispatch_context(visitor):
        result = await tools["handoff__consult"].call(
            message="Do you deliver to Bartica?"
        )
    assert not result.is_error
    assert action.pending_questions[0]["user_contact"] == author
    assert sent["to"] == _STAFF_PHONE


async def test_consult_from_group_participant_field_without_author(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())
    sent = {}

    async def _send(self, recipient, message):
        sent["to"] = recipient

    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    group_id = "120363428616636917"
    author = "5927777777"
    visitor = _Ctx(group_id, channel="whatsapp")
    visitor.data = {
        "whatsapp_payload": {
            "isGroup": True,
            "sender": group_id,
            "author": "",
            "participant": f"{author}@c.us",
        }
    }
    with bind_dispatch_context(visitor):
        result = await tools["handoff__consult"].call(message="Need delivery info?")
    assert not result.is_error
    assert action.pending_questions[0]["user_contact"] == author
    assert sent["to"] == _STAFF_PHONE


async def test_consult_from_group_empty_author_fetches_message_by_id(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())
    sent = {}
    fetch_calls = []

    class _API:
        async def get_message_by_id(self, message_id):
            fetch_calls.append(message_id)
            return {
                "message": {
                    "_data": {"author": "5928888888@c.us"},
                }
            }

    class _WA:
        async def api(self):
            return _API()

    class _Agent:
        id = "n.Agent.test"

        async def get_action_by_type(self, name):
            assert name == "WhatsAppAction"
            return _WA()

    async def _get_agent(self):
        return _Agent()

    async def _send(self, recipient, message):
        sent["to"] = recipient

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    group_id = "120363428616636917"
    msg_id = "true_120363428616636917@g.us_ABC123"
    visitor = _Ctx(group_id, channel="whatsapp")
    visitor.data = {
        "whatsapp_payload": {
            "isGroup": True,
            "sender": group_id,
            "author": "",
            "message_id": msg_id,
        }
    }
    with bind_dispatch_context(visitor):
        result = await tools["handoff__consult"].call(message="Need delivery info?")
    assert not result.is_error
    assert fetch_calls == [msg_id]
    assert action.pending_questions[0]["user_contact"] == "5928888888"
    assert sent["to"] == _STAFF_PHONE


async def test_consult_from_group_empty_author_uses_group_id_fallback(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())
    sent = {}

    async def _send(self, recipient, message):
        sent["to"] = recipient
        sent["message"] = message

    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    group_id = "120363428616636917"
    visitor = _Ctx(group_id, channel="whatsapp")
    visitor.data = {
        "whatsapp_payload": {
            "isGroup": True,
            "sender": group_id,
            "author": "",
        }
    }
    with bind_dispatch_context(visitor):
        result = await tools["handoff__consult"].call(message="Do you offer delivery?")
    assert not result.is_error
    assert "WhatsApp number" not in result.content
    assert action.pending_questions[0]["user_contact"] == group_id
    assert sent["to"] == _STAFF_PHONE
    assert f"Group: {group_id}" in sent["message"]


async def test_save_answer_replies_in_group_thread(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())
    group_id = "120363428616636917"
    group_sends = []

    class _API:
        async def send_message(self, phone, message, is_group=False):
            group_sends.append(
                {"phone": phone, "message": message, "is_group": is_group}
            )
            return {"ok": True}

    class _WA:
        def is_configured(self):
            return True

        def _config_issues(self):
            return []

        def get_class_name(self):
            return "WhatsAppAction"

        async def api(self):
            return _API()

    class _Agent:
        id = "n.Agent.test"

        async def get_action_by_type(self, name):
            assert name == "WhatsAppAction"
            return _WA()

    async def _get_agent(self):
        return _Agent()

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)

    async def _customer_reply(self, q, a):
        return f"Reply: {a}"

    monkeypatch.setattr(HandoffAction, "_customer_reply", _customer_reply)

    async def _append_once(self, question, answer):
        return question, answer, "n.DocumentNode.test"

    monkeypatch.setattr(HandoffAction, "_append_and_ingest", _append_once)

    tools = {t.name: t for t in await action.get_tools()}
    with bind_dispatch_context(_Ctx(group_id, channel="whatsapp")):
        await tools["handoff__consult"].call(message="Do you deliver?")
    qid = action.pending_questions[0]["id"]
    with bind_dispatch_context(_Ctx(_STAFF_PHONE)):
        saved = await tools["handoff__save_answer"].call(
            question_ids=[qid], answer="Yes, we deliver."
        )
    assert not saved.is_error
    customer_sends = [s for s in group_sends if s["is_group"]]
    assert len(customer_sends) == 1
    assert customer_sends[0]["phone"] == group_id


async def test_consult_from_group_without_isGroup_flag_uses_author(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())
    sent = {}

    async def _send(self, recipient, message):
        sent["to"] = recipient

    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    group_id = "120363428616636917"
    author = "5925555555"
    visitor = _Ctx(group_id, channel="whatsapp")
    visitor.data = {
        "whatsapp_payload": {
            "isGroup": False,
            "sender": group_id,
            "author": author,
        }
    }
    with bind_dispatch_context(visitor):
        result = await tools["handoff__consult"].call(message="Stock on item X?")
    assert not result.is_error
    assert action.pending_questions[0]["user_contact"] == author
    assert sent["to"] == _STAFF_PHONE


async def test_consult_from_group_lid_author_resolves_via_api(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())
    sent = {}

    class _API:
        async def convert_lid_to_phone_number(self, lid):
            assert lid.endswith("@lid")
            return "5926666666"

    class _WA:
        async def api(self):
            return _API()

    class _Agent:
        id = "n.Agent.test"

        async def get_action_by_type(self, name):
            assert name == "WhatsAppAction"
            return _WA()

    async def _get_agent(self):
        return _Agent()

    async def _send(self, recipient, message):
        sent["to"] = recipient

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    group_id = "120363428616636917"
    visitor = _Ctx(group_id, channel="whatsapp")
    visitor.data = {
        "whatsapp_payload": {
            "isGroup": True,
            "sender": group_id,
            "author": "1234567890123456",
        }
    }
    with bind_dispatch_context(visitor):
        result = await tools["handoff__consult"].call(message="Need a quote.")
    assert not result.is_error
    assert action.pending_questions[0]["user_contact"] == "5926666666"
    assert sent["to"] == _STAFF_PHONE


async def test_save_answer_dms_group_author_not_group_chat_id(monkeypatch):
    _install_aca_staff(monkeypatch)
    _install_pending_store(monkeypatch)
    action = _bind_action(HandoffAction())
    customer_sends = []

    class _Agent:
        id = "n.Agent.test"

        async def get_action_by_type(self, name):
            return None

    async def _get_agent(self):
        return _Agent()

    async def _send_whatsapp(self, recipient, message):
        if recipient != _STAFF_PHONE:
            customer_sends.append(recipient)

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send_whatsapp)

    async def _customer_reply(self, q, a):
        return f"Answer: {a}"

    monkeypatch.setattr(HandoffAction, "_customer_reply", _customer_reply)

    async def _append_and_ingest_stub(self, question, answer):
        return question, answer, "n.DocumentNode.test"

    monkeypatch.setattr(HandoffAction, "_append_and_ingest", _append_and_ingest_stub)
    tools = {t.name: t for t in await action.get_tools()}
    group_id = "120363428616636917"
    author = "5923333333"
    visitor = _Ctx(group_id, channel="whatsapp")
    visitor.data = {
        "whatsapp_payload": {
            "isGroup": True,
            "sender": group_id,
            "author": author,
        }
    }
    with bind_dispatch_context(visitor):
        await tools["handoff__consult"].call(message="Do you deliver to Bartica?")
    qid = action.pending_questions[0]["id"]
    with bind_dispatch_context(_Ctx(_STAFF_PHONE)):
        saved = await tools["handoff__save_answer"].call(
            question_ids=[qid], answer="Yes, we deliver there."
        )
    assert not saved.is_error
    assert customer_sends == [author]


async def test_observe_enrolls_group_numbers_and_does_not_reply(monkeypatch):
    enrolled = {}

    class _ACA:
        async def add_users_to_group(self, group, user_ids, action_label="default"):
            enrolled["group"] = group
            enrolled["users"] = list(user_ids)
            enrolled["label"] = action_label

        async def has_tool_access(self, user_id, tool_name, channel="default"):
            return True

    class _API:
        async def group_members(self, group_id):
            assert group_id == "120363@g.us"
            return {
                "status": "success",
                "response": [
                    {"id": {"user": "5921111111"}, "formattedName": "Ada"},
                    {"id": {"user": "5922222222"}, "formattedName": "Ben"},
                    {"id": {"user": "5920000000"}, "formattedName": "You"},
                ],
            }

    class _WA:
        async def api(self):
            return _API()

    class _Agent:
        id = "n.Agent.test"

        async def get_action_by_type(self, name):
            assert name == "WhatsAppAction"
            return _WA()

    action = HandoffAction()
    action.mode = "observe"
    sent = []

    async def _get_agent(self):
        return _Agent()

    async def _get_action(self, name, *args, **kwargs):
        assert name == "AccessControlAction"
        return _ACA()

    async def _append(self, question, answer):
        sent.append((question, answer))
        return question, answer, "n.DocumentNode.fact"

    async def _send(self, recipient, message):
        sent.append(("whatsapp", recipient, message))

    monkeypatch.setattr(HandoffAction, "get_agent", _get_agent)
    monkeypatch.setattr(HandoffAction, "get_action", _get_action)
    monkeypatch.setattr(HandoffAction, "_append_and_ingest", _append)
    monkeypatch.setattr(HandoffAction, "_send_whatsapp", _send)
    tools = {t.name: t for t in await action.get_tools()}
    visitor = _Ctx("120363@g.us", channel="whatsapp")
    visitor.data = {"whatsapp_payload": {"isGroup": True, "sender": "120363@g.us"}}
    with bind_dispatch_context(visitor):
        result = await tools["handoff__observe"].call(
            fact="The shop closes at 5pm on Fridays."
        )
    assert not result.is_error
    assert "Inform them in a simple note" in result.content
    assert enrolled == {
        "group": "staff",
        "users": ["5921111111", "5922222222"],
        "label": "HandoffAction",
    }
    assert sent == [
        ("The shop closes at 5pm on Fridays.", "The shop closes at 5pm on Fridays.")
    ]


async def test_observe_mode_admits_group_messages_without_a_mention():
    from jvagent.action.whatsapp.utils.endpoint_helpers import is_directed_message

    class _HookAction:
        def whatsapp_direct_all_group_messages(self):
            return True

    class _ActionsMgr:
        async def get_all_actions(self, enabled_only=True):
            return [_HookAction()]

    class _Agent:
        async def get_actions_manager(self):
            return _ActionsMgr()

    class _WA:
        async def get_agent(self):
            return _Agent()

    data = SimpleNamespace(isGroup=True, body="the shop closes at 5", caption="")
    assert await is_directed_message(_WA(), data) is True


def _tool_permissions(extra_tools=None):
    tools = {
        "handoff__save_answer": {
            "deny": [],
            "allow": [{"group": "staff", "enabled": True}],
        },
        "handoff__update_chunk": {
            "deny": [],
            "allow": [{"group": "staff", "enabled": True}],
        },
        "handoff__observe": {
            "deny": [],
            "allow": [{"group": "staff", "enabled": True}],
        },
    }
    if extra_tools:
        tools.update(extra_tools)
    return {
        "whatsapp": {
            "any": {"deny": [], "allow": [{"group": "all", "enabled": True}]},
            "tools": tools,
        }
    }


def _aca_for_tools(members=None, permissions=None):
    from jvagent.action.access_control.access_control_action import (
        AccessControlAction,
    )

    aca = AccessControlAction(
        permissions=permissions if permissions is not None else _tool_permissions(),
        user_groups={"HandoffAction": {"staff": list(members or [_STAFF_PHONE])}},
        enforce=True,
        default_deny=False,
    )
    aca.enabled = True
    return aca


def _agent_for_aca(aca):
    class _Agent:
        async def get_access_control_action(self):
            return aca

    return _Agent()


async def _run_gated_tool(tool, *, aca, user_id, channel="whatsapp", **call_kwargs):
    """Invoke a tool through orchestrator permission wrap (production path)."""
    from jvagent.action.orchestrator.tools import wrap_action_tool
    from jvagent.tooling.tool_result import ToolResult

    wrapped = wrap_action_tool(
        tool,
        agent=_agent_for_aca(aca),
        user_id=user_id,
        channel=channel,
    )
    return ToolResult(content=await wrapped.run(call_kwargs))


async def test_has_tool_access_allow_and_deny():
    aca = _aca_for_tools()
    assert await aca.has_tool_access(_STAFF_PHONE, "handoff__save_answer", "whatsapp")
    assert await aca.has_tool_access(_STAFF_PHONE, "handoff__update_chunk", "whatsapp")
    assert await aca.has_tool_access(_STAFF_PHONE, "handoff__observe", "whatsapp")
    assert not await aca.has_tool_access(
        "9999999999", "handoff__save_answer", "whatsapp"
    )
    assert not await aca.has_tool_access(
        _STAFF_PHONE, "handoff__save_answer", "default"
    )


async def test_gated_tools_leave_the_visible_set_for_a_non_staff_sender():
    from jvagent.action.orchestrator.access import drop_unpermitted_tools

    aca = _aca_for_tools()
    names = [
        "handoff__consult",
        "handoff__save_answer",
        "handoff__update_chunk",
    ]

    customer_tools = {name: object() for name in names}
    customer_visible = set(names)
    await drop_unpermitted_tools(
        aca,
        user_id="9999999999",
        channel="whatsapp",
        tools=customer_tools,
        visible=customer_visible,
    )
    assert "handoff__save_answer" not in customer_tools
    assert "handoff__save_answer" not in customer_visible
    assert "handoff__update_chunk" not in customer_visible
    assert "handoff__consult" in customer_visible

    staff_tools = {name: object() for name in names}
    staff_visible = set(names)
    await drop_unpermitted_tools(
        aca,
        user_id=_STAFF_PHONE,
        channel="whatsapp",
        tools=staff_tools,
        visible=staff_visible,
    )
    assert "handoff__save_answer" in staff_visible
    assert "handoff__update_chunk" in staff_tools


def _consult_matrix():
    staff_only = {"deny": [], "allow": [{"group": "staff", "enabled": True}]}
    tools = {
        "handoff__consult": {
            "deny": [{"group": "staff", "enabled": True}],
            "allow": [{"group": "all", "enabled": True}],
        },
        "handoff__save_answer": staff_only,
        "handoff__update_chunk": staff_only,
    }
    return {
        "whatsapp": {
            "any": {"deny": [], "allow": [{"group": "all", "enabled": True}]},
            "tools": tools,
        }
    }


async def test_consult_tools_split_by_sender():
    from jvagent.action.orchestrator.access import drop_unpermitted_tools

    aca = _aca_for_tools(permissions=_consult_matrix())
    names = [
        "handoff__consult",
        "handoff__save_answer",
        "handoff__update_chunk",
        "pageindex__search",
    ]
    staff_only = {
        "handoff__save_answer",
        "handoff__update_chunk",
    }

    customer_tools = {name: object() for name in names}
    customer_visible = set(names)
    await drop_unpermitted_tools(
        aca,
        user_id="9999999999",
        channel="whatsapp",
        tools=customer_tools,
        visible=customer_visible,
    )
    assert customer_visible & staff_only == set()
    assert "handoff__consult" in customer_visible
    assert "pageindex__search" in customer_tools

    staff_tools = {name: object() for name in names}
    staff_visible = set(names)
    await drop_unpermitted_tools(
        aca,
        user_id=_STAFF_PHONE,
        channel="whatsapp",
        tools=staff_tools,
        visible=staff_visible,
    )
    assert (
        staff_visible
        & {
            "handoff__save_answer",
            "handoff__update_chunk",
        }
        == staff_only
    )
    assert "handoff__consult" not in staff_tools
    assert "handoff__consult" not in staff_visible
    assert "pageindex__search" in staff_visible


async def test_has_tool_access_missing_entry_denies_even_when_any_allows():
    aca = _aca_for_tools(
        permissions={
            "whatsapp": {
                "any": {"deny": [], "allow": [{"group": "all", "enabled": True}]},
            }
        }
    )
    assert not await aca.has_tool_access(
        _STAFF_PHONE, "handoff__save_answer", "whatsapp"
    )


async def test_denied_save_is_silent_and_writes_nothing(monkeypatch):
    from jvagent.action.handoff_action.handoff_action import SAVE_DENIED_LINE

    _install_pending_store(monkeypatch)
    aca = _aca_for_tools()
    action = _bind_action(HandoffAction())
    _seed_pending(action, id="q1", question="Do you offer delivery?")
    ingested = {}

    async def _append(self, question, answer):
        ingested["hit"] = True
        return question, answer, "n.DocumentNode.test"

    monkeypatch.setattr(HandoffAction, "_append_and_ingest", _append)
    tools = {t.name: t for t in await action.get_tools()}
    with bind_dispatch_context(_Ctx("9999999999", channel="whatsapp")):
        denied = await _run_gated_tool(
            tools["handoff__save_answer"],
            aca=aca,
            user_id="9999999999",
            question_ids=["q1"],
            answer="Yes, we deliver.",
        )
    assert denied.content == SAVE_DENIED_LINE
    assert not denied.is_error
    assert "hit" not in ingested
    assert action.pending_questions[0]["id"] == "q1"

    action.mode = "observe"
    tools = {t.name: t for t in await action.get_tools()}
    with bind_dispatch_context(_Ctx("9999999999", channel="whatsapp")):
        observed = await _run_gated_tool(
            tools["handoff__observe"],
            aca=aca,
            user_id="9999999999",
            fact="We are on Water Street.",
        )
    assert observed.content == SAVE_DENIED_LINE
    assert "hit" not in ingested


async def test_wrap_denied_save_does_not_run_the_tool():
    from jvagent.action.handoff_action.handoff_action import SAVE_DENIED_LINE
    from jvagent.action.orchestrator.tools import wrap_action_tool
    from jvagent.tooling.tool import Tool

    called = {}

    async def _exec(**kwargs):
        called["yes"] = True
        return "saved"

    tool = Tool(
        name="handoff__save_answer",
        description="save",
        execute=_exec,
        requires_tool_permission=True,
        permission_denied_message=SAVE_DENIED_LINE,
    )
    wrapped = wrap_action_tool(tool, agent=None, user_id="999", channel="whatsapp")
    assert await wrapped.run({}) == SAVE_DENIED_LINE
    assert "yes" not in called
