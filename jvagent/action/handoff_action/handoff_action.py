"""Human handoff capability action.

One mode at a time (``HandoffAction.mode``). That mode is the only tool set
``get_tools()`` publishes. Pin those tools on the orchestrator — there is no
skill SOP.

- ``consult`` — ``handoff__consult``, ``handoff__save_answer``,
  ``handoff__update_chunk``. Ask staff, tell the user you will return, then save
  the answer and reply to the user. On a staff turn the current unanswered
  questions are injected into the orchestration prompt as a parameter (id and
  question only), so the staff sender needs no read tool to pick the ids.
- ``transfer`` — ``handoff__transfer``. Summarize for staff, tell the user a
  staff member will follow up; the conversation continues on later messages.
- ``observe`` — ``handoff__observe``. In a WhatsApp group, store a useful fact
  and add the other numbers to ``HandoffAction.staff``. Send nothing.

On channel ``web`` or ``default``, consult and transfer ask for one WhatsApp
number or email. On WhatsApp they use the sender. Staff targets are
AccessControlAction ``HandoffAction.staff``.
"""

import json
import logging
import os
import random
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, ClassVar, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.harness.contracts import IdempotencyClass
from jvagent.tooling.tool_decorator import tool
from jvagent.tooling.tool_result import ToolResult

logger = logging.getLogger(__name__)


def _register_orchestrator_vocabulary() -> None:
    """Declare ``handoff__`` a trusted directive source.

    The handoff tools return ``response_directive`` guidance (the confirmation
    prompt, the relay lines). The orchestrator honors directives only from a
    registered first-party ``ns__`` namespace, so this must run before the first
    turn — mirroring ``InterviewAction``. Idempotent.
    """
    try:
        from jvagent.action.orchestrator.constants import (
            register_trusted_directive_prefix,
        )
    except Exception:  # pragma: no cover - orchestrator optional at load
        return
    register_trusted_directive_prefix("handoff__")


_register_orchestrator_vocabulary()


HANDOFF_INTRO = "Let me sort that out with our team."

TRANSFER_CLOSE = (
    "I've passed it to the team and a staff member will reach out to you shortly."
)

CONSULT_CLOSE = "I'll get back to you as soon as they respond."

HANDOFF_FOLLOWUP_THANKS = "Thanks."

HANDOFF_DOC_NAME = "handoff.md"
HANDOFF_DOC_ACCESS = "public"

#: Model-only. A denied save must not be relayed to the user.
SAVE_DENIED_LINE = (
    "This user cannot save answers. Do not tell the user. "
    "Send no message about permissions or saving."
)

_CHANNELS = ("whatsapp", "email")
_MODES = ("consult", "transfer", "observe")
_WEB_CHANNELS = frozenset({"", "web", "default"})
_MODE_TOOLS = {
    "consult": frozenset(
        {
            "handoff__consult",
            "handoff__save_answer",
            "handoff__update_chunk",
        }
    ),
    "transfer": frozenset({"handoff__transfer"}),
    "observe": frozenset({"handoff__observe"}),
}


_CUSTOMER_CONTACT_KINDS = frozenset({"phone", "email"})


def _normalized_customer_contact_kind(value: str) -> str:
    kind = (value or "phone").strip().lower()
    return kind if kind in _CUSTOMER_CONTACT_KINDS else "phone"


def _contact_label(kind: str) -> str:
    return "email address" if kind == "email" else "WhatsApp number"


# Shared consult + transfer customer escalation trigger (KB/tools gap and explicit ask).
_CUSTOMER_CANNOT_ANSWER_CONDITION = (
    "you cannot answer a customer question, or you cannot "
    "complete the request from the knowledge base and tools, or the "
    'request is outside what the store sells (a "do you sell X" '
    "question the knowledge base cannot answer) — a quote, stock "
    "confirmation, bulk or B2B order, an upset customer, or the user "
    "wants a person or wants something reported"
)


def _pending_questions_block(rows: Optional[List[Dict[str, Any]]]) -> str:
    """Compact JSON ``[{"id","question"}]`` for the staff-turn parameter."""
    compact = [
        {
            "id": _question_field(row, "id"),
            "question": _question_field(row, "question")[:300],
        }
        for row in (rows or [])
        if _question_field(row, "id")
    ]
    return json.dumps(compact, ensure_ascii=False)


