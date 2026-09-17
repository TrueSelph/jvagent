"""Ready-notice + pending-question answers from PageIndex document content.

Shared by the WhatsApp/Messenger notify webhook (proactive push) and
``check_ingest_status`` (other-channel poll). Chunks first; search only
when chunks are empty.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

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

_READY_DOC_TEXT_MAX_CHARS = 12000


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


def _format_content_parts(rows: Any) -> str:
    """Turn PageIndex rows or chunks into plain text for the LLM prompt."""
    if not rows:
        return ""
    if isinstance(rows, dict):
        rows = rows.get("results") or rows.get("documents") or rows.get("chunks") or []
    if not isinstance(rows, list):
        return str(rows)
    parts: List[str] = []
    for r in rows:
        if not isinstance(r, dict):
            parts.append(str(r))
            continue
        content = str(
            r.get("content")
            or r.get("text")
            or r.get("summary")
            or r.get("title")
            or ""
        ).strip()
        title = str(r.get("title") or "").strip()
        if title and content and title != content:
            parts.append(f"- [{title}] {content}")
        elif content:
            parts.append(f"- {content}")
    return "\n".join(parts)


def _format_search_excerpts(results: Any) -> str:
    """Turn PageIndex search results into plain text for the LLM prompt."""
    return _format_content_parts(results)


def _useful_source_text(text: str) -> bool:
    """True when text can ground an answer (not empty / placeholder)."""
    s = (text or "").strip()
    if not s:
        return False
    lowered = s.lower()
    if lowered in ("(no excerpts retrieved)", "(search failed)"):
        return False
    return True


def _truncate_ready_text(text: str, max_chars: int = _READY_DOC_TEXT_MAX_CHARS) -> str:
    """Cap document text so a large PDF cannot blow the notify prompt."""
    s = (text or "").strip()
    if max_chars <= 0 or len(s) <= max_chars:
        return s
    cut = s[:max_chars]
    nl = cut.rfind("\n")
    if nl >= max_chars // 2:
        cut = cut[:nl]
    return cut.rstrip() + "\n…"


def _empty_content_ready_message(display_doc: str, pending_question: str) -> str:
    """Ready notice when PageIndex has no readable body for the pending question."""
    type_word = _file_type_word(display_doc)
    if _should_quote_filename(display_doc):
        phrase = _friendly_file_phrase(display_doc)
        lead = f"{phrase[0].upper()}{phrase[1:]} is ready"
    else:
        lead = f"Your {type_word} is ready"
    question = (pending_question or "").strip()
    if question:
        return f"{lead}. You asked: {question}. " "I couldn't read any content from it."
    return f"{lead}. I couldn't read any content from it."


def _pageindex_collection(agent: Any, page_index: Any = None) -> str:
    """Collection name for the ready document (typically agent id)."""
    if page_index is not None:
        try:
            collection = str(page_index.resolve_collection() or "").strip()
            if collection:
                return collection
        except Exception:
            collection = str(getattr(page_index, "agent_id", "") or "").strip()
            if collection:
                return collection
    return str(getattr(agent, "id", "") or "").strip()


async def _load_document_content(
    agent: Any,
    internal_doc_name: str,
    page_index: Any = None,
) -> Tuple[str, bool]:
    """Load PageIndex chunk text for a known doc_name.

    Returns ``(text, loaded)``. ``loaded`` is True when the chunk list call
    succeeded (even if the body is empty).
    """
    name = (internal_doc_name or "").strip()
    if not name:
        return "", False
    try:
        from jvagent.action.pageindex.documents import list_document_chunks
    except Exception:
        return "", False
    collection = _pageindex_collection(agent, page_index)
    if not collection:
        return "", False
    try:
        out = await list_document_chunks(name, collection, per_page=0)
    except Exception:
        return "", False
    text = _truncate_ready_text(_format_content_parts(out))
    return text, True


async def _search_document_content(
    page_index: Any,
    query: str,
    internal_doc_name: str,
) -> Tuple[str, bool]:
    """Scoped PageIndex search; supplement when chunk load is empty."""
    if page_index is None or not (internal_doc_name or "").strip():
        return "", False
    q = (query or "").strip() or internal_doc_name
    try:
        results = await page_index.search(
            query=q,
            doc_name=internal_doc_name,
            access_control=False,
        )
    except Exception:
        return "", False
    text = _truncate_ready_text(_format_search_excerpts(results))
    return text, True


async def _ready_document_text(
    agent: Any,
    page_index: Any,
    internal_doc_name: str,
    query: str,
) -> Tuple[str, bool]:
    """Document body for a ready-notify answer: chunks first, search if empty."""
    text, loaded = await _load_document_content(agent, internal_doc_name, page_index)
    if _useful_source_text(text):
        return text, True
    search_text, searched = await _search_document_content(
        page_index, query, internal_doc_name
    )
    if _useful_source_text(search_text):
        return search_text, True
    return "", loaded or searched


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
    """One ready notification: load the ingested document + call_model.

    When the user had a pending question, the reply must: (1) say ready,
    (2) remind them of the question, (3) answer from PageIndex document
    content (chunks first; search only if chunks are empty). Returns
    generated text, or None on failure.
    """
    kind = _file_kind_label(display_doc)
    has_question = bool((utterance or "").strip())
    type_word = _file_type_word(display_doc)

    page_index = None
    try:
        page_index = await agent.get_action_by_type("PageIndexAction")
    except Exception:
        page_index = None

    source_text = ""
    if has_question:
        if not (internal_doc_name or "").strip():
            return None
        source_text, reached_pageindex = await _ready_document_text(
            agent, page_index, internal_doc_name, utterance
        )
        if not _useful_source_text(source_text):
            if reached_pageindex:
                return _empty_content_ready_message(display_doc, utterance)
            return None
    elif page_index is None:
        return None

    name_guidance = (
        f"The filename is '{display_doc}'. Refer to the document using "
        f"'{type_word}' (e.g. 'your {type_word}') unless the filename is "
        f"clearly meaningful and descriptive — if it is a machine hash, a "
        f"short generic name like 'edit' or 'file', or looks auto-generated, "
        f"use the type word only and do not quote the filename."
    )

    system_parts = [
        "You write a single concise reply. Follow these rules exactly:",
        f"- Briefly state that the {kind} is ready (e.g. 'Your {type_word} is ready'). {name_guidance} Never call it a 'file'.",
    ]
    if has_question:
        system_parts.extend(
            [
                "- Then remind the user of their pending question by quoting or "
                "briefly paraphrasing it (e.g. 'You asked about …').",
                "- Then answer that question using the provided document content. "
                "Keep the answer short — one or two sentences.",
                "- Structure the message in that exact order: (1) ready notice, "
                "(2) remind them of their question, (3) the answer.",
                "- Never invent facts. Do not mention excerpts, search, or "
                "processing internals.",
            ]
        )
    else:
        system_parts.append(
            "- The user did NOT ask a content question. Just say the document "
            "is ready and invite them to ask. Do not invent an answer."
        )
        system_parts.append("- Never invent facts.")
    system_parts.append("- No greetings, no corporate closers, no filler.")
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
            f"\nDocument content for doc_name={internal_doc_name!r}:\n{source_text}"
        )
        user_parts.append(
            "\nWrite one short message: ready → remind question → answer."
        )
    else:
        user_parts.append("\nNo pending question. Write one short ready notice.")
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

    Loads each ready doc's PageIndex chunks (search only if chunks are
    empty). Falls back to None on failure.
    """
    if not ready_entries:
        return None

    display_docs: List[str] = []
    doc_kinds: List[str] = []
    content_parts: List[str] = []
    questions: List[str] = []
    any_content = False
    any_reached = False

    page_index = None
    try:
        page_index = await agent.get_action_by_type("PageIndexAction")
    except Exception:
        page_index = None

    for entry in ready_entries:
        internal = str(entry.get("internal_doc_name") or "").strip()
        display = str(entry.get("display_doc") or "").strip() or "your document"
        pq = str(entry.get("pending_question") or "").strip()
        kind = _file_kind_label(display)

        display_docs.append(display)
        doc_kinds.append(kind)

        if not pq:
            continue

        questions.append(f"- About {display}: {pq}")
        source_text = ""
        reached = False
        if internal:
            source_text, reached = await _ready_document_text(
                agent, page_index, internal, pq
            )
            any_reached = any_reached or reached
        if _useful_source_text(source_text):
            any_content = True
            content_parts.append(f"doc_name={internal!r} ({display}):\n{source_text}")

    if not display_docs:
        return None

    if questions and not any_content:
        if any_reached and len(display_docs) == 1:
            return _empty_content_ready_message(
                display_docs[0],
                str(ready_entries[0].get("pending_question") or "").strip(),
            )
        if any_reached:
            asked = "; ".join(
                str(e.get("pending_question") or "").strip()
                for e in ready_entries
                if str(e.get("pending_question") or "").strip()
            )
            type_words = [f"your {_file_type_word(d)}" for d in display_docs]
            if len(type_words) == 2:
                joined = f"{type_words[0]} and {type_words[1]}"
            else:
                joined = ", ".join(type_words[:-1]) + f", and {type_words[-1]}"
            lead = f"{joined[0].upper()}{joined[1:]} are ready"
            if asked:
                return f"{lead}. You asked: {asked}. I couldn't read any content from them."
            return f"{lead}. I couldn't read any content from them."
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
                "- Then answer each pending question using the provided document "
                "content. A few short sentences is fine.",
                "- Structure the message in that exact order: (1) ready notice, "
                "(2) remind them of their question(s), (3) the answer(s).",
            ]
        )
        facts_rule = (
            "- Never invent facts. Do not mention excerpts, search, or "
            "processing internals."
        )
    else:
        system_parts.append(
            "- No pending questions. Just the ready notice and invite them to ask. "
            "Do not invent answers from document content."
        )
        facts_rule = "- Never invent facts."
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
    system_parts.append(facts_rule)
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
        if content_parts:
            user_parts.append("")
            user_parts.append("Document content:")
            user_parts.extend(content_parts)
        user_parts.append("")
        user_parts.append("Write one message: ready → remind question(s) → answer(s).")
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
