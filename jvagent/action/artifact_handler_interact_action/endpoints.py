"""ArtifactHandlerInteractAction import + notification callback endpoint.

jvforge POSTs to ``/api/artifact_handler_action/notify/{agent_id}`` with a
``process_document_url`` when an async ingest job finishes. The vault
downloads the artifact, imports the pageindex_graph into PageIndex, then
sends a proactive notification (WhatsApp or Messenger) with a ready notice
and an optional answer.
(background, using call_model if there's a pending question).

On failure the endpoint returns 503 + Retry-After so jvforge retries the
callback. On success it returns 200.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response

logger = logging.getLogger(__name__)


async def _resolve_action(agent_id: str) -> Optional[Any]:
    """Resolve the ArtifactHandlerInteractAction instance for this agent.

    Uses ``get_action_by_type`` (entity + agent_id query), not ``get_action``
    (label via Actions manager). Fresh ``Agent.get()`` in a webhook context
    often has no hydrated Actions edge — same pattern as PageIndex
    product callbacks.
    """
    try:
        from jvagent.core.agent import Agent

        agent = await Agent.get(agent_id)
        if agent is None:
            return None
        action = await agent.get_action_by_type("ArtifactHandlerInteractAction")
        if action is None:
            pass
        return action
    except Exception:
        return None


def _display_doc_name(entry: Dict[str, Any], payload_doc_name: str) -> str:
    """Prefer original filename; else strip ``{user_id}_`` from doc_name."""
    filename = str(entry.get("filename") or "").strip()
    if filename:
        return filename
    doc_name = str(payload_doc_name or entry.get("doc_name") or "").strip()
    if not doc_name:
        return "your document"
    user_id = str(entry.get("user_id") or "").strip()
    if user_id:
        uid = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in user_id)[
            :64
        ]
        prefix = f"{uid}_"
        if doc_name.startswith(prefix) and len(doc_name) > len(prefix):
            return doc_name[len(prefix) :]
    # Best-effort: strip leading ``{id}_`` when filename was not persisted.
    if "_" in doc_name:
        _, _, rest = doc_name.partition("_")
        if rest and "." in rest:
            return rest
    return doc_name


_IMAGE_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".heic",
        ".heif",
        ".bmp",
        ".tif",
        ".tiff",
    }
)

# WhatsApp / channel hash names like ``20260724_134410_c624a9f1.pdf``.
_MACHINE_FILENAME_RE = re.compile(r"^\d{8}_\d{6}_[0-9a-fA-F]{6,}(\.[A-Za-z0-9]+)?$")

# Short or ambiguous filenames that should NOT be quoted in user-facing messages.
_GENERIC_NAMES = frozenset(
    {
        "edit",
        "file",
        "upload",
        "document",
        "image",
        "photo",
        "pic",
        "img",
        "scan",
        "download",
        "attachment",
        "temp",
        "tmp",
        "test",
        "untitled",
        "new",
        "copy",
        "backup",
    }
)


def _should_quote_filename(display_doc: str) -> bool:
    """Decide whether to quote the filename in a user-facing message.

    Machine hash names and short/generic names are not quoted — the type word
    (PDF, image, document, …) is used instead.
    """
    name = (display_doc or "").strip()
    if not name or _MACHINE_FILENAME_RE.match(name):
        return False
    base = name.rsplit(".", 1)[0] if "." in name else name
    if base.lower() in _GENERIC_NAMES:
        return False
    if len(base) <= 2:
        return False
    return True


def _file_kind_label(display_doc: str) -> str:
    """Return ``image`` or ``document`` based on filename extension."""
    name = (display_doc or "").strip().lower()
    _, _, ext = name.rpartition(".")
    if ext and f".{ext}" in _IMAGE_EXTENSIONS:
        return "image"
    return "document"


def _file_type_word(display_doc: str) -> str:
    """Short type word for natural phrases (PDF, image, Word document, …)."""
    name = (display_doc or "").strip().lower()
    if "." not in name:
        return _file_kind_label(display_doc)
    ext = name.rsplit(".", 1)[-1]
    if f".{ext}" in _IMAGE_EXTENSIONS:
        return "image"
    mapping = {
        "pdf": "PDF",
        "doc": "Word document",
        "docx": "Word document",
        "txt": "text file",
        "rtf": "document",
        "csv": "spreadsheet",
        "xls": "spreadsheet",
        "xlsx": "spreadsheet",
        "ppt": "presentation",
        "pptx": "presentation",
    }
    return mapping.get(ext, _file_kind_label(display_doc))


def _friendly_file_phrase(display_doc: str) -> str:
    """Natural file reference, e.g. ``your PDF`` or ``your document 'report.pdf'``.

    Machine / hash basenames from WhatsApp are not quoted.
    """
    name = (display_doc or "").strip()
    type_word = _file_type_word(name)
    if (
        not name
        or name.lower() in ("document", "your document", "uploaded_file", "image")
        or _MACHINE_FILENAME_RE.match(name)
    ):
        return f"your {type_word}"
    kind = _file_kind_label(name)
    return f"your {kind} '{name}'"


def _format_search_excerpts(results: Any) -> str:
    """Turn PageIndex search results into plain text for the LLM prompt."""
    if not results:
        return ""
    if isinstance(results, dict):
        results = results.get("results") or results.get("documents") or []
    if not isinstance(results, list):
        return str(results)
    parts: List[str] = []
    for r in results:
        if not isinstance(r, dict):
            parts.append(str(r))
            continue
        content = r.get("content") or r.get("text") or r.get("title") or ""
        title = str(r.get("title") or "").strip()
        if title and content:
            parts.append(f"- [{title}] {content}")
        elif content:
            parts.append(f"- {content}")
    return "\n".join(parts)


async def _doc_description_lookup(
    agent: Any,
    ready_entries: List[Dict[str, Any]],
) -> Dict[str, str]:
    """Build a ``internal_doc_name → doc_description`` map from PageIndex.

    Returns an empty dict on failure (caller should fall back gracefully).
    """
    if not ready_entries:
        return {}
    page_index = await agent.get_action_by_type("PageIndexAction")
    if page_index is None:
        return {}
    try:
        docs = await page_index.list_documents(access_control=False, summary=True)
    except Exception:
        return {}
    if not isinstance(docs, list):
        return {}
    lookup: Dict[str, str] = {}
    for d in docs:
        if not isinstance(d, dict):
            continue
        name = str(d.get("doc_name") or "").strip()
        desc = str(d.get("doc_description") or "").strip()
        if name and desc:
            lookup[name] = desc
    return lookup


async def _publish_whatsapp_message(
    *,
    agent: Any,
    user_id: str,
    session_id: str,
    conversation_id: str,
    content: str,
    display_doc: str,
    job_id: str,
    answered: bool = False,
) -> bool:
    """Send a WhatsApp message directly via the WhatsApp API.

    Creates an interaction for record-keeping, sets the response, and sends
    the message via ``api.send_message()`` — never via the response bus,
    which would trigger the orchestrator and produce a duplicate reply.
    """
    memory = await agent.get_memory()
    if not memory:
        return False

    conversation = None
    if conversation_id:
        try:
            from jvagent.memory.conversation import Conversation

            conversation = await Conversation.get(conversation_id)
        except Exception:
            conversation = None

    if conversation is None:
        user = await memory.get_user(user_id, create_if_missing=False)
        if not user:
            return False
        if session_id:
            conversation = await user.get_conversation_by_session(session_id)
        if conversation is None:
            return False

    effective_session_id = (
        session_id or str(getattr(conversation, "session_id", "") or "").strip() or ""
    )
    if not effective_session_id:
        return False

    interaction = await conversation.add_interaction(
        utterance="",
        channel="whatsapp",
        session_id=effective_session_id,
    )
    if not interaction:
        return False

    interaction.add_parameter(
        {
            "is_proactive": True,
            "job_id": job_id,
            "doc_name": display_doc,
            "ready": True,
            "answered": answered,
        },
        "ArtifactHandlerInteractAction",
    )

    if content and content.strip():
        interaction.set_response(content.strip())

    await interaction.save()

    whatsapp_action = await agent.get_action_by_type("WhatsAppAction")
    if whatsapp_action is None:
        return False

    try:
        if not whatsapp_action.is_configured():
            return False
    except Exception:
        return False

    try:
        api = await whatsapp_action.api()
    except Exception:
        return False

    try:
        result = await api.send_message(
            phone=user_id,
            message=content,
        )
        return isinstance(result, dict) and bool(result.get("ok", True))
    except Exception:
        return False


async def _publish_messenger_message(
    *,
    agent: Any,
    user_id: str,
    session_id: str,
    conversation_id: str,
    content: str,
    display_doc: str,
    job_id: str,
    answered: bool = False,
) -> bool:
    """Send a Facebook Messenger message via the registered FacebookAction.

    Creates an interaction for record-keeping, sets the response, and sends
    via ``FacebookAPI.send_text_message()`` on the live FacebookAction held by
    the registered MessengerAdapter — never via ``response_bus.publish``, which
    would append a duplicate reply onto the interaction.
    """
    memory = await agent.get_memory()
    if not memory:
        logger.warning("_publish_messenger_message: agent has no memory, cannot send")
        return False

    conversation = None
    if conversation_id:
        try:
            from jvagent.memory.conversation import Conversation

            conversation = await Conversation.get(conversation_id)
        except Exception:
            conversation = None

    if conversation is None:
        user = await memory.get_user(user_id, create_if_missing=False)
        if not user:
            logger.warning(
                "_publish_messenger_message: user not found user_id=%s", user_id
            )
            return False
        if session_id:
            conversation = await user.get_conversation_by_session(session_id)
        if conversation is None:
            logger.warning(
                "_publish_messenger_message: conversation not found "
                "user_id=%s session_id=%s conversation_id=%s",
                user_id,
                session_id,
                conversation_id,
            )
            return False

    effective_session_id = (
        session_id or str(getattr(conversation, "session_id", "") or "").strip() or ""
    )
    if not effective_session_id:
        logger.warning(
            "_publish_messenger_message: no effective session_id "
            "user_id=%s conversation_id=%s",
            user_id,
            conversation_id,
        )
        return False

    interaction = await conversation.add_interaction(
        utterance="",
        channel="messenger",
        session_id=effective_session_id,
    )
    if not interaction:
        logger.warning(
            "_publish_messenger_message: add_interaction returned None "
            "user_id=%s conversation_id=%s",
            user_id,
            conversation_id,
        )
        return False

    interaction.add_parameter(
        {
            "is_proactive": True,
            "job_id": job_id,
            "doc_name": display_doc,
            "ready": True,
            "answered": answered,
        },
        "ArtifactHandlerInteractAction",
    )

    if content and content.strip():
        interaction.set_response(content.strip())

    await interaction.save()

    # Use the already-registered live FacebookAction held by MessengerAdapter
    # (startup-resolved Page token). Never call api() on a fresh find_one instance.
    try:
        response_bus = await agent.get_response_bus()
    except Exception:
        logger.warning(
            "_publish_messenger_message: get_response_bus failed",
            exc_info=True,
        )
        return False
    if not response_bus:
        logger.warning("_publish_messenger_message: no response bus")
        return False

    adapter = response_bus._channel_adapters.get("messenger")
    if not adapter or not getattr(adapter, "_initialized", False):
        facebook_action = await agent.get_action_by_type("FacebookAction")
        if facebook_action is None:
            logger.warning(
                "_publish_messenger_message: FacebookAction not found on agent"
            )
            return False
        try:
            await facebook_action.ensure_page_access_token()
            await facebook_action.ensure_adapter_registered()
        except Exception:
            logger.warning(
                "_publish_messenger_message: ensure adapter/token failed",
                exc_info=True,
            )
            return False
        adapter = response_bus._channel_adapters.get("messenger")
        if not adapter:
            logger.warning(
                "_publish_messenger_message: MessengerAdapter not registered"
            )
            return False

    facebook_action = getattr(adapter, "action", None)
    if facebook_action is None:
        logger.warning(
            "_publish_messenger_message: MessengerAdapter has no FacebookAction"
        )
        return False

    try:
        if not facebook_action.is_configured():
            logger.warning("_publish_messenger_message: FacebookAction not configured")
            return False
    except Exception:
        logger.warning(
            "_publish_messenger_message: FacebookAction is_configured() failed",
            exc_info=True,
        )
        return False

    try:
        api = facebook_action.api()
    except Exception:
        logger.warning(
            "_publish_messenger_message: FacebookAction.api() failed on "
            "registered action",
            exc_info=True,
        )
        return False

    try:
        result = await asyncio.to_thread(
            api.send_text_message, user_id, content
        )
        if isinstance(result, dict) and result.get("error"):
            logger.error(
                "_publish_messenger_message: send_text_message error for "
                "user_id=%s: %s",
                user_id,
                result.get("error"),
            )
            return False
        logger.info(
            "_publish_messenger_message: sent to user_id=%s job_id=%s", user_id, job_id
        )
        return True
    except Exception:
        logger.error(
            "_publish_messenger_message: send_text_message exception for user_id=%s",
            user_id,
            exc_info=True,
        )
        return False


_PROCESSING_STATUSES = frozenset({"queued", "processing", "pending", "submitted"})

_RETRY_AFTER_SECONDS = 30

_ARTIFACT_404_RETRIES = 6
_ARTIFACT_404_BACKOFF_S = (1.0, 2.0, 4.0, 8.0, 10.0, 5.0)


async def _download_and_import_graph(
    process_document_url: str,
    agent_id: str,
) -> Optional[str]:
    """Download artifact from jvforge and import into PageIndex.

    Rewrites the URL onto ``JVAGENT_JVFORGE_BASE_URL`` when the path
    matches ``/v1/artifacts/...``, so tunnel hostnames that are not
    DNS-resolvable from this host are handled correctly.

    Returns the effective doc_name on success, None on failure.
    """
    from jvagent.action.pageindex.documents import import_documents as _import_documents
    from jvagent.action.pageindex.url_guard import (
        fetch_url_bytes_capped,
        is_trusted_jvforge_url,
        rewrite_process_document_url_to_jvforge_base,
    )

    fetch_url = rewrite_process_document_url_to_jvforge_base(process_document_url)
    trusted = is_trusted_jvforge_url(fetch_url)
    if fetch_url != process_document_url:

        pass
    raw_bytes: Optional[bytes] = None
    for attempt in range(1, _ARTIFACT_404_RETRIES + 1):
        try:
            raw_bytes, _fname_hint, _ct = await fetch_url_bytes_capped(
                fetch_url,
                read_timeout=300.0,
                trusted_jvforge=trusted,
            )
            break
        except Exception as exc:
            msg = str(getattr(exc, "message", exc) or exc)
            if "HTTP 404" in msg and attempt < _ARTIFACT_404_RETRIES:
                delay = _ARTIFACT_404_BACKOFF_S[
                    min(attempt - 1, len(_ARTIFACT_404_BACKOFF_S) - 1)
                ]
                await asyncio.sleep(delay)
                continue
            return None

    if not raw_bytes:
        return None

    try:
        graph = json.loads(raw_bytes)
    except Exception:
        return None

    if not isinstance(graph, dict):
        return None

    roots = graph.get("roots")
    if not isinstance(roots, list) or not roots:
        return None

    root_name = ""
    if isinstance(roots[0], dict):
        root_name = str(roots[0].get("doc_name") or "").strip()

    try:
        from jvagent.action.pageindex.jvforge_assimilate import (
            _rewrite_pageindex_graph_doc_names,
            strip_redundant_md_suffix,
        )
    except Exception:
        try:
            from jvagent.action.pageindex.adapter import strip_redundant_md_suffix

            _rewrite_pageindex_graph_doc_names = None
        except Exception:
            strip_redundant_md_suffix = None
            _rewrite_pageindex_graph_doc_names = None

    effective_name = root_name
    if effective_name and strip_redundant_md_suffix:
        normalized = strip_redundant_md_suffix(effective_name)
        if _rewrite_pageindex_graph_doc_names:
            _rewrite_pageindex_graph_doc_names(graph, effective_name, normalized)
        effective_name = normalized

    if not effective_name:
        effective_name = "document"

    for root in graph.get("roots") or []:
        if isinstance(root, dict):
            root["collection_name"] = agent_id
            ctx = root.get("context")
            if isinstance(ctx, dict):
                ctx["collection_name"] = agent_id
    for node in graph.get("nodes") or []:
        if isinstance(node, dict):
            node["collection_name"] = agent_id
            ctx = node.get("context")
            if isinstance(ctx, dict):
                ctx["collection_name"] = agent_id

    try:
        await _import_documents(graph, purge=False, collection_name=agent_id)
    except Exception:
        return None

    return effective_name


def _canned_ready_message(
    display_doc: str,
    doc_description: Optional[str] = None,
    pending_question: Optional[str] = None,
) -> str:
    """Fallback notification message (single message, never 'file').

    When a pending question exists: ready → remind question → invite answer
    follow-up (no LLM answer available in this fallback).
    """
    type_word = _file_type_word(display_doc)
    if _should_quote_filename(display_doc):
        phrase = _friendly_file_phrase(display_doc)
        lead = f"{phrase[0].upper()}{phrase[1:]} is ready"
    else:
        lead = f"Your {type_word} is ready"

    if pending_question:
        msg = f"{lead}. You asked: {pending_question}."
        if doc_description:
            msg += f" It covers {doc_description}."
        msg += " Ask me anything about it."
        return msg

    if doc_description:
        return f"{lead}. It covers {doc_description}. Ask me anything about it."
    return f"{lead}. Ask me anything about it."


def _canned_ready_message_multi(
    display_docs: List[str],
    doc_descriptions: Optional[Dict[str, str]] = None,
    pending_questions: Optional[Dict[str, str]] = None,
) -> str:
    """Consolidated ready notice for multiple documents."""
    if not display_docs:
        return "Your files are ready. Ask me anything about them."
    if len(display_docs) == 1:
        dd = (doc_descriptions or {}).get(display_docs[0]) if doc_descriptions else None
        pq = (
            (pending_questions or {}).get(display_docs[0])
            if pending_questions
            else None
        )
        return _canned_ready_message(
            display_docs[0], doc_description=dd, pending_question=pq
        )

    phrases: List[str] = []
    for d in display_docs:
        if _should_quote_filename(d):
            phrases.append(_friendly_file_phrase(d))
        else:
            phrases.append(f"your {_file_type_word(d)}")
    if len(phrases) == 2:
        joined = f"{phrases[0]} and {phrases[1]}"
    else:
        joined = ", ".join(phrases[:-1]) + f", and {phrases[-1]}"

    lead = (
        f"{joined[0].upper()}{joined[1:]} {'are' if len(phrases) > 1 else 'is'} ready"
    )

    all_questions = []
    if pending_questions:
        for d in display_docs:
            pq = pending_questions.get(d)
            if pq:
                all_questions.append(pq)

    if all_questions:
        msg = f"{lead}. You asked: {'; '.join(all_questions)}."
        all_descs = []
        if doc_descriptions:
            for d in display_docs:
                desc = (doc_descriptions or {}).get(d)
                if desc:
                    all_descs.append(desc)
        if all_descs:
            msg += f" They cover {'; '.join(all_descs)}."
        msg += " Ask me anything about them."
        return msg

    all_descs = []
    if doc_descriptions:
        for d in display_docs:
            desc = (doc_descriptions or {}).get(d)
            if desc:
                all_descs.append(desc)
    if all_descs:
        return f"{lead}. They cover {'; '.join(all_descs)}. Ask me anything about them."
    return f"{lead}. Ask me anything about them."


async def _generate_ready_message(
    *,
    agent: Any,
    vault_action: Any,
    internal_doc_name: str,
    display_doc: str,
    utterance: str,
    doc_description: Optional[str] = None,
) -> Optional[str]:
    """One PageIndex search + one call_model for a single notification message.

    When the user had a pending question, the reply must: (1) say ready,
    (2) remind them of the question, (3) answer from excerpts. Returns
    generated text, or None on failure.
    """
    page_index = await agent.get_action_by_type("PageIndexAction")
    if page_index is None:
        return None

    try:
        results = await page_index.search(
            query=utterance,
            doc_name=internal_doc_name,
            access_control=False,
        )
    except Exception:
        return None

    excerpts = _format_search_excerpts(results)
    if not excerpts.strip():
        excerpts = "(no excerpts retrieved)"

    kind = _file_kind_label(display_doc)
    type_word = _file_type_word(display_doc)

    name_guidance = (
        f"The filename is '{display_doc}'. Refer to the document using "
        f"'{type_word}' (e.g. 'your {type_word}') unless the filename is "
        f"clearly meaningful and descriptive — if it is a machine hash, a "
        f"short generic name like 'edit' or 'file', or looks auto-generated, "
        f"use the type word only and do not quote the filename."
    )

    has_question = bool((utterance or "").strip())
    system_parts = [
        "You write a single concise reply. Follow these rules exactly:",
        f"- Briefly state that the {kind} is ready (e.g. 'Your {type_word} is ready'). {name_guidance} Never call it a 'file'.",
    ]
    if has_question:
        system_parts.extend(
            [
                "- Then remind the user of their pending question by quoting or "
                "briefly paraphrasing it (e.g. 'You asked about …').",
                "- Then answer that question using only the provided excerpts. "
                "Keep the answer short — one or two sentences.",
                "- Structure the message in that exact order: (1) ready notice, "
                "(2) remind them of their question, (3) the answer.",
            ]
        )
    else:
        system_parts.append(
            "- The user did NOT ask a content question. Just say the document "
            "is ready and invite them to ask. Do not invent an answer."
        )
    system_parts.extend(
        [
            "- Never invent facts. If the excerpts do not contain the answer, say so simply.",
            "- No greetings, no corporate closers, no filler.",
        ]
    )
    if doc_description:
        system_parts.append(
            f"- The document description is: {doc_description}. You may briefly reference this."
        )
    system_prompt = "\n".join(system_parts)

    user_parts = [
        f"Kind: {kind}",
        f"Type word: {type_word}",
        f"Filename: {display_doc}",
    ]
    if doc_description:
        user_parts.append(f"Document description: {doc_description}")
    if has_question:
        user_parts.append(f"\nUser pending question: {utterance}")
        user_parts.append(
            f"\nSearch excerpts for doc_name={internal_doc_name!r}:\n{excerpts}"
        )
        user_parts.append(
            "\nWrite one short message: ready → remind question → answer."
        )
    else:
        user_parts.append(
            "\nNo pending question. Write one short ready notice."
        )
    user_prompt = "\n".join(user_parts)

    try:
        from jvagent.action.utils.call_model import call_model

        text = await call_model(vault_action, user_prompt, system_prompt)
    except Exception:
        return None

    if not isinstance(text, str) or not text.strip():
        return None
    return text.strip()


async def _generate_ready_message_multi(
    *,
    agent: Any,
    vault_action: Any,
    ready_entries: List[Dict[str, Any]],
    doc_descriptions: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Generate a consolidated ready message for multiple documents.

    Searches each doc that has a pending_question, then builds a single
    call_model prompt covering all docs. Falls back to None on failure.
    """
    if not ready_entries:
        return None

    display_docs: List[str] = []
    doc_kinds: List[str] = []
    search_parts: List[str] = []
    questions: List[str] = []

    page_index = await agent.get_action_by_type("PageIndexAction")

    for entry in ready_entries:
        internal = str(entry.get("internal_doc_name") or "").strip()
        display = str(entry.get("display_doc") or "").strip() or "your document"
        pq = str(entry.get("pending_question") or "").strip()

        display_docs.append(display)
        doc_kinds.append(_file_kind_label(display))

        if page_index is not None and pq and internal:
            try:
                results = await page_index.search(
                    query=pq,
                    doc_name=internal,
                    access_control=False,
                )
                excerpts = _format_search_excerpts(results)
                if not excerpts.strip():
                    excerpts = "(no excerpts retrieved)"
            except Exception:
                excerpts = "(search failed)"
            search_parts.append(f"doc_name={internal!r} ({display}):\n{excerpts}")
            questions.append(f"- About {display}: {pq}")

    if not display_docs:
        return None

    kinds_label = (
        "images"
        if all(k == "image" for k in doc_kinds)
        else ("documents" if all(k == "document" for k in doc_kinds) else "files")
    )
    phrases: List[str] = []
    for d in display_docs:
        phrases.append(f"your {_file_type_word(d)}")
    if len(phrases) == 1:
        joined = phrases[0]
        is_plural = False
    elif len(phrases) == 2:
        joined = f"{phrases[0]} and {phrases[1]}"
        is_plural = True
    else:
        joined = ", ".join(phrases[:-1]) + f", and {phrases[-1]}"
        is_plural = True

    ready_line = (
        f"{joined[0].upper()}{joined[1:]} {'are' if is_plural else 'is'} ready."
    )

    filenames_line = ", ".join(repr(d) for d in display_docs)
    system_parts = [
        "You write natural replies. Follow these rules exactly:",
        f"- Always tell the user their {kinds_label} {'are' if is_plural else 'is'} ready. "
        f"Refer to each document by its type word (e.g. 'your PDF', 'your image') "
        f"unless the filename is clearly meaningful and descriptive — if a "
        f"filename is a machine hash, a short generic name like 'edit' or 'file', "
        f"or looks auto-generated, use the type word only and do not quote it. "
        f"The filenames are: {filenames_line}. Never call them 'files'.",
    ]
    if questions:
        system_parts.extend(
            [
                "- Then remind the user of each pending question by quoting or "
                "briefly paraphrasing it (e.g. 'You asked about …').",
                "- Then answer each pending question using only the provided "
                "excerpts. A few short sentences is fine.",
                "- Structure the message in that exact order: (1) ready notice, "
                "(2) remind them of their question(s), (3) the answer(s).",
            ]
        )
    else:
        system_parts.append(
            "- No pending questions. Just the ready notice and invite them to ask. "
            "Do not invent answers from excerpts."
        )
    if doc_descriptions:
        desc_items = [
            f"{d}: {desc}"
            for d, desc in doc_descriptions.items()
            if desc and d in display_docs
        ]
        if desc_items:
            system_parts.append(
                "- Document descriptions: "
                + "; ".join(desc_items)
                + ". Briefly reference these when announcing readiness."
            )
    system_parts.append("- Never invent facts.")
    system_parts.append("- No greetings, no corporate or support-bot closers.")
    system_prompt = "\n".join(system_parts)

    user_parts = [
        f"Ready {kinds_label}: {ready_line}",
    ]
    if doc_descriptions:
        desc_lines = [
            f"  {d}: {desc}"
            for d, desc in doc_descriptions.items()
            if desc and d in display_docs
        ]
        if desc_lines:
            user_parts.append("\nDocument descriptions:")
            user_parts.extend(desc_lines)
    if questions:
        user_parts.append("")
        user_parts.append("Pending questions:")
        user_parts.extend(questions)
        user_parts.append("")
        user_parts.append("Search excerpts:")
        user_parts.extend(search_parts)
        user_parts.append("")
        user_parts.append(
            "Write one message: ready → remind question(s) → answer(s)."
        )
    else:
        user_parts.append("")
        user_parts.append("No pending questions. Write one short ready notice.")

    user_prompt = "\n".join(user_parts)

    try:
        from jvagent.action.utils.call_model import call_model

        text = await call_model(vault_action, user_prompt, system_prompt)
    except Exception:
        return None

    if not isinstance(text, str) or not text.strip():
        return None
    return text.strip()