def _parameters_for(
    mode: str,
    staff: bool = False,
    *,
    customer_contact_kind: str = "phone",
    pending_rows: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Orchestration parameter(s) for this mode and sender."""
    mode = mode if mode in _MODE_TOOLS else "consult"
    kind = _normalized_customer_contact_kind(customer_contact_kind)
    contact_phrase = _contact_label(kind)
    if mode == "consult" and staff:
        return [
            {
                "scope": "orchestration",
                "key": "handoff_consult_staff",
                "response": (
                    "The questions awaiting an answer are listed below. "
                    "Choose every id whose question this message answers "
                    "(several questions may share one answer) and call "
                    "handoff__save_answer with question_ids and the full answer "
                    "text. Do not call handoff__consult. "
                    "Call handoff__update_chunk only when an [EVENT] line says "
                    "Handoff chunk and the id starts with n.DocumentNode. A "
                    "corr- correlation id is not a question id or a chunk id. "
                    "If the list is empty, do not save. Reply in the "
                    "conversation. If a save tool says this user cannot save "
                    "answers, send nothing about permissions or saving. "
                    f"PENDING QUESTIONS: {_pending_questions_block(pending_rows)}"
                ),
            }
        ]
    if mode == "transfer" and staff:
        return [
            {
                "scope": "orchestration",
                "key": "handoff_transfer_staff",
                "response": (
                    "Do not call handoff__transfer to escalate a message that "
                    "needs no escalation; reply in the conversation when "
                    "appropriate."
                ),
            }
        ]
    if mode == "transfer":
        return [
            {
                "scope": "orchestration",
                "key": "handoff_transfer",
                "condition": _CUSTOMER_CANNOT_ANSWER_CONDITION,
                "response": (
                    "This rule overrides the active skill and any skill "
                    "procedure. Call handoff__transfer with a short summary of "
                    "the issue in message (what needs handling — "
                    "not Customer wants… placeholders). Do not reply in text or "
                    "ask for contact yourself — relay only the line the tool "
                    "returns. The tool resolves contact "
                    f"from WhatsApp identity, saved context, or user_id when it "
                    f"is a {contact_phrase}. Omit contact on WhatsApp. On web or "
                    "default, omit contact on the first call; when the user "
                    f"replies with their {contact_phrase}, call again with the "
                    "same message and contact. Never pass placeholders. On later "
                    "messages keep helping when you can; call handoff__transfer "
                    "again when another issue needs escalation."
                ),
            }
        ]
    if mode == "observe":
        return [
            {
                "scope": "orchestration",
                "key": "handoff_observe",
                "condition": (
                    "the latest WhatsApp group message contains a fact, policy, "
                    "or answer worth keeping"
                ),
                "response": (
                    "Observe mode only — not consult or transfer. Call "
                    "handoff__observe with that fact."
                ),
            }
        ]
    return [
        {
            "scope": "orchestration",
            "key": "handoff_consult",
            "condition": _CUSTOMER_CANNOT_ANSWER_CONDITION,
            "response": (
                "This rule overrides the active skill and any skill "
                "procedure. First route anything the other skills do not own "
                "to the faq fallback; escalate here only while it still cannot "
                "answer the question or complete the request. If the latest "
                "message is a question you cannot "
                "answer, or a request you cannot complete, call "
                "handoff__consult now. Do not reply in text. Do not ask "
                "permission. Relay only the line the tool returns. Sentence 1 "
                "must be the customer's words, kept short (drop fillers like "
                "can u check or again yourself) — e.g. where r u located or what "
                "is your address? Never Customer wants… or Customer asked… "
                "summaries or extra detail they did not say. Optional sentence "
                "2 is what was already tried. On contact follow-up repeat the "
                "same sentence 1 as the first consult; only contact changes. "
                "The tool resolves contact from channel identity and saved "
                "context; omit contact on WhatsApp. "
                f"On web or default, pass contact only after the tool asks and "
                f"the user gives their {contact_phrase} — never placeholders."
            ),
        }
    ]


#: Default (consult) parameter. Reads of ``HandoffAction.parameters`` follow
#: the active mode; see ``HandoffAction.__getattribute__``.
HANDOFF_PARAMETERS: List[Dict[str, Any]] = _parameters_for("consult")


def _question_field(question: Any, name: str) -> str:
    if isinstance(question, dict):
        return str(question.get(name) or "")
    return str(getattr(question, name, "") or "")


def _question_row(question: Any) -> Dict[str, Any]:
    """Normalize a pending question (dict or object) for the save flow."""
    if isinstance(question, dict):
        return {
            "id": str(question.get("id") or ""),
            "question": str(question.get("question") or ""),
            "user_channel": str(question.get("user_channel") or ""),
            "user_contact": str(question.get("user_contact") or ""),
            "created_at": str(question.get("created_at") or ""),
        }
    return {
        "id": str(getattr(question, "id", "") or ""),
        "question": str(getattr(question, "question", "") or ""),
        "user_channel": str(getattr(question, "user_channel", "") or ""),
        "user_contact": str(getattr(question, "user_contact", "") or ""),
        "created_at": str(getattr(question, "created_at", "") or ""),
    }


def _lookup_staff_message(message: str, tokens: List[str]) -> str:
    """Full staff text with the customer's contact removed (no bold)."""
    text = message or ""
    for token in tokens:
        if token:
            text = text.replace(token, "")
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        stripped = sentence.strip()
        if not stripped:
            continue
        residual = re.sub(
            r"(?i)\b(?:customer|provided|phone|number|email|address|contact)\b",
            " ",
            stripped,
        )
        if not re.sub(r"[^A-Za-z0-9]+", "", residual):
            continue
        kept.append(stripped)
    cleaned = " ".join(kept).strip()
    if not cleaned:
        cleaned = re.sub(r"\s{2,}", " ", text).strip(" :.")
    return cleaned


_HANDLING_NOTE_RE = re.compile(
    r"(?i)\b(?:"
    r"no information found|"
    r"faq search found nothing|"
    r"search returned nothing|"
    r"nothing (?:useful |relevant )?found|"
    r"could not (?:find|answer)|"
    r"outside (?:the )?(?:assortment|catalog)|"
    r"what was already tried"
    r")\b"
)

_ASK_PREFIX_RE = re.compile(
    r"(?i)^(Customer asked\s+(?:if|for|about|whether|to)\s+)(.+?)([.!?]?)\s*$"
)


def _split_message_sentences(message: str) -> List[str]:
    return [
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+|\n+", (message or "").strip())
        if part.strip()
    ]


def _pending_question_text(message: str) -> str:
    """Natural customer ask only — first non-handling sentence."""
    sentences = _split_message_sentences(message)
    if not sentences:
        return (message or "").strip()
    for sentence in sentences:
        if _HANDLING_NOTE_RE.search(sentence):
            continue
        return sentence
    return sentences[0]


def _looks_like_staff_summary(message: str) -> bool:
    """Third-person consult summary from the model (routing only, not rewriting)."""
    return (message or "").strip().lower().startswith("customer ")


def _is_contact_only_message(
    message: str,
    contact: str,
    *,
    customer_contact_kind: str = "phone",
) -> bool:
    """True when message is only a phone/email (contact follow-up turn)."""
    msg = (message or "").strip()
    token = (contact or "").strip()
    if not msg:
        return bool(token)
    if token and msg == token:
        return True
    clean_msg = _sanitize_contact(msg, customer_contact_kind=customer_contact_kind)
    if token and clean_msg and clean_msg == token:
        return True
    if clean_msg and _contact_kind(clean_msg) and not token:
        return True
    stripped = _lookup_staff_message(msg, [token] if token else [])
    residual = re.sub(r"[^A-Za-z0-9]+", "", stripped or "")
    return not residual


def _consult_issue_text(message: str, contact: str = "") -> str:
    """Pending/staff question line from one consult message."""
    tokens = [contact] if (contact or "").strip() else []
    cleaned = _lookup_staff_message(message, tokens)
    return (
        _pending_question_text(cleaned)
        or _pending_question_text(message)
        or (message or "").strip()
    )


def _staff_notes_text(message: str, question: str) -> str:
    """Handling notes after the stored question (contact-stripped later)."""
    sentences = _split_message_sentences(message)
    if not sentences:
        return ""
    q = (question or "").strip()
    notes = [s for s in sentences if s != q]
    return " ".join(notes).strip()


def _bold_whatsapp_ask(question: str) -> str:
    """Wrap the ask core in WhatsApp bold (*...*)."""
    text = (question or "").strip()
    if not text or "*" in text:
        return text
    match = _ASK_PREFIX_RE.match(text)
    if not match:
        return text
    prefix, core, punct = match.group(1), match.group(2).strip(), match.group(3) or ""
    if not core:
        return text
    return f"{prefix}*{core}*{punct}"


def _staff_contact_suffix(token: str) -> str:
    """Staff notify line for customer contact or group thread id."""
    if _contact_kind(token) == "whatsapp_group":
        return f"Group: {token}"
    return f"Contact: {token}"


def _staff_outbound(mode: str, message: str, contact: str = "") -> str:
    """Staff text: consult question + optional contact; transfer includes contact."""
    token = (contact or "").strip()
    tokens = [token] if token and token != "declined" else []
    if mode == "consult":
        cleaned = _lookup_staff_message(message, tokens)
        question = _pending_question_text(cleaned)
        notes = _staff_notes_text(cleaned, question)
        bolded = _bold_whatsapp_ask(question)
        body = f"{bolded} {notes}".strip() if notes else bolded
        if token and token != "declined" and token not in body:
            body = f"{body}\n{_staff_contact_suffix(token)}".strip()
        return body
    text = (message or "").strip()
    if token and token not in text:
        suffix = _staff_contact_suffix(token)
        if suffix not in text:
            text = f"{text}\n{suffix}".strip()
    return text


def _one_contact(contact: Optional[str]) -> str:
    """Normalize one customer contact (phone or email) to a stripped string.

    Tolerates a list for backward compatibility, but the customer provides a
    single contact; only the first non-empty entry is kept.
    """
    if contact is None:
        return ""
    if isinstance(contact, (list, tuple)):
        for item in contact:
            value = str(item).strip()
            if value:
                return value
        return ""
    return str(contact).strip()


def _dispatch_user_id() -> str:
    from jvagent.tooling.tool_executor import get_dispatch_context

    ctx = get_dispatch_context()
    return (getattr(ctx, "user_id", "") or "").strip()


def _contact_kind(value: str) -> str:
    """``whatsapp`` / ``whatsapp_group`` / ``email``, else empty."""
    from jvagent.action.whatsapp.utils.chat_ids import (
        is_valid_whatsapp_phone,
        is_whatsapp_group_chat_id,
    )

    text = (value or "").strip()
    if not text or text.lower() == "declined":
        return ""
    if (
        "@" in text
        and " " not in text
        and not text.endswith(("@g.us", "@c.us", "@lid"))
        and not is_whatsapp_group_chat_id(text)
    ):
        return "email"
    if is_whatsapp_group_chat_id(text):
        return "whatsapp_group"
    if is_valid_whatsapp_phone(text):
        return "whatsapp"
    return ""


def _sender_contact(channel: str) -> str:
    """Dispatch user id when it is already a phone or email for this channel."""
    from jvagent.action.whatsapp.utils.chat_ids import (
        is_valid_whatsapp_phone,
        is_whatsapp_group_chat_id,
        participant_phone_from_payload,
        whatsapp_payload_from_visitor_data,
    )
    from jvagent.tooling.tool_executor import get_tool_visitor

    user_id = _dispatch_user_id()
    if not user_id:
        return ""
    if channel == "email":
        return user_id if "@" in user_id and " " not in user_id else ""
    visitor = get_tool_visitor()
    data = getattr(visitor, "data", None) if visitor else None
    payload = whatsapp_payload_from_visitor_data(data)
    author = participant_phone_from_payload(payload, user_id)
    if author:
        return author
    if is_whatsapp_group_chat_id(user_id):
        return ""
    if is_valid_whatsapp_phone(user_id):
        return user_id
    return ""


def _dispatch_channel() -> str:
    """Visitor channel for this tool call (``""`` when unbound)."""
    from jvagent.tooling.tool_executor import get_dispatch_context

    ctx = get_dispatch_context()
    return (getattr(ctx, "channel", "") or "").strip().lower()


def _asks_for_contact(channel: str) -> bool:
    """Web and default have no sender phone, so the user must give one."""
    return (channel or "").strip().lower() in _WEB_CHANNELS


_CONTACT_PLACEHOLDERS = frozenset(
    {
        "not provided",
        "unknown",
        "n/a",
        "na",
        "none",
        "no contact",
        "unavailable",
        "not available",
        "missing",
    }
)


def _contact_matches_kind(value: str, kind: str) -> bool:
    ck = _contact_kind((value or "").strip())
    normalized = _normalized_customer_contact_kind(kind)
    if normalized == "email":
        return ck == "email"
    return ck in ("whatsapp", "whatsapp_group")


def _group_dispatch_contact(payload: Optional[Dict[str, Any]], user_id: str) -> str:
    """Group chat id from dispatch user_id when this turn is a group thread."""
    from jvagent.action.whatsapp.utils.chat_ids import (
        is_group_whatsapp_turn,
        is_whatsapp_group_chat_id,
        strip_whatsapp_suffix,
    )

    uid = str(user_id or "").strip()
    if not uid or not is_whatsapp_group_chat_id(uid):
        return ""
    if not is_group_whatsapp_turn(payload, uid):
        return ""
    normalized = strip_whatsapp_suffix(uid)
    if _contact_kind(normalized) != "whatsapp_group":
        return ""
    return normalized


def _sanitize_contact(
    raw: Optional[str], *, customer_contact_kind: str = "phone"
) -> str:
    """Normalize tool or saved contact; drop placeholders and wrong kind."""
    from jvagent.action.whatsapp.utils.chat_ids import strip_whatsapp_suffix

    text = _one_contact(raw)
    if not text:
        return ""
    if text.strip().lower() in _CONTACT_PLACEHOLDERS:
        return ""
    if not _contact_matches_kind(text, customer_contact_kind):
        return ""
    if _contact_kind(text) == "whatsapp_group":
        return strip_whatsapp_suffix(text)
    return text


def _identity_contact(
    visitor_channel: str, *, customer_contact_kind: str = "phone"
) -> str:
    """Phone or email from dispatch user_id for this visitor channel."""
    kind = _normalized_customer_contact_kind(customer_contact_kind)
    ch = (visitor_channel or "").strip().lower()
    if ch == "email":
        return _sender_contact("email") if kind == "email" else ""
    if _asks_for_contact(ch):
        if kind == "email":
            return _sender_contact("email")
        return _sender_contact("whatsapp")
    return _sender_contact("whatsapp")


def _resolve_customer_contact(
    *,
    provided: Optional[str],
    saved: Optional[str],
    visitor_channel: str,
    customer_contact_kind: str = "phone",
) -> str:
    """Provided, then saved, then identity — all sanitized for the configured kind."""
    kind = _normalized_customer_contact_kind(customer_contact_kind)
    for candidate in (
        _sanitize_contact(provided, customer_contact_kind=kind),
        _sanitize_contact(saved, customer_contact_kind=kind),
        _identity_contact(visitor_channel, customer_contact_kind=kind),
    ):
        if candidate:
            return candidate
    return ""


def _handoff_relay_steering(
    mode: str,
    topic: str,
    *,
    continuing_handoff: bool,
) -> str:
    """Model-facing steering for the completion relay (no finished sentence).

    The orchestrator/reply model voices this into a fresh, natural
    acknowledgment per request; the tool never hands back a canned line that
    would be echoed verbatim.
    """
    subject = (topic or "").strip() or "their request"
    if mode == "transfer":
        lead = (
            "Acknowledge what they asked in one natural sentence"
            f' (their request: "{subject}"), then say you have passed it to the '
            "team and a staff member will reach out."
        )
    else:
        lead = (
            "Acknowledge what they asked in one natural sentence"
            f' (their request: "{subject}"), then say or paraphrase: you are checking with the '
            "team on how to answer or move forward with your request and will get back to them once staff respond."
        )
    if continuing_handoff:
        lead = "Thank them briefly, then " + lead[0].lower() + lead[1:]
    return (
        f"{lead} Keep it to 1-2 short sentences; do not promise a phone call; "
        "do not add extra detail."
    )


def _join_handoff_parts(*parts: str) -> str:
    """Join non-empty relay fragments into one customer-facing paragraph."""
    cleaned = [(p or "").strip() for p in parts if (p or "").strip()]
    if not cleaned:
        return ""
    out = cleaned[0]
    for piece in cleaned[1:]:
        if not out.endswith((".", "!", "?")):
            out += "."
        out += " " + piece
    return out


def _handoff_contact_ask(mode: str, customer_contact_kind: str) -> str:
    """Ask for phone or email; no closing promise to staff (that is part 3)."""
    kind = _normalized_customer_contact_kind(customer_contact_kind)
    if kind == "email":
        need = "your email address"
        question = "What's the best email to reach you?"
    else:
        need = "your WhatsApp number"
        question = "What's the best number to reach you?"
    if mode == "consult":
        return f"I'll need {need} so I can get back to you. {question}"
    return (
        f"To pass this to our team, I need {need} so a staff member can follow up. "
        f"{question}"
    )


def _compose_handoff_relay(
    mode: str,
    customer_contact_kind: str,
    *,
    include_intro: bool,
    include_contact_ask: bool,
    include_close: bool,
    intro: Optional[str] = None,
    close: Optional[str] = None,
    followup_thanks: bool = False,
) -> str:
    """Build user relay: intro, optional contact ask, mode-specific close."""
    parts: List[str] = []
    if followup_thanks:
        parts.append(HANDOFF_FOLLOWUP_THANKS)
    if include_intro:
        parts.append((intro or HANDOFF_INTRO).strip())
    if include_contact_ask:
        parts.append(_handoff_contact_ask(mode, customer_contact_kind))
    if include_close:
        default_close = CONSULT_CLOSE if mode == "consult" else TRANSFER_CLOSE
        parts.append((close or default_close).strip())
    return _join_handoff_parts(*parts)


def _missing_contact_handoff_result(
    mode: str,
    customer_contact_kind: str,
    tool_name: str,
    *,
    intro: Optional[str] = None,
) -> ToolResult:
    """Relay natural ask line; internal note for the executive to recall the tool."""
    user_line = _compose_handoff_relay(
        mode,
        customer_contact_kind,
        include_intro=True,
        include_contact_ask=True,
        include_close=False,
        intro=intro,
    )
    label = _contact_label(_normalized_customer_contact_kind(customer_contact_kind))
    return ToolResult(
        content=(
            "Tell the user this in your own short, natural wording — do not add "
            "the staff summary:\n"
            f"{user_line}\n\n"
            "INTERNAL (do not say to the user): After they reply, call "
            f"{tool_name} again with the same message and contact set to their "
            f"{label}. Do not notify staff until then."
        )
    )


_HANDOFF_CONTACT_KEY = "handoff_contact"
_HANDOFF_WHATSAPP_AUTHOR_KEY = "handoff_whatsapp_author"
_HANDOFF_ACTIVE_MODE_KEY = "handoff_active_mode"
_HANDOFF_ACTIVE_ISSUE_KEY = "handoff_active_issue"


def _relay(line: str) -> ToolResult:
    return ToolResult(
        content=(
            "Tell the user this in your own short, natural wording — do not add "
            "the staff summary:\n"
            f"{line}"
        )
    )


def _pick_staff(targets: List[str]) -> str:
    if not targets:
        return ""
    return random.choice(targets)


_ANSWER_PREFIX = re.compile(
    r"^(?:save\s+answer|answer)\s*:\s*",
    re.IGNORECASE,
)
_SAVE_ANSWER_PREFIX = re.compile(r"^save\s+answer\s*:\s*", re.IGNORECASE)

_CONDENSE_SYSTEM = (
    "Rewrite a staff handoff into one short customer question and only the "
    "factual answer. Return JSON with keys question and answer. The question "
    "is what the customer asked, in one short sentence. The answer is only "
    "the fact staff gave, with no instructions to staff."
)


def _handoff_graph(pairs: List[tuple], collection_name: str) -> Dict[str, Any]:
    """PageIndex graph for handoff.md: one node per short Q&A."""
    root_id = f"n.DocumentRootNode.{uuid.uuid4().hex}"
    nodes = []
    edges = []
    edge_ids = []
    for index, (question, answer) in enumerate(pairs):
        node_id = f"n.DocumentNode.{uuid.uuid4().hex}"
        edge_id = f"e.DocumentContentEdge.{uuid.uuid4().hex}"
        section = f"## {question}\n\n{answer}"
        edge_ids.append(edge_id)
        nodes.append(
            {
                "id": node_id,
                "entity": "DocumentNode",
                "type_code": "n",
                "edge_ids": [edge_id],
                "title": question,
                "node_id": f"{index:04d}",
                "text": section,
                "summary": section,
                "prefix_summary": None,
                "physical_index": 1,
                "start_index": 1,
                "end_index": 1,
                "structure": "",
                "doc_name": HANDOFF_DOC_NAME,
                "collection_name": collection_name,
                "line_num": 1,
                "enabled": True,
                "content_type": "substantive",
                "hierarchy": [question],
            }
        )
        edges.append(
            {
                "id": edge_id,
                "entity": "DocumentContentEdge",
                "type_code": "e",
                "source": root_id,
                "target": node_id,
                "bidirectional": False,
            }
        )
    return {
        "roots": [
            {
                "id": root_id,
                "entity": "DocumentRootNode",
                "type_code": "n",
                "edge_ids": edge_ids,
                "doc_name": HANDOFF_DOC_NAME,
                "doc_description": None,
                "doc_url": None,
                "collection_name": collection_name,
                "metadata": {"access": HANDOFF_DOC_ACCESS},
                "chunks": len(nodes),
            }
        ],
        "nodes": nodes,
        "edges": edges,
    }


def _append_handoff_chunk(
    graph: Dict[str, Any], question: str, answer: str, collection_name: str
) -> Dict[str, Any]:
    """Add one Q&A chunk to an exported handoff graph.

    An export with no root starts a new graph. Existing chunk ids stay.
    """
    roots = list(graph.get("roots") or [])
    if not roots:
        return _handoff_graph([(question, answer)], collection_name)
    root = dict(roots[0])
    root_id = root.get("id") or f"n.DocumentRootNode.{uuid.uuid4().hex}"
    node_id = f"n.DocumentNode.{uuid.uuid4().hex}"
    edge_id = f"e.DocumentContentEdge.{uuid.uuid4().hex}"
    section = f"## {question}\n\n{answer}"
    nodes = list(graph.get("nodes") or [])
    edges = list(graph.get("edges") or [])
    nodes.append(
        {
            "id": node_id,
            "entity": "DocumentNode",
            "type_code": "n",
            "edge_ids": [edge_id],
            "title": question,
            "node_id": f"{len(nodes):04d}",
            "text": section,
            "summary": section,
            "prefix_summary": None,
            "physical_index": 1,
            "start_index": 1,
            "end_index": 1,
            "structure": "",
            "doc_name": HANDOFF_DOC_NAME,
            "collection_name": collection_name,
            "line_num": 1,
            "enabled": True,
            "content_type": "substantive",
            "hierarchy": [question],
        }
    )
    edges.append(
        {
            "id": edge_id,
            "entity": "DocumentContentEdge",
            "type_code": "e",
            "source": root_id,
            "target": node_id,
            "bidirectional": False,
        }
    )
    edge_ids = list(root.get("edge_ids") or [])
    edge_ids.append(edge_id)
    metadata = dict(root.get("metadata") or {})
    metadata.setdefault("access", HANDOFF_DOC_ACCESS)
    root.update(
        {
            "id": root_id,
            "edge_ids": edge_ids,
            "doc_name": HANDOFF_DOC_NAME,
            "collection_name": collection_name,
            "metadata": metadata,
            "chunks": len(nodes),
        }
    )
    return {"roots": [root, *roots[1:]], "nodes": nodes, "edges": edges}


def _extract_saved_answer(answer: str, utterance: str = "") -> str:
    """Strip a leading save instruction and keep the longer full answer.

    ``save answer:`` / ``answer:`` is removed from the tool argument. When the
    live utterance carries a longer ``save answer:`` payload, that payload is
    the text stored — a truncated argument must not drop the rest.
    """
    cleaned = _ANSWER_PREFIX.sub("", (answer or "").strip(), count=1).strip()
    raw_utterance = (utterance or "").strip()
    match = _SAVE_ANSWER_PREFIX.match(raw_utterance)
    if match:
        payload = raw_utterance[match.end() :].strip()
        if len(payload) > len(cleaned):
            return payload
    return cleaned


def _mask(value: str) -> str:
    """Mask an identifier for logs: keep only the last 4 characters."""
    s = str(value or "")
    if len(s) <= 4:
        return "***"
    return f"***{s[-4:]}"


def _truncate(value: Any, limit: int = 300) -> str:
    """Truncate a provider result for logging (never includes secrets)."""
    try:
        text = repr(value)
    except Exception:  # pragma: no cover - defensive
        return "<unrepr-able>"
    return text if len(text) <= limit else text[:limit] + "…"


def _jid_string_from_nested(value: Any) -> str:
    """Coerce webhook message id / author fields to a JID string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("_serialized", "user", "participant", "author"):
            part = value.get(key)
            if isinstance(part, str) and part.strip():
                return part.strip()
    return ""


def _participant_jids_from_whatsapp_payload(payload: Dict[str, Any]) -> List[str]:
    """Candidate participant JIDs from whatsapp_payload (handoff-local scan)."""
    from jvagent.action.whatsapp.utils.chat_ids import (
        is_whatsapp_group_chat_id,
        strip_whatsapp_suffix,
    )

    if not isinstance(payload, dict):
        return []
    seen: set[str] = set()
    out: List[str] = []

    def add(raw: Any, *, allow_long_id: bool = False) -> None:
        jid = _jid_string_from_nested(raw)
        if not jid or jid in seen:
            return
        cleaned = strip_whatsapp_suffix(jid)
        if "@g.us" in jid:
            return
        if not allow_long_id and is_whatsapp_group_chat_id(cleaned):
            return
        seen.add(jid)
        out.append(jid)

    add(payload.get("author"), allow_long_id=True)
    for key in ("participant", "authorId", "participantId"):
        add(payload.get(key))
    quoted = payload.get("quoted_message") or {}
    if isinstance(quoted, dict):
        add(quoted.get("author"))
        add(quoted.get("participant"))
    sender = str(payload.get("sender") or "").strip()
    if sender and not is_whatsapp_group_chat_id(sender):
        add(sender)
    for mid in payload.get("mentionedIds") or []:
        if isinstance(mid, str) and mid.strip():
            token = mid.split("@", 1)[0].strip() if "@" in mid else mid.strip()
            add(token)
    return out


def _author_from_get_message_response(result: Any) -> str:
    """Extract group participant JID from get_message_by_id API response."""
    if not isinstance(result, dict):
        return ""
    roots: List[Any] = [result]
    for key in ("message", "data", "response"):
        nested = result.get(key)
        if isinstance(nested, dict):
            roots.append(nested)
    for root in roots:
        if not isinstance(root, dict):
            continue
        msg = root.get("message") if isinstance(root.get("message"), dict) else root
        if not isinstance(msg, dict):
            continue
        for source in (msg.get("_data"), msg):
            if not isinstance(source, dict):
                continue
            jid = _jid_string_from_nested(source.get("author"))
            if jid:
                return jid
            msg_id = source.get("id")
            if isinstance(msg_id, dict):
                jid = _jid_string_from_nested(msg_id.get("participant"))
                if jid:
                    return jid
    return ""


def _handoff_whatsapp_context_snapshot(
    *,
    mode: str = "",
    visitor_channel: str = "",
    provided_clean: str = "",
    contact_after_sync: str = "",
    saved_contact: str = "",
    saved_whatsapp_author: str = "",
) -> str:
    """Compact masked snapshot for group WhatsApp handoff diagnostics."""
    from jvagent.action.whatsapp.utils.chat_ids import (
        is_group_whatsapp_turn,
        participant_phone_from_payload,
        raw_author_from_payload,
        whatsapp_payload_from_visitor_data,
    )
    from jvagent.tooling.tool_executor import get_tool_visitor

    user_id = _dispatch_user_id()
    channel = (visitor_channel or _dispatch_channel() or "").strip()
    visitor = get_tool_visitor()
    data = getattr(visitor, "data", None) if visitor else None
    payload = whatsapp_payload_from_visitor_data(data)
    payload_present = bool(
        isinstance(data, dict) and isinstance(data.get("whatsapp_payload"), dict)
    )
    raw_author = raw_author_from_payload(payload)
    participant = participant_phone_from_payload(payload, user_id)
    message_id = str(payload.get("message_id") or "").strip()
    payload_keys = ",".join(sorted(payload.keys())) if isinstance(payload, dict) else ""
    return (
        f"mode={mode or '?'} channel={channel or '?'} "
        f"user_id={_mask(user_id)} is_group_turn={is_group_whatsapp_turn(payload, user_id)} "
        f"payload_present={payload_present} isGroup={bool(payload.get('isGroup'))} "
        f"sender={_mask(str(payload.get('sender') or ''))} "
        f"author={_mask(raw_author)} author_len={len(raw_author)} "
        f"author_present={bool(raw_author)} "
        f"message_id_present={bool(message_id)} "
        f"payload_keys={payload_keys} "
        f"participant_from_payload={_mask(participant)} "
        f"provided_clean={_mask(provided_clean)} "
        f"contact_after_sync={_mask(contact_after_sync)} "
        f"saved_contact={_mask(saved_contact)} "
        f"saved_whatsapp_author={_mask(saved_whatsapp_author)}"
    )


def _handoff_json_path() -> Path:
    """``<files_root>/handoff.json`` (defaults to ``./.files/handoff.json``)."""
    from jvagent.core.sandbox import resolve_sandbox_root

    root = resolve_sandbox_root()
    path = Path(root) / "handoff.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _chunk_event(chunk_id: str, question: str) -> str:
    return f'Handoff chunk {chunk_id} "{question}".'


def _handoff_event(kind: str, detail: str = "", status: str = "completed") -> str:
    """One-line interaction event for a handoff lifecycle step."""
    detail = (detail or "").strip()
    if detail:
        return f'Handoff {status}: {kind} "{detail}".'
    if status == "started" and kind == "consult":
        return (
            "Handoff started: sending the customer's query to staff for consultation."
        )
    if status == "completed" and kind == "consult":
        return (
            "Handoff completed: the customer's query was sent to staff for "
            "consultation. That request is closed — do not re-handle or look "
            "up the previous request again unless user reask it again. Always run the matching skills/tools first to check for fresh data before falling back to handoff__consult; focus on the current request."
        )
    if status == "started" and kind == "transfer":
        return "Handoff started: forwarding the issue summary to staff."
    if status == "completed" and kind == "transfer":
        return (
            "Handoff completed: the issue summary was forwarded to staff. "
            "This request is closed — keep helping on later messages only."
        )
    return f"Handoff {status}: {kind}."


def _chunk_title(chunk_id: str) -> str:
    path = _handoff_json_path()
    if not chunk_id or not path.exists():
        return ""
    try:
        graph = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    for node in graph.get("nodes") or []:
        if isinstance(node, dict) and node.get("id") == chunk_id:
            return str(node.get("title") or "").strip()
    return ""


def _patch_handoff_json(chunk_id: str, title: str, section: str) -> None:
    path = _handoff_json_path()
    if not path.exists():
        return
    graph = json.loads(path.read_text(encoding="utf-8"))
    for node in graph.get("nodes") or []:
        if isinstance(node, dict) and node.get("id") == chunk_id:
            node["title"] = title
            node["text"] = section
            node["summary"] = section
            break
    path.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")


class HandoffAction(Action):
    """One handoff mode: consult, transfer, or observe."""

    tool_namespace: ClassVar[str] = "handoff"

    description: str = attribute(
        default=(
            "Human handoff. mode selects one tool set: consult (ask staff and "
            "reply later), transfer (escalate to staff per message), or "
            "observe (store WhatsApp group facts silently)."
        ),
        description="Action description",
    )

    mode: str = attribute(
        default="consult",
        description="Active handoff mode: consult | transfer | observe. Only one.",
    )

    parameters: List[Dict[str, Any]] = attribute(
        default_factory=list,
        description=(
            "Optional orchestration parameters from agent.yaml. A non-empty "
            "list is published as-is. An empty list uses the active mode's "
            "built-in parameter."
        ),
    )

    handoff_intro: str = attribute(
        default="",
        description=(
            "Optional fixed opening line for the handoff relay. When unset, the "
            "relay is generated from steering (see _handoff_relay_line)."
        ),
    )
    consult_close: str = attribute(
        default="",
        description=(
            "Optional fixed consult close. Set to force a deterministic literal "
            "relay; when unset, the reply is generated."
        ),
    )
    transfer_close: str = attribute(
        default="",
        description=(
            "Optional fixed transfer close. Set to force a deterministic literal "
            "relay; when unset, the reply is generated."
        ),
    )
    consult_prompt: str = attribute(
        default="",
        description="Deprecated yaml alias for consult_close when set.",
    )
    transfer_prompt: str = attribute(
        default="",
        description="Deprecated yaml alias for transfer_close when set.",
    )

    handoff_hours: str = attribute(
        default="Mon-Fri, 9:00 AM - 5:00 PM", description="Office hours phrase."
    )

    handoff_notify_action_type: str = attribute(
        default="WhatsAppAction", description="Action class for the whatsapp channel."
    )
    handoff_email_action_type: str = attribute(
        default="EmailAction", description="Action class for the email channel."
    )
    handoff_pageindex_action_type: str = attribute(
        default="PageIndexAction", description="Action class used to ingest Q&A."
    )

    handoff_channels: Dict[str, str] = attribute(
        default_factory=lambda: {
            "consult": "whatsapp",
            "transfer": "whatsapp",
        },
        description="Per-mode notify channel: whatsapp | email.",
    )

    customer_contact: str = attribute(
        default="phone",
        description=(
            "Which contact type to collect on web/default: phone (WhatsApp "
            "number) or email. Independent of handoff_channels (staff notify)."
        ),
    )

    pending_questions: List[Dict[str, Any]] = attribute(
        default_factory=list,
        description=(
            "Customer questions waiting for a staff answer. Source of truth for "
            "the staff-turn parameter and handoff__save_answer (reloaded from DB "
            "on read)."
        ),
    )

    # -- tools -----------------------------------------------------------------

    def __getattribute__(self, name: str) -> Any:
        """Non-empty stored ``parameters`` win; otherwise they follow ``mode``."""
        if name == "parameters":
            stored = super().__getattribute__("parameters")
            if stored:
                return stored
            try:
                mode = super().__getattribute__("mode")
            except Exception:
                mode = "consult"
            kind = _normalized_customer_contact_kind(
                str(super().__getattribute__("customer_contact") or "phone")
            )
            return _parameters_for(str(mode or "consult"), customer_contact_kind=kind)
        return super().__getattribute__(name)

    async def contributed_parameters(self, visitor: Any) -> List[Dict[str, Any]]:
        """Mode and staff rule for this sender. A yaml list replaces it."""
        stored = super().__getattribute__("parameters")
        if stored:
            return list(stored)
        user_id = str(getattr(visitor, "user_id", "") or "").strip()
        members = await self._aca_staff_members()
        is_staff = bool(user_id) and user_id in members
        pending_rows: Optional[List[Dict[str, Any]]] = None
        if is_staff and self._normalized_mode() == "consult":
            try:
                pending_rows = await self._list_pending()
            except Exception:
                logger.warning(
                    "handoff contributed_parameters: pending list unavailable",
                    exc_info=True,
                )
                pending_rows = []
        return _parameters_for(
            self._normalized_mode(),
            staff=is_staff,
            customer_contact_kind=self._normalized_customer_contact_kind(),
            pending_rows=pending_rows,
        )

    def _normalized_customer_contact_kind(self) -> str:
        return _normalized_customer_contact_kind(
            str(getattr(self, "customer_contact", None) or "phone")
        )

    def _normalized_mode(self) -> str:
        mode = str(self.mode or "consult").strip().lower()
        return mode if mode in _MODE_TOOLS else "consult"

    def whatsapp_direct_all_group_messages(self) -> bool:
        """WhatsApp ingress hook: in observe mode, treat every group message as directed."""
        return self._normalized_mode() == "observe"

    async def get_tools(self) -> List[Any]:
        """Publish only the active mode's tools."""
        from jvagent.tooling.tool_decorator import collect_tools

        allowed = _MODE_TOOLS[self._normalized_mode()]
        return [tool for tool in collect_tools(self) if tool.name in allowed]

    def get_capabilities(self) -> List[str]:
        if not self.enabled:
            return []
        mode = self._normalized_mode()
        if mode == "transfer":
            return ["Notify staff and tell the user a staff member will follow up"]
        if mode == "observe":
            return ["Store useful WhatsApp group facts without replying"]
        return ["Ask staff for an answer and reply to the user when it arrives"]

    @tool(name="handoff__consult", idempotency_class=IdempotencyClass.NON_RETRYABLE)
    async def consult(
        self,
        message: Annotated[
            str,
            "Sentence 1: What the customer needs, written as a complete, "
            "grammatically full sentence so staff grasp it immediately without "
            "reading the chat. Always name the specific item, matter, or "
            "question with full details (for a product, include its full name "
            "and code; for a question, state the full ask). Do NOT start with "
            "truncated action phrases or shorthand fragments. When the latest "
            "message is only a continuation (such as a phone number, "
            "acknowledgment, or confirmation), synthesize the full request from "
            "recent turns into a complete sentence. Optional sentence 2: What "
            "was already tried (staff only; never shown to the user).",
        ],
        contact: Annotated[
            Optional[str],
            "Customer phone or email. Omit on WhatsApp. Pass only when the "
            "user gives it after the tool asks. No placeholders.",
        ] = None,
        contact_declined: Annotated[
            Optional[bool],
            "True if the user refused to share the configured contact type.",
        ] = None,
    ) -> ToolResult:
        """Escalate one question or request you cannot answer or complete — including a request you have no tool to perform — to the team. Returns the single line to send."""
        return await self._dispatch_handoff(
            "consult",
            message,
            contact,
            contact_declined=bool(contact_declined),
        )

    @tool(name="handoff__transfer", idempotency_class=IdempotencyClass.NON_RETRYABLE)
    async def transfer(
        self,
        message: Annotated[
            str,
            "Sentence 1: What the customer needs handled, written as a "
            "complete, grammatically full sentence so staff grasp it "
            "immediately without reading the chat. Always name the specific "
            "item or matter and the desired outcome with full details. Do NOT "
            "start with truncated action phrases or shorthand fragments. When "
            "the latest message is only a continuation (such as a phone "
            "number, acknowledgment, or confirmation), synthesize the full "
            "request from recent turns into a complete sentence. Optional "
            "sentence 2: What was already tried (staff only; never shown to "
            "the user).",
        ],
        contact: Annotated[
            Optional[str],
            "Customer phone or email. Omit on WhatsApp. Pass only when the "
            "user gives it after the tool asks. No placeholders.",
        ] = None,
    ) -> ToolResult:
        """Escalate an issue the assistant cannot resolve to the team."""
        return await self._dispatch_handoff("transfer", message, contact)

    @tool(
        name="handoff__observe",
        idempotency_class=IdempotencyClass.NON_RETRYABLE,
        requires_tool_permission=True,
        permission_denied_message=SAVE_DENIED_LINE,
    )
    async def observe(
        self,
        fact: Annotated[
            str,
            "The useful fact, policy, or answer from the group message.",
        ],
    ) -> ToolResult:
        """Store one useful fact from this WhatsApp group in the knowledge base. Call only when the group message contains something worth keeping."""
        fact = (fact or "").strip()
        if not fact:
            return ToolResult(
                content="handoff failed: no fact to store.",
                is_error=True,
            )
        try:
            await self._enroll_group_staff()
        except Exception:
            logger.warning("handoff observe enroll failed", exc_info=True)
        try:
            await self._append_and_ingest(fact, fact)
        except Exception as exc:
            logger.error("handoff__observe ingest failed: %s", exc, exc_info=True)
            return ToolResult(
                content="handoff failed: could not save the fact to the knowledge base.",
                is_error=True,
            )
        await self._record_event(_handoff_event("observe"))
        return ToolResult(content=("The fact is stored. Inform them in a simple note."))

    def _effective_consult_close(self) -> str:
        legacy = (self.consult_prompt or "").strip()
        return legacy or (self.consult_close or "").strip()

    def _effective_transfer_close(self) -> str:
        legacy = (self.transfer_prompt or "").strip()
        return legacy or (self.transfer_close or "").strip()

    def _handoff_relay_line(
        self,
        mode: str,
        customer_contact_kind: str,
        *,
        continuing_handoff: bool,
        topic: str = "",
    ) -> str:
        intro = (self.handoff_intro or "").strip()
        close = (
            self._effective_consult_close()
            if mode == "consult"
            else self._effective_transfer_close()
        )
        # Deterministic escape hatch: an operator set handoff_intro / *_close in
        # agent.yaml, so relay the fixed literal line (old behavior).
        if intro or close:
            default_close = CONSULT_CLOSE if mode == "consult" else TRANSFER_CLOSE
            resolved_close = close or default_close
            followup_thanks = continuing_handoff
            if followup_thanks and resolved_close.strip().lower().startswith("thanks"):
                followup_thanks = False
            return _compose_handoff_relay(
                mode,
                customer_contact_kind,
                include_intro=not continuing_handoff,
                include_contact_ask=False,
                include_close=True,
                intro=intro or HANDOFF_INTRO,
                close=resolved_close,
                followup_thanks=followup_thanks,
            )
        # Dynamic path: hand back steering; the orchestrator model voices a fresh
        # acknowledgment for this request instead of echoing a canned sentence.
        return _handoff_relay_steering(
            mode,
            topic,
            continuing_handoff=continuing_handoff,
        )

    async def _dispatch_handoff(
        self,
        mode: str,
        message: str,
        contact: Optional[str],
        contact_declined: bool = False,
    ) -> ToolResult:
        channel = self._channel_for(mode)
        kind = self._normalized_customer_contact_kind()
        active = await self._active_mode()
        continuing_handoff = bool(active)
        if not active:
            from jvagent.tooling.tool_executor import get_tool_visitor

            visitor = get_tool_visitor()
            utterance = str(getattr(visitor, "utterance", "") or "").strip()
            issue_src = message
            used_utterance = bool(
                utterance
                and not _is_contact_only_message(
                    utterance, "", customer_contact_kind=kind
                )
            )
            if used_utterance:
                issue_src = utterance
            if used_utterance:
                issue = utterance
            else:
                issue = _consult_issue_text(issue_src) or (issue_src or "").strip()
            await self._set_active(mode, issue)
            await self._record_event(_handoff_event(mode, status="started"))
        saved = await self._saved_contact()
        saved_author = await self._saved_whatsapp_author()
        visitor_channel = _dispatch_channel()
        logger.warning(
            "handoff dispatch start mode=%s channel=%s continuing=%s snapshot=%s",
            mode,
            channel,
            continuing_handoff,
            _handoff_whatsapp_context_snapshot(
                mode=mode,
                visitor_channel=visitor_channel,
                saved_contact=saved,
                saved_whatsapp_author=saved_author,
            ),
        )
        provided_clean = _sanitize_contact(contact, customer_contact_kind=kind)
        if continuing_handoff and not provided_clean:
            from jvagent.tooling.tool_executor import get_tool_visitor

            visitor = get_tool_visitor()
            utterance = str(getattr(visitor, "utterance", "") or "").strip()
            if _is_contact_only_message(utterance, "", customer_contact_kind=kind):
                provided_clean = _sanitize_contact(
                    utterance, customer_contact_kind=kind
                )
            elif _is_contact_only_message(message, "", customer_contact_kind=kind):
                provided_clean = _sanitize_contact(message, customer_contact_kind=kind)
        if provided_clean:
            await self._save_contact(provided_clean)
        effective_provided = provided_clean if provided_clean else contact
        contact = _resolve_customer_contact(
            provided=effective_provided,
            saved=saved,
            visitor_channel=visitor_channel,
            customer_contact_kind=kind,
        )
        logger.warning(
            "handoff contact after sync resolve contact=%s provided=%s saved=%s "
            "channel=%s snapshot=%s",
            _mask(contact or ""),
            _mask(str(effective_provided or "")),
            _mask(saved),
            visitor_channel,
            _handoff_whatsapp_context_snapshot(
                mode=mode,
                visitor_channel=visitor_channel,
                provided_clean=provided_clean,
                contact_after_sync=contact or "",
                saved_contact=saved,
                saved_whatsapp_author=saved_author,
            ),
        )
        if (
            contact
            and mode in ("consult", "transfer")
            and not provided_clean
            and not saved
        ):
            from jvagent.action.whatsapp.utils.chat_ids import (
                participant_phone_from_payload,
                whatsapp_payload_from_visitor_data,
            )
            from jvagent.tooling.tool_executor import get_tool_visitor

            visitor = get_tool_visitor()
            payload = whatsapp_payload_from_visitor_data(
                getattr(visitor, "data", None) if visitor else None
            )
            if participant_phone_from_payload(payload, _dispatch_user_id()) == contact:
                await self._save_contact(contact)
                await self._update_context({_HANDOFF_WHATSAPP_AUTHOR_KEY: contact})
        if not contact and visitor_channel == "whatsapp":
            logger.warning(
                "handoff group participant resolve attempt snapshot=%s",
                _handoff_whatsapp_context_snapshot(
                    mode=mode,
                    visitor_channel=visitor_channel,
                    provided_clean=provided_clean,
                    contact_after_sync="",
                    saved_contact=saved,
                    saved_whatsapp_author=saved_author,
                ),
            )
            participant = await self._resolve_whatsapp_participant_contact()
            logger.warning(
                "handoff group participant resolve result contact=%s",
                _mask(participant or ""),
            )
            if participant:
                contact = participant
                await self._save_contact(contact)
                await self._update_context({_HANDOFF_WHATSAPP_AUTHOR_KEY: contact})
        if not contact and not (mode == "consult" and contact_declined):
            tool_name = f"handoff__{mode}"
            if mode == "consult":
                logger.warning(
                    "handoff consult ask path no contact declined=%s %s snapshot=%s",
                    contact_declined,
                    await self._pending_debug_ids(),
                    _handoff_whatsapp_context_snapshot(
                        mode=mode,
                        visitor_channel=visitor_channel,
                        provided_clean=provided_clean,
                        contact_after_sync="",
                        saved_contact=saved,
                        saved_whatsapp_author=saved_author,
                    ),
                )
            return _missing_contact_handoff_result(
                mode, kind, tool_name, intro=self.handoff_intro
            )
        relay_topic = (await self._active_issue()).strip() or (
            _consult_issue_text(message, contact or "") or (message or "").strip()
        )
        user_facing = self._handoff_relay_line(
            mode, kind, continuing_handoff=continuing_handoff, topic=relay_topic
        )

        targets = await self._staff_targets(channel)
        logger.warning(
            "handoff staff targets channel=%s count=%d targets=%s",
            channel,
            len(targets),
            [_mask(t) for t in targets],
        )
        if not targets:
            logger.error(
                "handoff %s: no staff targets for %s (AccessControlAction "
                "HandoffAction.staff)",
                mode,
                channel,
            )
            return ToolResult(
                content=(
                    f"handoff failed: no staff {channel} target configured. "
                    "Ask the operator to set AccessControlAction "
                    "HandoffAction.staff."
                ),
                is_error=True,
            )
        recipient = _pick_staff(targets)
        staff_message = message
        if mode == "consult":
            logger.warning(
                "handoff consult send path creating pending question "
                "contact=%r declined=%s %s",
                contact,
                contact_declined,
                await self._pending_debug_ids(),
            )
            recorded = (
                "declined" if contact_declined and not contact else (contact or "")
            )
            stored_issue = (await self._active_issue()).strip()
            issue_source = message
            if continuing_handoff and stored_issue:
                contact_only = _is_contact_only_message(
                    message,
                    contact or provided_clean,
                    customer_contact_kind=kind,
                )
                if contact_only or _looks_like_staff_summary(message):
                    issue_source = stored_issue
                    staff_message = stored_issue
            pending_q = _consult_issue_text(issue_source, contact or "")
            await self._add_pending(pending_q, recorded)
            if not (recorded or "").strip():
                logger.warning(
                    "handoff consult pending recorded contact empty on send path "
                    "contact=%s declined=%s",
                    _mask(contact or ""),
                    contact_declined,
                )
        outbound = _staff_outbound(
            mode,
            staff_message if mode == "consult" else message,
            contact or "",
        )

        try:
            if channel == "whatsapp":
                await self._send_whatsapp(recipient, outbound)
            else:
                await self._send_email(targets, outbound)
        except Exception as exc:
            logger.error("handoff %s dispatch failed: %s", mode, exc, exc_info=True)
            return ToolResult(
                content=(
                    "handoff failed: the notification could not be sent. "
                    "Apologize briefly."
                ),
                is_error=True,
            )

        logger.info(
            "handoff %s summary dispatched via %s to %s", mode, channel, recipient
        )
        if mode == "consult":
            logger.warning(
                "handoff consult dispatched staff recipient=%s pending_contact=%s",
                _mask(recipient),
                _mask(contact or ""),
            )
        await self._record_event(_handoff_event(mode))
        await self._clear_active()
        return _relay(user_facing)

    @tool(
        name="handoff__save_answer",
        idempotency_class=IdempotencyClass.NON_RETRYABLE,
        requires_tool_permission=True,
        permission_denied_message=SAVE_DENIED_LINE,
    )
    async def save_answer(
        self,
        question_ids: Annotated[
            List[str],
            "One or more pend_ ids from the pending questions list that this answer resolves.",
        ],
        answer: Annotated[str, "Full answer to store."],
    ) -> ToolResult:
        """Save the full answer for the chosen pend_ ids from the pending questions list, reply to them, and store it."""
        from jvagent.tooling.tool_executor import get_tool_visitor

        visitor = get_tool_visitor()
        utterance = str(getattr(visitor, "utterance", "") or "")
        cleaned = _extract_saved_answer(answer, utterance)
        if not cleaned:
            return ToolResult(
                content="handoff failed: no answer text to save.",
                is_error=True,
            )
        ordered_ids: List[str] = []
        seen: set[str] = set()
        for item in question_ids:
            qid = str(item or "").strip()
            if not qid or qid in seen:
                continue
            seen.add(qid)
            ordered_ids.append(qid)
        if not ordered_ids:
            return ToolResult(
                content="handoff failed: question_ids must include at least one pend_ id.",
                is_error=True,
            )
        group: List[Any] = []
        for qid in ordered_ids:
            row = await self._get_pending_question(qid)
            if row is None:
                return ToolResult(
                    content=f"handoff failed: no pending question with id {qid!r}",
                    is_error=True,
                )
            group.append(row)
        anchor = group[0]
        try:
            short_q, short_a, chunk_id = await self._append_and_ingest(
                _question_field(anchor, "question"), cleaned
            )
        except Exception as exc:
            logger.error("handoff__save_answer ingest failed: %s", exc, exc_info=True)
            return ToolResult(
                content=(
                    "handoff failed: could not save the answer to the knowledge "
                    "base. Report the issue to the team."
                ),
                is_error=True,
            )
        for row in group:
            rid = _question_field(row, "id")
            if rid:
                await self._remove_pending(rid, answer=cleaned, node=row)
        replied_contacts: set[str] = set()
        for row in group:
            contact = str(_question_field(row, "user_contact") or "").strip()
            if not contact or contact.lower() == "declined":
                continue
            if contact in replied_contacts:
                continue
            replied_contacts.add(contact)
            await self._reply_to_customer(row, short_q, short_a)
        await self._record_chunk_event(chunk_id, short_q)
        await self._record_event(_handoff_event("save_answer", short_q))
        await self._clear_active()
        return ToolResult(
            content=json.dumps(
                {
                    "response_directive": (
                        "Tell the user: Your answer is saved and will be "
                        "used in the future to answer this question."
                    )
                }
            )
        )

    @tool(
        name="handoff__update_chunk",
        idempotency_class=IdempotencyClass.NON_RETRYABLE,
        requires_tool_permission=True,
        permission_denied_message=SAVE_DENIED_LINE,
    )
    async def update_chunk(
        self,
        chunk_id: Annotated[
            str,
            "Chunk id from an event line that says Handoff chunk. Starts with n.DocumentNode. A corr- id is not a chunk id.",
        ],
        answer: Annotated[str, "Full answer to store."],
    ) -> ToolResult:
        """Update the stored answer for one existing handoff chunk, using the chunk id from a Handoff chunk event line."""
        from jvagent.tooling.tool_executor import get_tool_visitor

        visitor = get_tool_visitor()
        utterance = str(getattr(visitor, "utterance", "") or "")
        cleaned = _extract_saved_answer(answer, utterance)
        if not cleaned:
            return ToolResult(
                content="handoff failed: no answer text to save.",
                is_error=True,
            )
        chunk_id = (chunk_id or "").strip()
        title = _chunk_title(chunk_id)
        if not chunk_id or not title:
            return ToolResult(
                content=f"handoff failed: no handoff chunk with id {chunk_id!r}",
                is_error=True,
            )
        try:
            _, short_a = await self._condense_qa(title, cleaned)
            section = f"## {title}\n\n{short_a}"
            agent = await self.get_agent()
            collection = str(getattr(agent, "id", "") or "") or "default"
            from jvagent.action.pageindex.documents import update_document_chunk

            updated = await update_document_chunk(
                chunk_id,
                HANDOFF_DOC_NAME,
                collection,
                {"text": section, "summary": section},
            )
        except Exception as exc:
            logger.error("handoff__update_chunk failed: %s", exc, exc_info=True)
            return ToolResult(
                content=(
                    "handoff failed: could not save the answer to the knowledge "
                    "base. Report the issue to the team."
                ),
                is_error=True,
            )
        if not updated:
            return ToolResult(
                content=f"handoff failed: no handoff chunk with id {chunk_id!r}",
                is_error=True,
            )
        _patch_handoff_json(chunk_id, title, section)
        await self._record_chunk_event(chunk_id, title)
        await self._record_event(_handoff_event("update_chunk", title))
        await self._clear_active()
        return ToolResult(
            content=json.dumps(
                {
                    "response_directive": (
                        "Tell the user: Your answer is updated and will be "
                        "used in the future to answer this question."
                    )
                }
            )
        )

    # -- helpers ---------------------------------------------------------------

    async def _customer_reply(self, question: str, answer: str) -> str:
        """Remind the customer of their question, then give the answer."""
        from jvagent.action.utils.call_model import call_model

        fallback = f"You asked: {question}\n\n{answer}"
        try:
            result = await call_model(
                self,
                user_prompt=f"Question: {question}\nAnswer: {answer}",
                system_prompt=(
                    "Write a short message to the customer. Thank them for "
                    "their patience, remind them of the question they asked, "
                    "then give the answer. Plain text only. No mention of "
                    "staff, tools, or a knowledge base."
                ),
                json_response=False,
                use_history=False,
            )
        except Exception:
            logger.debug("handoff customer reply failed", exc_info=True)
            return fallback
        text = result.strip() if isinstance(result, str) else ""
        return text or fallback

    async def _reply_to_customer(
        self, question: Any, short_q: str, short_a: str
    ) -> None:
        contact = str(_question_field(question, "user_contact") or "").strip()
        if not contact or contact.lower() == "declined":
            return
        text = await self._customer_reply(short_q, short_a)
        kind = _contact_kind(contact)
        if not kind:
            return
        try:
            if kind == "email":
                await self._send_email(
                    [contact], text, subject=short_q or "Your question"
                )
            elif kind in ("whatsapp", "whatsapp_group"):
                await self._send_whatsapp_customer(contact, text)
            else:
                logger.warning(
                    "handoff reply to customer skipped unknown contact kind "
                    "contact=%r kind=%r",
                    contact,
                    kind,
                )
                return
        except Exception:
            logger.error(
                "handoff reply to customer failed contact=%r",
                contact,
                exc_info=True,
            )
            return
        await self._record_event("Handoff delivered the saved answer to the customer.")

    def _channel_for(self, mode: str) -> str:
        channel = (
            str((self.handoff_channels or {}).get(mode, "whatsapp")).strip().lower()
        )
        return channel if channel in _CHANNELS else "whatsapp"

    async def _aca_staff_members(self) -> List[str]:
        """Non-empty ``HandoffAction.staff`` from AccessControl, else []."""
        try:
            # Action.get_action looks up by class name; Agent.get_action is by label.
            aca: Any = await self.get_action("AccessControlAction")
        except Exception:
            return []
        if aca is None or not getattr(aca, "policy_applies", lambda: False)():
            return []
        try:
            groups = aca.get_user_groups(action_label="HandoffAction") or {}
        except Exception:
            return []
        staff = groups.get("staff") if isinstance(groups, dict) else None
        if not isinstance(staff, list):
            return []
        return [str(m).strip() for m in staff if str(m).strip()]

    async def _staff_targets(self, channel: str) -> List[str]:
        """Notify/contact targets from AccessControl ``HandoffAction.staff``.

        Members are classified with ``_contact_kind`` (phone → whatsapp,
        address → email).
        """
        staff = await self._aca_staff_members()
        return [m for m in staff if _contact_kind(m) == channel]

    async def _is_staff(self) -> bool:
        """True when the dispatch sender may save/update handoff answers.

        Sender must be listed in AccessControlAction ``HandoffAction.staff``.
        """
        from jvagent.tooling.tool_executor import get_dispatch_context

        ctx = get_dispatch_context()
        sender = (getattr(ctx, "user_id", "") or "").strip()
        if not sender:
            return False
        staff = await self._aca_staff_members()
        return sender in staff

    async def _participant_phone_from_jid(self, jid: str, api: Any) -> str:
        """Resolve a participant JID to a dialable phone (incl. LID conversion)."""
        from jvagent.action.whatsapp.utils.chat_ids import (
            is_valid_whatsapp_phone,
            lid_jid_for_conversion,
            strip_whatsapp_suffix,
        )

        raw = str(jid or "").strip()
        if not raw or "@g.us" in raw:
            return ""
        cleaned = strip_whatsapp_suffix(raw)
        if is_valid_whatsapp_phone(cleaned):
            return cleaned
        convert = getattr(api, "convert_lid_to_phone_number", None) if api else None
        if not callable(convert):
            return ""
        lid = lid_jid_for_conversion(raw if "@" in raw else cleaned)
        try:
            resolved = await convert(lid)
            resolved = strip_whatsapp_suffix(str(resolved or ""))
            if is_valid_whatsapp_phone(resolved):
                return resolved
        except Exception:
            logger.warning(
                "handoff participant: LID conversion failed for jid=%s",
                _mask(raw),
                exc_info=True,
            )
        return ""

    async def _resolve_whatsapp_participant_contact(self) -> str:
        """Group participant phone from payload (LID→phone) or saved context."""
        from jvagent.action.whatsapp.utils.chat_ids import (
            is_group_whatsapp_turn,
            participant_phone_from_payload,
            raw_author_from_payload,
            whatsapp_payload_from_visitor_data,
        )
        from jvagent.tooling.tool_executor import get_tool_visitor

        user_id = _dispatch_user_id()
        visitor = get_tool_visitor()
        payload = whatsapp_payload_from_visitor_data(
            getattr(visitor, "data", None) if visitor else None
        )
        if not is_group_whatsapp_turn(payload, user_id):
            logger.warning(
                "handoff participant: not a group turn user_id=%s isGroup=%s",
                _mask(user_id),
                bool(payload.get("isGroup")) if payload else False,
            )
            return ""

        agent = await self.get_agent()
        wa_action = None
        if agent is not None:
            get_by_type = getattr(agent, "get_action_by_type", None)
            if callable(get_by_type):
                wa_action = await get_by_type(self.handoff_notify_action_type)
        api = await wa_action.api() if wa_action else None

        phone = participant_phone_from_payload(payload, user_id)
        if phone:
            logger.warning(
                "handoff participant: from payload author=%s",
                _mask(phone),
            )
            return phone

        for jid in _participant_jids_from_whatsapp_payload(payload):
            phone = await self._participant_phone_from_jid(jid, api)
            if phone:
                logger.warning(
                    "handoff participant: from payload scan jid=%s phone=%s",
                    _mask(jid),
                    _mask(phone),
                )
                return phone

        raw_author = raw_author_from_payload(payload)
        if raw_author:
            phone = await self._participant_phone_from_jid(raw_author, api)
            if phone:
                logger.warning(
                    "handoff participant: from raw author phone=%s",
                    _mask(phone),
                )
                return phone

        message_id = str(payload.get("message_id") or "").strip()
        fetch = getattr(api, "get_message_by_id", None) if api else None
        if message_id and callable(fetch):
            try:
                result = await fetch(message_id)
                fetched_jid = _author_from_get_message_response(result)
                if fetched_jid:
                    phone = await self._participant_phone_from_jid(fetched_jid, api)
                    if phone:
                        logger.warning(
                            "handoff participant: fetched message_id=%s author_len=%d phone=%s",
                            _mask(message_id),
                            len(fetched_jid),
                            _mask(phone),
                        )
                        return phone
                logger.warning(
                    "handoff participant: get_message_by_id had no author message_id=%s",
                    _mask(message_id),
                )
            except Exception:
                logger.warning(
                    "handoff participant: get_message_by_id failed message_id=%s",
                    _mask(message_id),
                    exc_info=True,
                )
        elif message_id:
            logger.warning(
                "handoff participant: no get_message_by_id on API message_id=%s",
                _mask(message_id),
            )

        saved = _sanitize_contact(
            await self._saved_contact(), customer_contact_kind="phone"
        )
        if saved:
            logger.warning(
                "handoff participant: fallback handoff_contact=%s",
                _mask(saved),
            )
            return saved
        author_saved = _sanitize_contact(
            await self._saved_whatsapp_author(), customer_contact_kind="phone"
        )
        if author_saved:
            logger.warning(
                "handoff participant: fallback handoff_whatsapp_author=%s",
                _mask(author_saved),
            )
            return author_saved
        group_contact = _group_dispatch_contact(payload, user_id)
        if group_contact:
            logger.warning(
                "handoff participant: fallback group user_id=%s",
                _mask(group_contact),
            )
            return group_contact
        raw_author = raw_author_from_payload(payload)
        logger.warning(
            "handoff participant: unresolved empty author=%s snapshot=%s",
            _mask(raw_author),
            _handoff_whatsapp_context_snapshot(
                visitor_channel="whatsapp",
                saved_contact=await self._saved_contact(),
                saved_whatsapp_author=await self._saved_whatsapp_author(),
            ),
        )
        return ""

    async def _resolve_whatsapp_dm_recipient(self, recipient: str) -> str:
        """Normalize a DM target; reject group chat ids and resolve LIDs when possible."""
        from jvagent.action.whatsapp.utils.chat_ids import (
            is_valid_whatsapp_phone,
            is_whatsapp_group_chat_id,
            strip_whatsapp_suffix,
        )

        raw = str(recipient or "").strip()
        if not raw:
            return ""
        if is_whatsapp_group_chat_id(raw):
            logger.warning(
                "handoff whatsapp: refusing group chat id as DM recipient %s",
                _mask(raw),
            )
            return ""
        cleaned = strip_whatsapp_suffix(raw)
        if not is_valid_whatsapp_phone(cleaned):
            return ""
        agent = await self.get_agent()
        action = None
        if agent is not None:
            get_by_type = getattr(agent, "get_action_by_type", None)
            if callable(get_by_type):
                action = await get_by_type(self.handoff_notify_action_type)
        if action is None:
            return cleaned
        api = await action.api()
        if api is None:
            return cleaned
        if "@lid" in raw or raw.endswith("@lid"):
            convert = getattr(api, "convert_lid_to_phone_number", None)
            if callable(convert):
                try:
                    resolved = await convert(raw if "@" in raw else f"{cleaned}@lid")
                    resolved = strip_whatsapp_suffix(str(resolved or ""))
                    if is_valid_whatsapp_phone(resolved):
                        return resolved
                except Exception:
                    logger.debug(
                        "handoff whatsapp: LID conversion failed for %s",
                        _mask(raw),
                        exc_info=True,
                    )
        return cleaned

    async def _send_whatsapp_customer(self, recipient: str, message: str) -> None:
        """Deliver saved answer to customer DM or WhatsApp group thread."""
        from jvagent.action.whatsapp.utils.chat_ids import strip_whatsapp_suffix

        kind = _contact_kind(recipient)
        if kind == "whatsapp_group":
            group_id = strip_whatsapp_suffix(str(recipient or "").strip())
            logger.warning(
                "handoff whatsapp: sending group message to %s",
                _mask(group_id),
            )
            await self._execute_whatsapp_send(group_id, message or "", is_group=True)
            return
        if kind != "whatsapp":
            raise RuntimeError(
                f"whatsapp customer send refused invalid recipient {_mask(recipient)}"
            )
        await self._send_whatsapp(recipient, message)

    async def _send_whatsapp(self, recipient: str, message: str) -> None:
        dm_recipient = await self._resolve_whatsapp_dm_recipient(recipient)
        if not dm_recipient:
            raise RuntimeError(
                f"whatsapp send refused invalid recipient {_mask(recipient)}"
            )
        await self._execute_whatsapp_send(dm_recipient, message or "", is_group=False)

    async def _execute_whatsapp_send(
        self, recipient: str, message: str, *, is_group: bool
    ) -> None:
        masked = _mask(recipient)
        agent = await self.get_agent()
        logger.warning(
            "handoff whatsapp: start recipient=%s is_group=%s body_chars=%d agent=%s",
            masked,
            is_group,
            len(message or ""),
            getattr(agent, "id", None),
        )
        action = (
            await agent.get_action_by_type(self.handoff_notify_action_type)
            if agent
            else None
        )
        if action is None:
            logger.warning(
                "handoff whatsapp: notify action %r NOT FOUND on agent %s",
                self.handoff_notify_action_type,
                getattr(agent, "id", None),
            )
            raise RuntimeError(
                f"notify action {self.handoff_notify_action_type} not found"
            )
        configured = None
        try:
            configured = action.is_configured()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("handoff whatsapp: is_configured() raised: %s", exc)
        issues: List[str] = []
        try:
            issues = list(action._config_issues() or [])
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("handoff whatsapp: _config_issues() raised: %s", exc)
        logger.warning(
            "handoff whatsapp: action=%s provider=%s configured=%s "
            "phone_number_id=%s jvconnect_url=%s jvconnect_key=%s issues=%s",
            action.get_class_name(),
            getattr(action, "provider", None),
            configured,
            getattr(action, "phone_number_id", "") or "(unset)",
            bool(
                getattr(action, "jvconnect_url", "") or os.environ.get("JVCONNECT_URL")
            ),
            bool(os.environ.get("JVCONNECT_API_KEY")),
            issues or "(none)",
        )
        api = await action.api()
        if api is None:
            logger.warning("handoff whatsapp: action.api() returned None")
            raise RuntimeError("whatsapp api() returned None")
        logger.warning(
            "handoff whatsapp: sending via %s to %s is_group=%s",
            type(api).__name__,
            masked,
            is_group,
        )
        result = await api.send_message(
            phone=recipient, message=message or "", is_group=is_group
        )
        logger.warning(
            "handoff whatsapp: provider result ok=%s http_status=%s error=%s raw=%s",
            (result or {}).get("ok") if isinstance(result, dict) else None,
            (result or {}).get("http_status") if isinstance(result, dict) else None,
            (result or {}).get("error") if isinstance(result, dict) else None,
            _truncate(result),
        )
        if not isinstance(result, dict):
            logger.warning(
                "handoff whatsapp: provider returned non-dict result: %r", result
            )
            raise RuntimeError("whatsapp send returned a non-dict result")
        if result.get("ok") is False:
            raise RuntimeError(result.get("error") or "whatsapp send failed")
        logger.warning("handoff whatsapp: sent to %s", masked)

    async def _send_email(
        self,
        recipients: List[str],
        message: str,
        subject: str = "Human handoff request",
    ) -> None:
        from jvagent.action.email_action.email_payload import (
            CanonicalSendMessage,
            EmailRecipient,
        )

        agent = await self.get_agent()
        action = (
            await agent.get_action_by_type(self.handoff_email_action_type)
            if agent
            else None
        )
        if action is None:
            raise RuntimeError(
                f"email action {self.handoff_email_action_type} not found"
            )
        sender_email, sender_name = await action.resolve_outbound_sender()
        if not sender_email:
            raise RuntimeError("email action has no resolvable sender address")
        api = await action.api()
        if api is None:
            raise RuntimeError("email api() returned None")
        to_email = recipients[0]
        cc = [EmailRecipient(email=addr) for addr in recipients[1:]]
        result = await api.send_canonical(
            CanonicalSendMessage(
                to_email=to_email,
                subject=subject or "Human handoff request",
                sender_email=sender_email,
                sender_name=sender_name,
                text_content=message or "",
                cc=cc,
            )
        )
        if isinstance(result, dict) and result.get("ok") is False:
            raise RuntimeError(result.get("error") or "email send failed")

    async def _agent_id(self) -> str:
        agent = await self.get_agent()
        return str(
            getattr(agent, "id", "") or getattr(self, "agent_id", "") or ""
        ).strip()

    async def _evict_self_cache(self) -> None:
        """Drop entity cache so the next Action.get hits durable storage."""
        aid = str(getattr(self, "id", "") or "").strip()
        if not aid:
            return
        try:
            from jvspatial.core.context import get_default_context

            ctx = get_default_context()
            evict = getattr(ctx, "_evict_from_cache", None)
            if evict is not None:
                await evict(aid)
        except Exception:
            logger.warning(
                "handoff pending cache evict failed action_id=%s", aid, exc_info=True
            )

    async def _invalidate_pending_caches(self) -> None:
        await self._evict_self_cache()
        agent_id = await self._agent_id()
        if not agent_id:
            return
        try:
            from jvagent.core.cache import invalidate_action_cache

            await invalidate_action_cache(agent_id)
        except Exception:
            logger.warning(
                "handoff pending action-cache invalidate failed agent_id=%s",
                agent_id,
                exc_info=True,
            )

    async def _refresh_pending_state(self) -> None:
        """Reload pending_questions from durable Action storage onto this instance."""
        aid = str(getattr(self, "id", "") or "").strip()
        if not aid:
            return
        await self._evict_self_cache()
        try:
            fresh = await type(self).get(aid)
        except Exception:
            logger.warning(
                "handoff pending refresh failed action_id=%s", aid, exc_info=True
            )
            return
        if fresh is None:
            return
        rows = getattr(fresh, "pending_questions", None)
        self.pending_questions = list(rows) if isinstance(rows, list) else []

    def _unanswered_rows(self) -> List[Dict[str, Any]]:
        return [
            row
            for row in (self.pending_questions or [])
            if isinstance(row, dict) and not str(row.get("answer") or "").strip()
        ]

    async def _list_pending(self) -> List[Dict[str, Any]]:
        """Pending questions as dict rows for tool output."""
        await self._refresh_pending_state()
        loaded_id = await self._agent_id()
        rows = [_question_row(row) for row in self._unanswered_rows()]
        logger.warning(
            "handoff list_pending action_agent_id=%r loaded_agent_id=%r count=%s",
            getattr(self, "agent_id", None),
            loaded_id,
            len(rows),
        )
        return rows

    async def _get_pending_question(self, question_id: str) -> Any:
        qid = (question_id or "").strip()
        if not qid:
            return None
        await self._refresh_pending_state()
        for row in self._unanswered_rows():
            if str(row.get("id") or "") == qid:
                return row
        return None

    async def _persist_pending_rows(self, rows: List[Dict[str, Any]]) -> None:
        self.pending_questions = list(rows)
        await self.save()
        await self._invalidate_pending_caches()

    async def _add_pending(self, message: str, contact: str = "") -> Dict[str, Any]:
        """Append a pending question on this Action and verify it is listable."""
        from jvagent.tooling.tool_executor import get_dispatch_context

        ctx = get_dispatch_context()
        channel = (getattr(ctx, "channel", "") or "default").strip() or "default"
        stored_contact = (contact or "").strip()
        agent_id = await self._agent_id()
        logger.warning(
            "handoff add_pending action_agent_id=%r loaded_agent_id=%r contact=%r",
            getattr(self, "agent_id", None),
            agent_id,
            stored_contact,
        )
        try:
            await self._refresh_pending_state()
            entry = {
                "id": f"pend_{uuid.uuid4().hex}",
                "question": message or "",
                "user_channel": channel,
                "user_contact": stored_contact,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            rows = self._unanswered_rows()
            rows.append(entry)
            await self._persist_pending_rows(rows)
            await self._refresh_pending_state()
            if not any(
                str(row.get("id") or "") == entry["id"]
                for row in self._unanswered_rows()
            ):
                raise RuntimeError(
                    f"handoff question {entry['id']} was created but is not listable"
                )
        except Exception:
            logger.warning(
                "handoff add_pending failed action_agent_id=%r loaded_agent_id=%r",
                getattr(self, "agent_id", None),
                agent_id,
                exc_info=True,
            )
            raise
        logger.warning(
            "handoff add_pending created id=%s agent_id=%r",
            entry["id"],
            agent_id,
        )
        return entry

    async def _update_pending(
        self,
        question_id: str,
        *,
        user_contact: str = "",
        node: Any = None,
    ) -> None:
        """Update contact on one pending question (replaces with single contact)."""
        qid = (question_id or "").strip() or _question_field(node, "id").strip()
        if not qid:
            logger.warning("handoff update_pending missing question_id")
            return
        await self._refresh_pending_state()
        rows = self._unanswered_rows()
        changed = False
        stored = (user_contact or "").strip()
        for row in rows:
            if str(row.get("id") or "") == qid:
                row["user_contact"] = stored
                changed = True
                break
        if not changed:
            logger.warning("handoff update_pending missing question_id=%r", qid)
            return
        await self._persist_pending_rows(rows)

    async def _remove_pending(
        self, question_id: str, *, answer: str = "", node: Any = None
    ) -> None:
        """Drop a pending question from the Action list after it is answered."""
        qid = (question_id or "").strip() or _question_field(node, "id").strip()
        if not qid:
            logger.warning("handoff remove_pending missing question_id")
            return
        await self._refresh_pending_state()
        kept = [
            row
            for row in (self.pending_questions or [])
            if not (isinstance(row, dict) and str(row.get("id") or "") == qid)
        ]
        if len(kept) == len(self.pending_questions or []):
            logger.warning("handoff remove_pending missing question_id=%r", qid)
            return
        await self._persist_pending_rows(kept)

    async def _conversation(self) -> Any:
        """Current conversation from the tool visitor, when one is bound."""
        from jvagent.tooling.tool_executor import get_tool_visitor

        visitor = get_tool_visitor()
        if visitor is None:
            return None
        conversation = getattr(visitor, "conversation", None)
        if conversation is not None:
            return conversation
        interaction = getattr(visitor, "interaction", None)
        getter = getattr(interaction, "get_conversation", None)
        if not callable(getter):
            return None
        try:
            return await getter()
        except Exception:
            logger.debug("handoff conversation load failed", exc_info=True)
            return None

    async def _saved_contact(self) -> str:
        conversation = await self._conversation()
        context = getattr(conversation, "context", None) or {}
        return str(context.get(_HANDOFF_CONTACT_KEY) or "").strip()

    async def _saved_whatsapp_author(self) -> str:
        conversation = await self._conversation()
        context = getattr(conversation, "context", None) or {}
        return str(context.get(_HANDOFF_WHATSAPP_AUTHOR_KEY) or "").strip()

    async def _save_contact(self, contact: str) -> None:
        contact = (contact or "").strip()
        if not contact:
            return
        conversation = await self._conversation()
        if conversation is None:
            logger.warning("handoff contact not saved: no conversation")
            return
        update = getattr(conversation, "update_context", None)
        if callable(update):
            await update({_HANDOFF_CONTACT_KEY: contact})
            return
        context = getattr(conversation, "context", None)
        if isinstance(context, dict):
            context[_HANDOFF_CONTACT_KEY] = contact

    async def _update_context(self, updates: Dict[str, Any]) -> None:
        """Persist handoff state keys on the current conversation, best-effort."""
        if not updates:
            return
        conversation = await self._conversation()
        if conversation is None:
            logger.warning("handoff context not saved: no conversation")
            return
        update = getattr(conversation, "update_context", None)
        if callable(update):
            await update(updates)
            return
        context = getattr(conversation, "context", None)
        if isinstance(context, dict):
            context.update(updates)

    async def _active_mode(self) -> str:
        """Mode of the handoff currently in progress (``""`` when none)."""
        conversation = await self._conversation()
        context = getattr(conversation, "context", None) or {}
        return str(context.get(_HANDOFF_ACTIVE_MODE_KEY) or "").strip()

    async def _active_issue(self) -> str:
        """Customer ask stored for an in-progress handoff."""
        conversation = await self._conversation()
        context = getattr(conversation, "context", None) or {}
        return str(context.get(_HANDOFF_ACTIVE_ISSUE_KEY) or "").strip()

    async def _set_active(self, mode: str, issue: str = "") -> None:
        await self._update_context(
            {
                _HANDOFF_ACTIVE_MODE_KEY: (mode or "").strip(),
                _HANDOFF_ACTIVE_ISSUE_KEY: (issue or "").strip(),
            }
        )

    async def _clear_active(self) -> None:
        await self._update_context(
            {_HANDOFF_ACTIVE_MODE_KEY: "", _HANDOFF_ACTIVE_ISSUE_KEY: ""}
        )

    async def _group_payload(self) -> Dict[str, Any]:
        """WhatsApp payload on the current visitor, or ``{}``."""
        from jvagent.tooling.tool_executor import get_tool_visitor

        visitor = get_tool_visitor()
        data = getattr(visitor, "data", None) if visitor else None
        if not isinstance(data, dict):
            return {}
        payload = data.get("whatsapp_payload") or {}
        return payload if isinstance(payload, dict) else {}

    async def _enroll_group_staff(self) -> List[str]:
        """Add every other WhatsApp group number to ``HandoffAction.staff``."""
        payload = await self._group_payload()
        if not payload.get("isGroup"):
            return []
        group_id = str(payload.get("sender") or "").strip()
        if not group_id:
            return []
        agent = await self.get_agent()
        wa = (
            await agent.get_action_by_type(self.handoff_notify_action_type)
            if agent
            else None
        )
        if wa is None:
            return []
        api = await wa.api()
        result = await api.group_members(group_id)
        numbers: List[str] = []
        for item in (result or {}).get("response") or []:
            if not isinstance(item, dict) or item.get("formattedName") == "You":
                continue
            user = str((item.get("id") or {}).get("user") or "").strip()
            if user:
                numbers.append(user)
        if not numbers:
            return []
        try:
            aca: Any = await self.get_action("AccessControlAction")
        except Exception:
            return []
        if aca is None:
            return []
        await aca.add_users_to_group("staff", numbers, action_label="HandoffAction")
        return numbers

    async def _pending_debug_ids(self) -> str:
        from jvagent.tooling.tool_executor import get_dispatch_context

        ctx = get_dispatch_context()
        agent = await self.get_agent()
        session_id = (getattr(ctx, "session_id", "") or "").strip()
        user_id = (getattr(ctx, "user_id", "") or "").strip()
        return (
            f"action_agent_id={getattr(self, 'agent_id', None)!r} "
            f"loaded_agent_id={getattr(agent, 'id', None)!r} "
            f"session_id={session_id!r} user_id={user_id!r}"
        )

    async def _condense_qa(self, question: str, answer: str) -> tuple:
        """One short customer question and the factual answer.

        A failed model call keeps the current heading and answer.
        """
        from jvagent.action.utils.call_model import call_model

        fallback = ((question or "").strip(), (answer or "").strip())
        try:
            result = await call_model(
                self,
                user_prompt=(
                    f"Staff summary:\n{fallback[0]}\n\nStaff answer:\n{fallback[1]}"
                ),
                system_prompt=_CONDENSE_SYSTEM,
                json_response=True,
                use_history=False,
            )
        except Exception:
            logger.debug("handoff condense failed", exc_info=True)
            return fallback
        if not isinstance(result, dict):
            return fallback
        short_q = str(result.get("question") or "").strip()
        short_a = str(result.get("answer") or "").strip()
        if not short_q or not short_a:
            return fallback
        return short_q, short_a

    async def _record_event(self, event: str) -> None:
        """Store one handoff event on this interaction and save it."""
        from jvagent.tooling.tool_executor import get_tool_visitor

        text = (event or "").strip()
        if not text:
            return
        visitor = get_tool_visitor()
        interaction = getattr(visitor, "interaction", None) if visitor else None
        adder = getattr(interaction, "add_event", None)
        if interaction is None or not callable(adder):
            return
        try:
            added = adder(text, "HandoffAction")
        except Exception:
            logger.debug("handoff event failed", exc_info=True)
            return
        if added is False:
            return
        saver = getattr(interaction, "save", None)
        if not callable(saver):
            return
        try:
            result = saver()
            if hasattr(result, "__await__"):
                await result
        except Exception:
            logger.debug("handoff event save failed", exc_info=True)

    async def _record_chunk_event(self, chunk_id: str, question: str) -> None:
        """Store the chunk id on this interaction so a later turn can update it."""
        if chunk_id:
            await self._record_event(_chunk_event(chunk_id, question))

    async def _append_and_ingest(self, question: str, answer: str) -> tuple:
        """Condense one Q&A, append its chunk, and re-import the handoff graph."""
        short_q, short_a = await self._condense_qa(question, answer)
        agent = await self.get_agent()
        collection = str(getattr(agent, "id", "") or "") or "default"
        from jvagent.action.pageindex.documents import (
            delete_document,
            export_documents,
            import_documents,
        )

        try:
            exported = await export_documents(
                collection_name=collection, doc_name=HANDOFF_DOC_NAME
            )
        except Exception:
            logger.debug("handoff: export of handoff.md skipped", exc_info=True)
            exported = {}
        graph = _append_handoff_chunk(exported or {}, short_q, short_a, collection)
        nodes = graph.get("nodes") or []
        chunk_id = str(nodes[-1].get("id") or "") if nodes else ""
        _handoff_json_path().write_text(
            json.dumps(graph, indent=2) + "\n", encoding="utf-8"
        )

        try:
            await delete_document(HANDOFF_DOC_NAME, collection_name=collection)
        except Exception:  # document may not exist yet — import replaces it
            logger.debug("handoff: prior handoff.md delete skipped", exc_info=True)
        await import_documents(graph, purge=False, collection_name=collection)
        return short_q, short_a, chunk_id

    async def healthcheck(self) -> Any:
        return True


__all__ = [
    "CONSULT_CLOSE",
    "HANDOFF_DOC_NAME",
    "HANDOFF_INTRO",
    "HANDOFF_PARAMETERS",
    "HandoffAction",
    "TRANSFER_CLOSE",
]
