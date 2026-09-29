"""Human handoff capability action.

Model-callable tools, split across the ``handoff`` and ``handoff_staff`` skills:

- ``handoff__contact_details()`` — return the team's email, phone, and hours.
- ``handoff__update_contact(phone_number?, email?)`` — replace the reply address.
- ``handoff__staff_lookup(message, phone_numbers?, emails?)`` — cannot answer.
- ``handoff__agent_escalation(message, phone_numbers?, emails?)`` — person now.
- ``handoff__scheduled_callback(message, phone_numbers?, emails?)`` — later.
- ``handoff__pending_questions()`` — list questions waiting for an answer.
- ``handoff__save_answer(question_id, answer)`` — save the answer for one
  pending question and return a thank-you.
- ``handoff__update_chunk(chunk_id, answer)`` — replace one saved chunk.

Staff identity is the dispatch sender. Prefer AccessControlAction
``HandoffAction.staff`` (phones and emails in one list). One WhatsApp or email
target is chosen at random per notification.
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

DIRECT_CONTACT_PROMPT = """You can reach a human representative directly using the contact details below:

Email: {handoff_email}
Phone / WhatsApp: {handoff_phone}
Office Hours: {handoff_hours}

A team member will assist you as soon as possible."""


AGENT_ESCALATION_PROMPT = """A staff member will reach out to you shortly."""


SCHEDULED_CALLBACK_PROMPT = """I'll arrange for a human representative to follow up with you.

You can expect a response within the next 24 hours (or the next business day). If your request is urgent, please use the direct contact option for faster assistance."""


STAFF_LOOKUP_PROMPT = """I don't have that information yet, so I'll check with the team and get back to you when they respond."""

STAFF_LOOKUP_ASK_PROMPT = """I don't have that right now. May I have your phone number or email so I can get back to you?"""

HANDOFF_DOC_NAME = "handoff.md"
HANDOFF_DOC_ACCESS = "public"

_CHANNELS = ("whatsapp", "email")

#: Orchestration-scoped routing rule. Accumulated onto the interaction by the
#: orchestrator and rendered into the loop prompt, so the model activates the
#: handoff skill and uses the tools when the user asks for a human or the answer
#: is out of reach. Keyed so an agent can override it.
HANDOFF_PARAMETERS: List[Dict[str, Any]] = [
    {
        "scope": "orchestration",
        "key": "handoff_routing",
        "condition": (
            "the user asks for a human / agent / live support, wants a callback, "
            "OR you cannot answer their question from the knowledge base or the "
            "tools available this turn (a search returned nothing relevant, or "
            "the request is outside what you can do)"
        ),
        "response": (
            "Search the FAQ and available skills first, including a 'do you "
            "sell' question. Do not guess or drop the request. Call use_skill "
            "with name 'handoff' only after that search returned nothing useful "
            "(its tools are gated), then: if the user only wants the team's "
            "contact details call handoff__contact_details; otherwise call "
            "handoff__agent_escalation (a staff member will reach out), "
            "handoff__scheduled_callback (reach out later), or "
            "handoff__staff_lookup (you cannot answer). For staff_lookup, "
            "message sentence 1 is the natural customer ask only; optional "
            "sentence 2 is what was already tried (staff notify only). Do not "
            "mention WhatsApp or tell staff to reply. Relay the returned line "
            "and do not add that summary."
        ),
    },
    {
        "scope": "orchestration",
        "key": "handoff_staff_answer",
        "condition": (
            "the latest message, with the conversation so far, looks like an "
            "answer rather than a new question"
        ),
        "response": (
            "Call handoff__pending_questions first (pending questions are "
            "unknown until it returns), then handoff__save_answer for the match."
        ),
    },
]


def _question_field(question: Any, name: str) -> str:
    if isinstance(question, dict):
        return str(question.get(name) or "")
    return str(getattr(question, name, "") or "")


def _question_row(question: Any) -> Dict[str, Any]:
    """Normalize a pending question (dict or object) for tool output."""
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


def _render_direct_contact(template: str, email: str, phone: str, hours: str) -> str:
    """Render the direct-contact block; drop lines whose field is blank."""
    try:
        rendered = template.format(
            handoff_email=email, handoff_phone=phone, handoff_hours=hours
        )
    except (KeyError, IndexError):
        return template
    cleaned_lines = []
    for line in rendered.split("\n"):
        stripped = line.strip()
        if stripped.endswith(":") and (
            stripped.lower().startswith("email:")
            or stripped.lower().startswith("phone")
        ):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


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