@endpoint(
    "/artifact_handler_action/notify/{agent_id}",
    methods=["POST"],
    webhook=True,
    auth=False,
    webhook_auth="api_key",
    tags=["ArtifactHandlerInteractAction"],
    summary="jvforge async ingest completion callback (import + notification)",
    description=(
        "Authenticate with **api_key** query parameter or header. "
        "jvforge POSTs ``process_document_url`` when an async ingest job finishes."
    ),
    response=success_response(
        data={
            "status": ResponseField(str, example="imported"),
            "job_id": ResponseField(str, example="abc-123"),
            "notified": ResponseField(bool, example=True),
        }
    ),
)
async def artifact_handler_notify(request: Request, agent_id: str):
    """Receive ``process_document_url`` from jvforge, import the graph, and
    send a ready notification.

    Expected payload::

        {
          "process_document_url": "<https://jvforge/v1/artifacts/{job_id}>",
          "job_id": "<str>",
          "doc_name": "<str|null>"
        }

    Flow:
        1. Resolve the ArtifactHandlerInteractAction; bind API key to this agent.
        2. Require a known ``job_id`` in the reverse index (blocks replay/spam import).
        3. Download artifact from ``process_document_url`` and import into PageIndex.
        4. Mark the job as ``ready`` in conversation ``pending_ingest_jobs``.
        5. For WhatsApp/Messenger: send ready notice + optional answer.
        6. Return 200 on success, 503 + Retry-After on failure (so jvforge retries).
    """
    import hmac

    try:
        payload = await request.json()
    except Exception:
        try:
            raw = await request.body()
            payload = json.loads(raw or b"{}")
        except Exception:
            payload = {}

    if not isinstance(payload, dict):
        payload = {}

    process_document_url = str(payload.get("process_document_url") or "").strip()
    job_id = str(payload.get("job_id") or "").strip()
    doc_name = str(payload.get("doc_name") or "").strip()

    if not process_document_url:
        return JSONResponse(
            status_code=400,
            content={"detail": "process_document_url is required"},
        )
    if not job_id:
        return JSONResponse(
            status_code=400,
            content={"detail": "job_id is required"},
        )

    action = await _resolve_action(agent_id)
    if action is None:
        return JSONResponse(
            status_code=503,
            content={"detail": "action not available"},
            headers={"Retry-After": str(_RETRY_AFTER_SECONDS)},
        )

    # Bind the presented API key to this action's minted notify key so a key
    # minted for agent A cannot drive imports on agent B (jvspatial endpoint
    # allowlists are prefix-based; exact-path minting alone is not enough when
    # agent ids share a common prefix).
    user = getattr(request.state, "user", None) or {}
    api_key_id = (
        str(user.get("api_key_id") or "").strip() if isinstance(user, dict) else ""
    )
    expected_key = str(getattr(action, "notify_webhook_api_key_id", None) or "").strip()
    if (
        not expected_key
        or not api_key_id
        or not hmac.compare_digest(expected_key, api_key_id)
    ):
        return JSONResponse(
            status_code=403,
            content={"detail": "API key not authorized for this agent"},
        )

    from jvagent.core.agent import Agent

    agent = await Agent.get(agent_id)
    if agent is None:
        return JSONResponse(
            status_code=503,
            content={"detail": "agent not found"},
            headers={"Retry-After": str(_RETRY_AFTER_SECONDS)},
        )

    # Job lookup BEFORE download/import — unknown / cleared jobs must not
    # trigger expensive PageIndex writes (replay / forged callbacks).
    entry = await action.lookup_job(job_id)
    if not entry:
        return JSONResponse(
            status_code=404,
            content={"detail": "unknown or already-cleared job_id"},
        )

    entry_agent = str(entry.get("agent_id") or "").strip()
    if entry_agent and entry_agent != agent_id:
        return JSONResponse(
            status_code=403,
            content={"detail": "job_id does not belong to this agent"},
        )

    # Idempotent success when we already finished notifying for this job.
    if entry.get("notified"):
        return {
            "status": "already_imported",
            "job_id": job_id,
            "notified": True,
            "doc_name": str(entry.get("doc_name") or doc_name or ""),
        }

    imported_doc_name = await _download_and_import_graph(process_document_url, agent_id)
    if not imported_doc_name:
        return JSONResponse(
            status_code=503,
            content={"detail": "graph import failed"},
            headers={"Retry-After": str(_RETRY_AFTER_SECONDS)},
        )

    user_id = str(entry.get("user_id") or "").strip()
    session_id = str(entry.get("session_id") or "").strip()
    conversation_id = str(entry.get("conversation_id") or "").strip()
    channel = str(entry.get("channel") or "").strip().lower() or "default"
    logger.info(
        "artifact_handler_notify: job_id=%s channel=%s user_id=%s doc=%s",
        job_id,
        channel,
        user_id,
        doc_name,
    )
    # Prefer PageIndex import name; fall back to vault job name normalized the
    # same way PageIndex does (strip_redundant_md_suffix).
    vault_doc_name = str(entry.get("doc_name") or doc_name or "").strip()
    try:
        from jvagent.action.pageindex.adapter import strip_redundant_md_suffix
    except Exception:
        strip_redundant_md_suffix = None  # type: ignore[assignment]
    if imported_doc_name:
        internal_doc_name = str(imported_doc_name).strip()
    elif vault_doc_name and strip_redundant_md_suffix:
        internal_doc_name = strip_redundant_md_suffix(vault_doc_name) or vault_doc_name
    else:
        internal_doc_name = vault_doc_name
    display_doc = _display_doc_name(entry, doc_name)
    pending_question = str(entry.get("pending_question") or "").strip()

    # ── Mark the job as ready in conversation context.
    if conversation_id:
        try:
            from jvagent.memory.conversation import Conversation

            conv = await Conversation.get(conversation_id)
            if conv is not None:
                ctx = getattr(conv, "context", None)
                if isinstance(ctx, dict):
                    vault = ctx.get("artifact_handler")
                    if isinstance(vault, dict):
                        pending = vault.get("pending_ingest_jobs")
                        if isinstance(pending, dict) and job_id in pending:
                            job_entry = pending[job_id]
                            if isinstance(job_entry, dict):
                                prev_status = str(job_entry.get("status") or "").lower()
                                if (
                                    prev_status in _PROCESSING_STATUSES
                                    or prev_status == ""
                                ):
                                    job_entry["status"] = "ready"
                                    job_entry["ready_at"] = time.time()
                                    if internal_doc_name:
                                        job_entry["doc_name"] = internal_doc_name
                                        vault["active_doc_name"] = internal_doc_name
                                    await conv.update_context(
                                        {"artifact_handler": vault}
                                    )
        except Exception:
            pass
    # ── Send proactive notifications.
    # WhatsApp and Messenger get push messages; web/default relies on
    # check_ingest_status polling (TODO: add web push in a future phase).
    if user_id and channel == "whatsapp":
        asyncio.create_task(
            _send_whatsapp_notifications(
                agent_id=agent_id,
                job_id=job_id or "",
                user_id=user_id,
                session_id=session_id,
                conversation_id=conversation_id,
                internal_doc_name=internal_doc_name,
                display_doc=display_doc,
                pending_question=pending_question,
            )
        )
    elif user_id and channel == "messenger":
        asyncio.create_task(
            _send_messenger_notifications(
                agent_id=agent_id,
                job_id=job_id or "",
                user_id=user_id,
                session_id=session_id,
                conversation_id=conversation_id,
                internal_doc_name=internal_doc_name,
                display_doc=display_doc,
                pending_question=pending_question,
            )
        )

    # ── Mark notified + clear from jvforge reverse index.
    if action is not None and job_id:
        try:
            await action.mark_notified(job_id)
            await action.clear_job(job_id)
        except Exception:
            pass
    return {
        "status": "imported",
        "job_id": job_id,
        "notified": channel in ("whatsapp", "messenger") and bool(user_id),
        "doc_name": imported_doc_name,
    }