def _staff_outbound(
    mode: str,
    message: str,
    phone_numbers: Optional[List[str]],
    emails: Optional[List[str]],
    contact: str,
) -> str:
    """Staff text: lookup omits the contact; escalation and callback include it."""
    tokens = [
        str(t).strip() for t in (phone_numbers or []) + (emails or []) if str(t).strip()
    ]
    if mode == "staff_lookup":
        cleaned = _lookup_staff_message(message, tokens)
        question = _pending_question_text(cleaned)
        notes = _staff_notes_text(cleaned, question)
        bolded = _bold_whatsapp_ask(question)
        if notes:
            return f"{bolded} {notes}".strip()
        return bolded
    text = (message or "").strip()
    follow_up = (contact or "").strip()
    if follow_up and follow_up not in text:
        text = f"{text}\nContact: {follow_up}".strip()
    return text


def _first_contact(
    phone_numbers: Optional[List[str]], emails: Optional[List[str]]
) -> str:
    for n in phone_numbers or []:
        if str(n).strip():
            return str(n).strip()
    for e in emails or []:
        if str(e).strip():
            return str(e).strip()
    return ""


def _dispatch_user_id() -> str:
    from jvagent.tooling.tool_executor import get_dispatch_context

    ctx = get_dispatch_context()
    return (getattr(ctx, "user_id", "") or "").strip()


def _contact_kind(value: str) -> str:
    """``whatsapp`` for a phone, ``email`` for an address, else empty."""
    text = (value or "").strip()
    if not text or text.lower() == "declined":
        return ""
    if "@" in text and " " not in text:
        return "email"
    digits = re.sub(r"\D", "", text)
    if digits and len(digits) >= 6 and re.fullmatch(r"[+\d][\d\s()-]*", text):
        return "whatsapp"
    return ""


def _sender_contact(channel: str) -> str:
    """Dispatch user id when it is already a phone or email for this channel."""
    user_id = _dispatch_user_id()
    if not user_id:
        return ""
    if channel == "email":
        return user_id if "@" in user_id and " " not in user_id else ""
    digits = re.sub(r"\D", "", user_id)
    if digits and len(digits) >= 6 and re.fullmatch(r"[+\d][\d\s()-]*", user_id):
        return user_id
    return ""


_HANDOFF_CONTACT_KEY = "handoff_contact"
_CONFIRM_MODES = ("agent_escalation", "scheduled_callback")


def _issue_keys(message: str) -> set:
    """Raw and contact-stripped forms of one staff issue (question side only)."""
    raw = (message or "").strip()
    keys = {raw} if raw else set()
    stripped = _lookup_staff_message(raw, [])
    if stripped:
        keys.add(stripped)
    question = _pending_question_text(stripped or raw)
    if question:
        keys.add(question)
    return keys


def _confirm_directive(mode: str, message: str, contact: str) -> ToolResult:
    """Ask the user to confirm the issue and contact before staff are notified."""
    tool_name = f"handoff__{mode}"
    return ToolResult(
        content=(
            "Confirm with the user before notifying staff. "
            f"The issue is: {message}. The contact is: {contact}. "
            "Ask if that issue and contact are correct. If they agree, call "
            f"{tool_name} again with the same message and confirmed true. "
            "If they give a different phone or email, call "
            f"{tool_name} again with the same message and pass it in "
            "phone_numbers or emails. Do not say staff were notified."
        )
    )


def _relay(line: str) -> ToolResult:
    return ToolResult(
        content=("Relay this to the user and do not add the staff summary:\n" f"{line}")
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


def _handoff_json_path() -> Path:
    """``<files_root>/handoff.json`` (defaults to ``./.files/handoff.json``)."""
    from jvagent.core.sandbox import resolve_sandbox_root

    root = resolve_sandbox_root()
    path = Path(root) / "handoff.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _chunk_event(chunk_id: str, question: str) -> str:
    return f'Handoff chunk {chunk_id} "{question}".'


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
    """Human handoff tools: direct contact, staff notification, staff Q&A."""

    tool_namespace: ClassVar[str] = "handoff"

    description: str = attribute(
        default=(
            "Human handoff tools — show direct contact details, notify staff "
            "(WhatsApp or email) for escalation/callback, look up a pending "
            "customer question, or resolve it into the knowledge base."
        ),
        description="Action description",
    )

    parameters: List[Dict[str, Any]] = attribute(
        default_factory=lambda: [dict(p) for p in HANDOFF_PARAMETERS],
        description=(
            "Scoped behavioural parameters this action contributes to the common "
            "subsystem. The orchestration-scoped rule tells the loop when to "
            "hand off (explicit human ask, or an unanswerable question)."
        ),
    )

    direct_contact_prompt: str = attribute(
        default=DIRECT_CONTACT_PROMPT, description="Prompt for direct contact"
    )
    agent_escalation_prompt: str = attribute(
        default=AGENT_ESCALATION_PROMPT, description="Prompt for agent escalation"
    )
    scheduled_callback_prompt: str = attribute(
        default=SCHEDULED_CALLBACK_PROMPT, description="Prompt for scheduled callback"
    )
    staff_lookup_prompt: str = attribute(
        default=STAFF_LOOKUP_PROMPT, description="Prompt for staff lookup"
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
            "agent_escalation": "whatsapp",
            "scheduled_callback": "whatsapp",
            "staff_lookup": "whatsapp",
        },
        description="Per-mode notify channel: whatsapp | email.",
    )

    pending_questions: List[Dict[str, Any]] = attribute(
        default_factory=list,
        description=(
            "Customer questions waiting for a staff answer. Source of truth for "
            "handoff__pending_questions / save_answer (reloaded from DB on read)."
        ),
    )

    # -- tools -----------------------------------------------------------------

    def get_capabilities(self) -> List[str]:
        if not self.enabled:
            return []
        return [
            "Reach a human teammate (contact details, escalation, callback, or "
            "checking a pending question with staff)",
        ]

    @tool(name="handoff__contact_details")
    async def contact_details(self) -> str:
        """Return the team's email, phone, and office hours. Use when the user wants those details and no notification should be sent."""
        phone, emails = await self._public_contacts()
        return _render_direct_contact(
            self.direct_contact_prompt,
            email=", ".join(emails),
            phone=phone,
            hours=self.handoff_hours,
        )

    @tool(name="handoff__update_contact")
    async def update_contact(
        self,
        phone_number: Annotated[
            Optional[str],
            "One phone number. Omit when none.",
        ] = None,
        email: Annotated[
            Optional[str],
            "One email address. Omit when none.",
        ] = None,
    ) -> ToolResult:
        """Replace the saved phone or email replies are sent to. Use when the user wants to change where the team reaches them."""
        contact = (phone_number or "").strip() or (email or "").strip()
        if not contact:
            return ToolResult(
                content="handoff failed: pass a phone number or email.",
                is_error=True,
            )
        prior = await self._saved_contact()
        if await self._conversation() is None:
            return ToolResult(
                content="handoff failed: the contact could not be saved.",
                is_error=True,
            )
        await self._save_contact(contact)
        if await self._saved_contact() != contact:
            return ToolResult(
                content="handoff failed: the contact could not be saved.",
                is_error=True,
            )
        if prior:
            await self._update_pending_contact(prior, contact)
        return _relay(f"Your contact is updated to {contact}.")

    @tool(
        name="handoff__staff_lookup", idempotency_class=IdempotencyClass.NON_RETRYABLE
    )
    async def staff_lookup(
        self,
        message: Annotated[
            str,
            "Sentence 1: natural customer ask only. Optional sentence 2: what "
            "was already tried (staff notify only). Never shown to the user.",
        ],
        phone_numbers: Annotated[
            Optional[List[str]],
            "Phone numbers the user provided. Omit when none.",
        ] = None,
        emails: Annotated[
            Optional[List[str]],
            "Email addresses the user provided. Omit when none.",
        ] = None,
        contact_declined: Annotated[
            Optional[bool],
            "True if the user refused to share a phone or email.",
        ] = None,
    ) -> ToolResult:
        """Record a question you cannot answer and notify staff. Use after a search returned nothing useful."""
        return await self._dispatch_handoff(
            "staff_lookup",
            message,
            phone_numbers,
            emails,
            contact_declined=bool(contact_declined),
        )

    @tool(
        name="handoff__agent_escalation",
        idempotency_class=IdempotencyClass.NON_RETRYABLE,
    )
    async def agent_escalation(
        self,
        message: Annotated[
            str,
            "The customer's question and what was already tried. Never shown to the user.",
        ],
        phone_numbers: Annotated[
            Optional[List[str]],
            "Phone numbers the user provided. Omit when none.",
        ] = None,
        emails: Annotated[
            Optional[List[str]],
            "Email addresses the user provided. Omit when none.",
        ] = None,
        confirmed: Annotated[
            Optional[bool],
            "True after the user confirms this issue and contact.",
        ] = None,
    ) -> ToolResult:
        """Notify staff that the customer wants a person now. Use when the user asks for a human or live support."""
        return await self._dispatch_handoff(
            "agent_escalation",
            message,
            phone_numbers,
            emails,
            confirmed=bool(confirmed),
        )

    @tool(
        name="handoff__scheduled_callback",
        idempotency_class=IdempotencyClass.NON_RETRYABLE,
    )
    async def scheduled_callback(
        self,
        message: Annotated[
            str,
            "The customer's question and what was already tried. Never shown to the user.",
        ],
        phone_numbers: Annotated[
            Optional[List[str]],
            "Phone numbers the user provided. Omit when none.",
        ] = None,
        emails: Annotated[
            Optional[List[str]],
            "Email addresses the user provided. Omit when none.",
        ] = None,
        confirmed: Annotated[
            Optional[bool],
            "True after the user confirms this issue and contact.",
        ] = None,
    ) -> ToolResult:
        """Notify staff to reach the customer later. Use when the user wants a callback."""
        return await self._dispatch_handoff(
            "scheduled_callback",
            message,
            phone_numbers,
            emails,
            confirmed=bool(confirmed),
        )

    async def _dispatch_handoff(
        self,
        mode: str,
        message: str,
        phone_numbers: Optional[List[str]],
        emails: Optional[List[str]],
        contact_declined: bool = False,
        confirmed: bool = False,
    ) -> ToolResult:
        channel = self._channel_for(mode)
        prior = await self._saved_contact()
        provided = _first_contact(phone_numbers, emails)
        contact_replaced = bool(provided) and provided != prior
        if provided:
            await self._save_contact(provided)
        contact = provided or prior or _sender_contact(channel)
        if mode in _CONFIRM_MODES:
            if not contact:
                tool_name = f"handoff__{mode}"
                return ToolResult(
                    content=(
                        "Ask the user for a phone number or email. When they reply, "
                        f"call {tool_name} again with the same message and pass that "
                        "contact in phone_numbers or emails."
                    )
                )
            if contact_replaced or not confirmed:
                return _confirm_directive(mode, message, contact)
            user_facing = (
                self.agent_escalation_prompt
                if mode == "agent_escalation"
                else self.scheduled_callback_prompt
            )
        elif mode == "staff_lookup" and not contact and not contact_declined:
            logger.warning(
                "handoff staff_lookup ask path no contact declined=%s %s",
                contact_declined,
                await self._pending_debug_ids(),
            )
            return ToolResult(
                content=(
                    "Ask the user, in a natural line, that you don't have that "
                    "right now and may they share a phone number or email so you "
                    "can get back to them. If they share one, call "
                    "handoff__staff_lookup again with the same message and pass "
                    "it in phone_numbers or emails. If they refuse, call "
                    "handoff__staff_lookup again with the same message and "
                    "contact_declined true."
                )
            )
        updating = False
        if mode == "staff_lookup":
            user_facing = self.staff_lookup_prompt
            ids = await self._pending_debug_ids()
            open_q = await self._open_lookup(message)
            if open_q is not None:
                stored = _question_field(open_q, "user_contact").strip()
                effective = contact or ("declined" if contact_declined else "")
                contact_changed = bool(contact) and stored != contact
                question_id = _question_field(open_q, "id")
                if stored and not contact_changed:
                    logger.warning(
                        "handoff staff_lookup send skipped same contact "
                        "question_id=%s stored=%r contact=%r declined=%s %s",
                        question_id,
                        stored,
                        contact,
                        contact_declined,
                        ids,
                    )
                    return _relay(user_facing)
                logger.warning(
                    "handoff staff_lookup updating question_id=%s "
                    "stored=%r effective=%r contact=%r declined=%s %s",
                    question_id,
                    stored,
                    effective,
                    contact,
                    contact_declined,
                    ids,
                )
                new_contact = contact or effective
                await self._update_pending(
                    question_id, user_contact=new_contact, node=open_q
                )
                updating = True

        targets = await self._staff_targets(channel)
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
        outbound = _staff_outbound(mode, message, phone_numbers, emails, contact)
        if mode == "staff_lookup" and not updating:
            logger.warning(
                "handoff staff_lookup send path creating pending question "
                "contact=%r declined=%s %s",
                contact,
                contact_declined,
                await self._pending_debug_ids(),
            )
            recorded = "declined" if contact_declined and not contact else contact
            pending_q = _pending_question_text(
                _lookup_staff_message(
                    message,
                    [
                        str(t).strip()
                        for t in (phone_numbers or []) + (emails or [])
                        if str(t).strip()
                    ],
                )
                or message
            )
            await self._add_pending(pending_q, recorded)

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
                    "Apologize briefly and offer the direct contact option via "
                    "handoff__contact_details."
                ),
                is_error=True,
            )

        logger.info(
            "handoff %s summary dispatched via %s to %s", mode, channel, recipient
        )
        return _relay(user_facing)

    @tool(name="handoff__pending_questions")
    async def list_pending_questions(self) -> ToolResult:
        """List customer questions waiting for an answer. If the latest message and the conversation so far look like an answer rather than a new question, call this first. Then call handoff__save_answer with the question_id it matches."""
        try:
            pending = await self._list_pending()
        except Exception:
            logger.warning(
                "handoff pending_questions failed action_agent_id=%r",
                getattr(self, "agent_id", None),
                exc_info=True,
            )
            return ToolResult(
                content="handoff failed: could not list pending questions.",
                is_error=True,
            )
        if not pending:
            logger.warning(
                "handoff pending_questions returning empty action_agent_id=%r",
                getattr(self, "agent_id", None),
            )
            return ToolResult(content="(no pending questions)")
        lines = [
            "- id={id} | contact={contact} | Q: {question}".format(
                id=_question_field(q, "id") or "unknown",
                contact=_question_field(q, "user_contact") or "unknown",
                question=_question_field(q, "question")[:300],
            )
            for q in pending
        ]
        return ToolResult(
            content=(
                "Pending questions:\n"
                + "\n".join(lines)
                + "\n\nCall handoff__save_answer with the question_id that this "
                "answer matches and the full answer text. Do not reply to the "
                "user yet."
            )
        )

    @tool(name="handoff__save_answer", idempotency_class=IdempotencyClass.NON_RETRYABLE)
    async def save_answer(
        self,
        question_id: Annotated[str, "id of the pending question being answered."],
        answer: Annotated[str, "Full answer to store."],
    ) -> ToolResult:
        """Save the full answer for one pending question and return a confirmation. Use after handoff__pending_questions, with the matching question_id."""
        from jvagent.tooling.tool_executor import get_tool_visitor

        if not await self._is_staff():
            return ToolResult(
                content=(
                    "handoff failed: only authorized staff may resolve a pending "
                    "question."
                ),
                is_error=True,
            )
        visitor = get_tool_visitor()
        utterance = str(getattr(visitor, "utterance", "") or "")
        cleaned = _extract_saved_answer(answer, utterance)
        if not cleaned:
            return ToolResult(
                content="handoff failed: no answer text to save.",
                is_error=True,
            )
        question = await self._get_pending_question(question_id)
        if question is None:
            return ToolResult(
                content=f"handoff failed: no pending question with id {question_id!r}",
                is_error=True,
            )
        try:
            short_q, short_a, chunk_id = await self._append_and_ingest(
                _question_field(question, "question"), cleaned
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
        await self._remove_pending(question_id, answer=cleaned, node=question)
        await self._reply_to_customer(question, short_q, short_a)
        await self._record_chunk_event(chunk_id, short_q)
        return ToolResult(
            content=(
                "Relay this to the staff member as-is: Your answer is saved "
                "and will be used in the future to answer this question."
            )
        )

    @tool(
        name="handoff__update_chunk", idempotency_class=IdempotencyClass.NON_RETRYABLE
    )
    async def update_chunk(
        self,
        chunk_id: Annotated[str, "id of the handoff chunk to update."],
        answer: Annotated[str, "Full answer to store."],
    ) -> ToolResult:
        """Update the saved answer for one existing chunk and return a confirmation. Use when the user is correcting a saved answer, taking chunk_id from the [EVENT] in history and the answer from their message."""
        from jvagent.tooling.tool_executor import get_tool_visitor

        if not await self._is_staff():
            return ToolResult(
                content=(
                    "handoff failed: only authorized staff may resolve a pending "
                    "question."
                ),
                is_error=True,
            )
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
        return ToolResult(
            content=(
                "Relay this to the staff member as-is: Your answer is updated "
                "and will be used in the future to answer this question."
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
                    "Write a short message to the customer. Remind them of "
                    "the question they asked, then give the answer. Plain text "
                    "only. No mention of staff, tools, or a knowledge base."
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
        contact = _question_field(question, "user_contact").strip()
        kind = _contact_kind(contact)
        if not kind:
            return
        text = await self._customer_reply(short_q, short_a)
        try:
            if kind == "email":
                await self._send_email(
                    [contact], text, subject=short_q or "Your question"
                )
            else:
                await self._send_whatsapp(contact, text)
        except Exception:
            logger.error("handoff reply to customer failed", exc_info=True)

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

    async def _public_contacts(self) -> tuple:
        """Phone is one random staff number; email is every staff address."""
        numbers = await self._staff_targets("whatsapp")
        emails = await self._staff_targets("email")
        phone = _pick_staff(numbers) if numbers else ""
        return phone, emails

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

    async def _send_whatsapp(self, recipient: str, message: str) -> None:
        masked = _mask(recipient)
        agent = await self.get_agent()
        logger.warning(
            "handoff whatsapp: start recipient=%s body_chars=%d agent=%s",
            masked,
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
            "handoff whatsapp: sending via %s to %s",
            type(api).__name__,
            masked,
        )
        result = await api.send_message(phone=recipient, message=message or "")
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
                "id": f"q_{uuid.uuid4().hex}",
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
        """Update contact on one pending question."""
        qid = (question_id or "").strip() or _question_field(node, "id").strip()
        if not qid:
            logger.warning("handoff update_pending missing question_id")
            return
        await self._refresh_pending_state()
        rows = self._unanswered_rows()
        changed = False
        for row in rows:
            if str(row.get("id") or "") == qid:
                row["user_contact"] = (user_contact or "").strip()
                changed = True
                break
        if not changed:
            logger.warning("handoff update_pending missing question_id=%r", qid)
            return
        await self._persist_pending_rows(rows)

    async def _update_pending_contact(self, prior: str, contact: str) -> None:
        """Update user_contact on every pending question matching ``prior``."""
        prior = (prior or "").strip()
        contact = (contact or "").strip()
        if not prior or not contact:
            return
        await self._refresh_pending_state()
        rows = self._unanswered_rows()
        changed = False
        for row in rows:
            if str(row.get("user_contact") or "") == prior:
                row["user_contact"] = contact
                changed = True
        if changed:
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

    async def _open_lookup(self, message: str = "") -> Any:
        """Open pending question for this issue."""
        try:
            agent_id = await self._agent_id()
            keys = _issue_keys(message)
            rows = await self._list_pending()
            for question in rows:
                stored = _question_field(question, "question").strip()
                if keys and stored not in keys:
                    continue
                logger.warning(
                    "handoff open lookup matched id=%s agent_id=%r",
                    _question_field(question, "id"),
                    agent_id,
                )
                return question
            logger.warning(
                "handoff open lookup no match agent_id=%r scanned=%s",
                agent_id,
                len(rows),
            )
        except Exception:
            logger.warning("handoff open lookup failed", exc_info=True)
        return None

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

    async def _record_chunk_event(self, chunk_id: str, question: str) -> None:
        """Store the chunk id on this interaction so a later turn can update it."""
        from jvagent.tooling.tool_executor import get_tool_visitor

        visitor = get_tool_visitor()
        interaction = getattr(visitor, "interaction", None) if visitor else None
        adder = getattr(interaction, "add_event", None)
        if interaction is None or not callable(adder) or not chunk_id:
            return
        try:
            added = adder(_chunk_event(chunk_id, question), "HandoffAction")
        except Exception:
            logger.debug("handoff chunk event failed", exc_info=True)
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
            logger.debug("handoff chunk event save failed", exc_info=True)

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

    # -- rendering helper (shared with tests) ---------------------------------

    async def render_direct_contact(self) -> str:
        phone, emails = await self._public_contacts()
        return _render_direct_contact(
            self.direct_contact_prompt,
            email=", ".join(emails),
            phone=phone,
            hours=self.handoff_hours,
        )


__all__ = [
    "AGENT_ESCALATION_PROMPT",
    "DIRECT_CONTACT_PROMPT",
    "HANDOFF_DOC_NAME",
    "HANDOFF_PARAMETERS",
    "HandoffAction",
    "SCHEDULED_CALLBACK_PROMPT",
    "STAFF_LOOKUP_PROMPT",
]