async def _send_whatsapp_notifications(
    *,
    agent_id: str,
    job_id: str,
    user_id: str,
    session_id: str,
    conversation_id: str,
    internal_doc_name: str,
    display_doc: str,
    pending_question: str,
) -> None:
    """Send a single WhatsApp notification: ready notice, or ready + answer."""
    try:
        from jvagent.core.agent import Agent

        agent = await Agent.get(agent_id)
        if agent is None:
            return

        action = await _resolve_action(agent_id)

        single_entry = {
            "internal_doc_name": internal_doc_name,
            "display_doc": display_doc,
            "pending_question": pending_question,
        }
        desc_lookup: Dict[str, str] = {}
        try:
            desc_lookup = await _doc_description_lookup(agent, [single_entry])
        except Exception:
            pass
        doc_description = desc_lookup.get(internal_doc_name, "")

        content: Optional[str] = None
        answered = False

        if pending_question and internal_doc_name and action is not None:
            content = await _generate_ready_message(
                agent=agent,
                vault_action=action,
                internal_doc_name=internal_doc_name,
                display_doc=display_doc,
                utterance=pending_question,
                doc_description=doc_description or None,
            )
            if content:
                answered = True

        if not content:
            content = _canned_ready_message(
                display_doc,
                doc_description=doc_description,
                pending_question=pending_question or None,
            )

        await _publish_whatsapp_message(
            agent=agent,
            user_id=user_id,
            session_id=session_id,
            conversation_id=conversation_id,
            content=content,
            display_doc=display_doc,
            job_id=job_id,
            answered=answered,
        )
    except Exception:
        logger.error(
            "_send_whatsapp_notifications: unexpected error agent_id=%s " "job_id=%s",
            agent_id,
            job_id,
            exc_info=True,
        )


async def _send_messenger_notifications(
    *,
    agent_id: str,
    job_id: str,
    user_id: str,
    session_id: str,
    conversation_id: str,
    internal_doc_name: str,
    display_doc: str,
    pending_question: str,
) -> None:
    """Send a single Messenger notification: ready notice, or ready + answer."""
    logger.info(
        "_send_messenger_notifications: starting agent_id=%s job_id=%s "
        "user_id=%s doc=%s",
        agent_id,
        job_id,
        user_id,
        display_doc,
    )
    try:
        from jvagent.core.agent import Agent

        agent = await Agent.get(agent_id)
        if agent is None:
            logger.warning(
                "_send_messenger_notifications: agent not found agent_id=%s",
                agent_id,
            )
            return

        action = await _resolve_action(agent_id)

        single_entry = {
            "internal_doc_name": internal_doc_name,
            "display_doc": display_doc,
            "pending_question": pending_question,
        }
        desc_lookup: Dict[str, str] = {}
        try:
            desc_lookup = await _doc_description_lookup(agent, [single_entry])
        except Exception:
            pass
        doc_description = desc_lookup.get(internal_doc_name, "")

        content: Optional[str] = None
        answered = False

        if pending_question and internal_doc_name and action is not None:
            content = await _generate_ready_message(
                agent=agent,
                vault_action=action,
                internal_doc_name=internal_doc_name,
                display_doc=display_doc,
                utterance=pending_question,
                doc_description=doc_description or None,
            )
            if content:
                answered = True

        if not content:
            content = _canned_ready_message(
                display_doc,
                doc_description=doc_description,
                pending_question=pending_question or None,
            )

        logger.info(
            "_send_messenger_notifications: publishing to user_id=%s "
            "answered=%s content_len=%d",
            user_id,
            answered,
            len(content) if content else 0,
        )
        await _publish_messenger_message(
            agent=agent,
            user_id=user_id,
            session_id=session_id,
            conversation_id=conversation_id,
            content=content,
            display_doc=display_doc,
            job_id=job_id,
            answered=answered,
        )
    except Exception:
        logger.error(
            "_send_messenger_notifications: unexpected error agent_id=%s job_id=%s",
            agent_id,
            job_id,
            exc_info=True,
        )
