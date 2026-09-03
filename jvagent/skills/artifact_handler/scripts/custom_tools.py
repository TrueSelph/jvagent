"""Custom tools for the artifact_handler skill.

Ingest path (per the skill's design):

**URL** the user typed in text → passed to
``ArtifactHandlerInteractAction.submit_ingest`` (jvforge async when
``JVAGENT_JVFORGE_BASE_URL`` is set) or ``PageIndexAction.assimilate``
(sync fallback).

Media attachments (uploaded files) are handled by
``ArtifactHandlerInteractAction.execute``, which auto-detects attachments on
``visitor.data`` and ingests them before the LLM sees the message. This
tool (``ingest_document``) handles URL-based ingestion only.

Async path: ``ArtifactHandlerInteractAction.submit_ingest`` registers the job
in the action's reverse-index and gives jvforge a ``notification_url``
pointing at ``POST /api/artifact_handler_action/notify/{agent_id}``. Sync
fallback (no jvforge base URL) still calls
``PageIndexAction.assimilate`` directly.

The LLM-facing entry point is ``ingest_document``: the model calls it when
the user types a URL. An optional ``question`` is saved with the job and
answered when the document is ready (WhatsApp notify generates the reply
in-process via PageIndex search + call_model, or web via
``check_ingest_status``).

Access is always ``private_<user_id>``; the matching access-control group
is created idempotently so the same user can later search their own docs.

Documents expire after a default retention window (do not surface the
duration to users). The vault index lives on
``conversation.context["artifact_handler"]["private_<user_id>"]``. Pending
async jobs live on
``conversation.context["artifact_handler"]["pending_ingest_jobs"]``.
The last-discussed / newest doc for follow-up selection lives on
``conversation.context["artifact_handler"]["active_doc_name"]``. On every
``ingest_document`` / ``list_my_documents`` / ``review_expired`` /
``check_ingest_status`` call, pending jobs are refreshed by checking
``PageIndexAction.list_documents`` with access control and newly-expired
docs are surfaced.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

_SKILL_NAME = "artifact_handler"

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

_MACHINE_FILENAME_RE = re.compile(r"^\d{8}_\d{6}_[0-9a-fA-F]{6,}(\.[A-Za-z0-9]+)?$")

_O_USER_PREFIX_RE = re.compile(r"^o_User_[a-zA-Z0-9]+_")


def _file_kind_label(doc_name: str) -> str:
    """Return 'image' or 'document' based on filename extension."""
    name = (doc_name or "").strip().lower()
    _, _, ext = name.rpartition(".")
    if ext and f".{ext}" in _IMAGE_EXTENSIONS:
        return "image"
    return "document"


def _display_doc_name(doc_name: str) -> str:
    """Strip the ``o_User_xxx_`` prefix from a doc_name to get a readable filename.

    Falls back to the full doc_name if no prefix is found.
    """
    name = (doc_name or "").strip()
    if not name:
        return "your document"
    m = _O_USER_PREFIX_RE.match(name)
    if m:
        rest = name[m.end() :]
        if rest and "." in rest:
            return rest
    if "_" in name:
        _, _, rest = name.partition("_")
        if rest and "." in rest:
            return rest
    return name


def _friendly_file_phrase(doc_name: str) -> str:
    """Natural file reference, e.g. ``your image`` or ``your document 'report.pdf'``.

    Machine / hash basenames from WhatsApp are not quoted.
    """
    display = _display_doc_name(doc_name)
    kind = _file_kind_label(display)
    if (
        not display
        or display.lower() in ("document", "your document", "uploaded_file", "image")
        or _MACHINE_FILENAME_RE.match(display)
    ):
        return f"your {kind}"
    return f"your {kind} '{display}'"


# Retention window (seconds). Duration is internal — never mention it to users.
_DEFAULT_RETENTION_SECONDS = 30 * 86400

# Conversation context keys.
_VAULT_CTX_KEY = "artifact_handler"
_PENDING_JOBS_KEY = "pending_ingest_jobs"
_ACTIVE_DOC_KEY = "active_doc_name"

# Terminal failure statuses for pending ingest jobs in conversation context.
_FAILED_JOB_STATUSES = frozenset(
    {"failed", "error", "cancelled", "canceled", "webhook_failed"}
)

# Job statuses that mean "still processing" (not done, not failed).
_PROCESSING_STATUSES = frozenset({"queued", "processing", "pending", "submitted"})


# ─── Helpers ──────────────────────────────────────────────────────────


async def _get_conversation(visitor: Any) -> Any:
    """Resolve the visitor's conversation object (canonical pattern)."""
    if visitor is None:
        return None
    if hasattr(visitor, "conversation") and visitor.conversation is not None:
        return visitor.conversation
    interaction = getattr(visitor, "interaction", None)
    if interaction is not None and hasattr(interaction, "get_conversation"):
        try:
            return await interaction.get_conversation()
        except Exception:
            pass
    return None


def _resolve_session_id(visitor: Any) -> Optional[str]:
    """Return the visitor's session_id, stripped, or None."""
    sid = getattr(visitor, "session_id", None)
    if sid is None:
        return None
    sid = str(sid).strip()
    return sid or None


def _resolve_user_id(visitor: Any) -> Optional[str]:
    """Return the visitor's user_id, stripped, or None."""
    uid = getattr(visitor, "user_id", None)
    if uid is None:
        return None
    uid = str(uid).strip()
    return uid or None


def _group_key(user_id: str) -> str:
    """Access tag / group name for this user."""
    return f"private_{user_id}"


def _vault_dict(conversation: Any) -> Dict[str, Any]:
    """Return the artifact_handler context dict (or empty)."""
    if conversation is None:
        return {}
    ctx = getattr(conversation, "context", None)
    if not isinstance(ctx, dict):
        return {}
    vault = ctx.get(_VAULT_CTX_KEY)
    return dict(vault) if isinstance(vault, dict) else {}


def _read_vault(conversation: Any, group_key: str) -> List[Dict[str, Any]]:
    """Read this session's vault entries from conversation context."""
    vault = _vault_dict(conversation)
    entries = vault.get(group_key)
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict)]


async def _write_vault(
    conversation: Any, group_key: str, entries: List[Dict[str, Any]]
) -> None:
    """Persist this session's vault entries via conversation.update_context."""
    if conversation is None:
        return
    ctx = getattr(conversation, "context", None)
    if not isinstance(ctx, dict):
        ctx = {}
        try:
            await conversation.update_context(ctx)
        except Exception:
            pass
        ctx = getattr(conversation, "context", None)
        if not isinstance(ctx, dict):
            return
    vault = ctx.get(_VAULT_CTX_KEY)
    if not isinstance(vault, dict):
        vault = {}
    else:
        vault = dict(vault)
    vault[group_key] = entries
    try:
        await conversation.update_context({_VAULT_CTX_KEY: vault})
    except Exception:
        pass


def _read_pending_jobs(conversation: Any) -> Dict[str, Dict[str, Any]]:
    """Read pending_ingest_jobs from conversation context."""
    vault = _vault_dict(conversation)
    pending = vault.get(_PENDING_JOBS_KEY)
    if not isinstance(pending, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for jid, entry in pending.items():
        if isinstance(entry, dict) and jid:
            out[str(jid)] = dict(entry)
    return out


async def _set_active_doc_name(conversation: Any, doc_name: str) -> None:
    """Persist the last-discussed / newest vault doc for follow-up selection."""
    name = str(doc_name or "").strip()
    if conversation is None or not name:
        return
    ctx = getattr(conversation, "context", None)
    if not isinstance(ctx, dict):
        try:
            await conversation.update_context({})
        except Exception:
            pass
        ctx = getattr(conversation, "context", None)
        if not isinstance(ctx, dict):
            return
    vault = ctx.get(_VAULT_CTX_KEY)
    if not isinstance(vault, dict):
        vault = {}
    else:
        vault = dict(vault)
    vault[_ACTIVE_DOC_KEY] = name
    try:
        await conversation.update_context({_VAULT_CTX_KEY: vault})
    except Exception:
        pass


async def _clear_active_doc_name_if(conversation: Any, doc_name: str) -> None:
    """Clear active_doc_name when it matches a deleted document."""
    name = str(doc_name or "").strip()
    if conversation is None or not name:
        return
    vault = _vault_dict(conversation)
    current = str(vault.get(_ACTIVE_DOC_KEY) or "").strip()
    if current != name:
        return
    ctx = getattr(conversation, "context", None)
    if not isinstance(ctx, dict):
        return
    vault = ctx.get(_VAULT_CTX_KEY)
    if not isinstance(vault, dict):
        return
    vault = dict(vault)
    vault.pop(_ACTIVE_DOC_KEY, None)
    try:
        await conversation.update_context({_VAULT_CTX_KEY: vault})
    except Exception:
        pass


async def _write_pending_jobs(
    conversation: Any, pending: Dict[str, Dict[str, Any]]
) -> None:
    """Persist pending_ingest_jobs under artifact_handler, preserving sibling keys."""
    if conversation is None:
        return
    ctx = getattr(conversation, "context", None)
    if not isinstance(ctx, dict):
        try:
            await conversation.update_context({})
        except Exception:
            pass
        ctx = getattr(conversation, "context", None)
        if not isinstance(ctx, dict):
            return
    vault = ctx.get(_VAULT_CTX_KEY)
    if not isinstance(vault, dict):
        vault = {}
    else:
        vault = dict(vault)
    vault[_PENDING_JOBS_KEY] = pending
    try:
        await conversation.update_context({_VAULT_CTX_KEY: vault})
    except Exception:
        pass


def _now_ts() -> int:
    return int(time.time())


def _humanize_remaining(expires_at: int, now: Optional[int] = None) -> str:
    """Human-readable 'in N days' for the remaining retention window."""
    now = now if now is not None else _now_ts()
    secs = expires_at - now
    if secs <= 0:
        return "expired"
    days = secs // 86400
    if days >= 1:
        return f"in {days} day{'s' if days != 1 else ''}"
    hours = secs // 3600
    if hours >= 1:
        return f"in {hours} hour{'s' if hours != 1 else ''}"
    return "soon"


def _filename_from_url(url: str) -> str:
    """Derive a filename from a URL's path tail (best-effort)."""
    import os as _os
    from urllib.parse import urlparse

    try:
        tail = _os.path.basename(urlparse(url).path)
    except Exception:
        tail = ""
    return tail or "document"


_IMAGE_EXTENSIONS = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".bmp",
        ".webp",
        ".tif",
        ".tiff",
        ".heic",
        ".heif",
    }
)


def _url_is_image(url: str) -> bool:
    """Best-effort check whether a URL points to an image file."""
    import os as _os

    try:
        ext = _os.path.splitext(urlparse(url).path)[1].lower()
        return ext in _IMAGE_EXTENSIONS
    except Exception:
        return False


def _safe_filename_segment(
    filename: Optional[str], *, default: str = "document"
) -> str:
    base = (filename or "").strip() or default
    safe = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in base)[:80]
    return safe or default


def _vault_doc_ids(user_id: str, filename: Optional[str]) -> Tuple[str, str]:
    """Return ``(doc_name, display_filename)`` as ``{user_id}_{original}``.

    ``doc_name`` is PageIndex-normalized via ``strip_redundant_md_suffix``.
    """
    from jvagent.action.pageindex.adapter import strip_redundant_md_suffix

    display = _safe_filename_segment(filename)
    uid = (
        "".join(
            c if c.isalnum() or c in ("-", "_") else "_"
            for c in (user_id or "").strip()
        )[:64]
        or "unknown"
    )
    doc_name = strip_redundant_md_suffix(f"{uid}_{display}") or f"{uid}_{display}"
    return doc_name, display


def _doc_name_owned_by_user(user_id: str, doc_name: str) -> bool:
    """True when *doc_name* carries this user's vault namespace prefix."""
    uid = (
        "".join(
            c if c.isalnum() or c in ("-", "_") else "_"
            for c in (user_id or "").strip()
        )[:64]
        or "unknown"
    )
    prefix = f"{uid}_"
    name = (doc_name or "").strip()
    return bool(name) and name.startswith(prefix)


def _safe_doc_name(user_id: str, idx: int, filename: Optional[str]) -> str:
    """Build a unique doc_name for PageIndex (``{user_id}_{filename}``)."""
    doc_name, _ = _vault_doc_ids(user_id, filename)
    if doc_name.endswith("_document") and not (filename or "").strip():
        return f"{doc_name}_{_now_ts()}_{idx}"
    return doc_name


async def _get_page_index_action(ctx: Any) -> Optional[Any]:
    """Resolve the PageIndexAction via the interview action (lifecycle binder)."""
    interview_action = getattr(ctx, "interview", None)
    if interview_action is None:
        return None
    try:
        return await interview_action.get_action("PageIndexAction")
    except Exception:
        return None


async def _doc_description_lookup(
    ctx: Any, user_id: str, session_id: str
) -> Dict[str, str]:
    """Build a doc_name → doc_description map from PageIndex list_documents."""
    page_index = await _get_page_index_action(ctx)
    if page_index is None:
        return {}
    try:
        docs = await page_index.list_documents(
            access_control=True, user_id=user_id, session_id=session_id, summary=True
        )
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
        if name:
            lookup[name] = desc
    return lookup


async def _get_access_control_action(ctx: Any) -> Optional[Any]:
    """Resolve the AccessControlAction via the interview action."""
    interview_action = getattr(ctx, "interview", None)
    if interview_action is None:
        return None
    try:
        return await interview_action.get_action("AccessControlAction")
    except Exception:
        return None


async def _get_artifact_handler_action(ctx: Any) -> Optional[Any]:
    """Resolve ArtifactHandlerInteractAction via the interview action."""
    interview_action = getattr(ctx, "interview", None)
    if interview_action is None:
        return None
    try:
        return await interview_action.get_action("ArtifactHandlerInteractAction")
    except Exception:
        return None


def _resolve_agent_id(ctx: Any, visitor: Any, dv_action: Any = None) -> str:
    """Best-effort agent id for notification URL / routing."""
    if dv_action is not None:
        aid = getattr(dv_action, "agent_id", None)
        if aid:
            return str(aid)
    agent = getattr(visitor, "_agent", None) if visitor is not None else None
    if agent is not None:
        aid = getattr(agent, "id", None)
        if aid:
            return str(aid)
    interview = getattr(ctx, "interview", None)
    if interview is not None:
        aid = getattr(interview, "agent_id", None)
        if aid:
            return str(aid)
    return ""


def _resolve_routing(visitor: Any, conversation: Any) -> Tuple[str, str, str]:
    """Return ``(user_id, conversation_id, channel)`` for reverse-index."""
    user_id = str(getattr(visitor, "user_id", "") or "") if visitor else ""
    conversation_id = ""
    if conversation is not None:
        conversation_id = str(
            getattr(conversation, "id", None)
            or getattr(conversation, "conversation_id", None)
            or ""
        )
    channel = (
        str(getattr(visitor, "channel", "") or "").strip().lower() if visitor else ""
    ) or "default"
    return user_id, conversation_id, channel


def _jvforge_configured() -> bool:
    from jvagent.env import get_jvagent_jvforge_base_url

    return bool((get_jvagent_jvforge_base_url() or "").strip())


async def _ensure_access_group(ctx: Any, user_id: str, session_id: str = "") -> None:
    """Idempotently register the user's access-control group.

    Adds the user_id (and session_id, if different) to the
    ``private_<user_id>`` group under the PageIndexAction scope, so the
    user can later search their own docs via list_documents with
    access_control enabled.
    """
    access_control = await _get_access_control_action(ctx)
    if access_control is None:
        return
    group = _group_key(user_id)
    try:
        await access_control.add_user_to_group(
            group, user_id, action_label="PageIndexAction"
        )
    except Exception:
        pass
    if session_id and session_id != user_id:
        try:
            await access_control.add_user_to_group(
                group, session_id, action_label="PageIndexAction"
            )
        except Exception:
            pass


async def _migrate_vault_session_key(
    conversation: Any, session_id: str, user_id: str
) -> None:
    """Migrate vault entries from session-keyed to user-keyed storage.

    Older versions stored entries under ``private_<session_id>``. This
    migrates them to ``private_<user_id>`` so they remain accessible.
    """
    if not conversation or not session_id or not user_id:
        return
    if session_id == user_id:
        return
    old_key = f"private_{session_id}"
    new_key = _group_key(user_id)
    if old_key == new_key:
        return

    vault = _vault_dict(conversation)
    old_entries = vault.get(old_key)
    if not isinstance(old_entries, list) or not old_entries:
        return

    new_entries = vault.get(new_key)
    if not isinstance(new_entries, list):
        new_entries = []

    existing_names = {
        str(e.get("doc_name") or "") for e in new_entries if isinstance(e, dict)
    }
    migrated = []
    for e in old_entries:
        if not isinstance(e, dict):
            continue
        name = str(e.get("doc_name") or "")
        if name and name not in existing_names:
            migrated.append(e)

    if migrated:
        new_entries = list(new_entries) + migrated
        vault = dict(vault)
        vault[new_key] = new_entries
        del vault[old_key]
        try:
            await conversation.update_context({_VAULT_CTX_KEY: vault})
        except Exception:
            pass


def _expired_entries(entries: List[Dict[str, Any]], now: int) -> List[Dict[str, Any]]:
    """Return entries whose expires_at <= now."""
    return [e for e in entries if int(e.get("expires_at", 0) or 0) <= now]


def _unnotified_expired(
    entries: List[Dict[str, Any]], now: int
) -> List[Dict[str, Any]]:
    """Expired entries not yet surfaced to the user (notified != True)."""
    return [
        e
        for e in entries
        if int(e.get("expires_at", 0) or 0) <= now and not e.get("notified")
    ]


def _format_doc_line(entry: Dict[str, Any], now: int) -> str:
    """One-line human description of a vault entry for the user."""
    name = str(entry.get("filename") or entry.get("doc_name") or "document")
    saved = int(entry.get("ingested_at", 0) or 0)
    expires = int(entry.get("expires_at", 0) or 0)
    saved_age = (now - saved) // 86400 if saved else 0
    remaining = _humanize_remaining(expires, now)
    return f"- {name} (saved {saved_age}d ago, expires {remaining})"


async def _maybe_prompt_expired(
    ctx: Any, conversation: Any, group_key: str
) -> List[Dict[str, Any]]:
    """Surface newly-expired docs (auto-on-activation).

    Marks surfaced entries ``notified=True`` and persists. Returns the list
    of newly-expired entries (for the caller to include in the tool response).
    The model is instructed (via system_message) to ask yes/no per doc and
    call delete_document on the user's yes.
    """
    entries = _read_vault(conversation, group_key)
    if not entries:
        return []
    now = _now_ts()
    expired = _unnotified_expired(entries, now)
    if not expired:
        return []

    # Mark notified so we don't re-nag the same docs next activation.
    for e in entries:
        if int(e.get("expires_at", 0) or 0) <= now:
            e["notified"] = True
    await _write_vault(conversation, group_key, entries)

    lines = "\n".join(_format_doc_line(e, now) for e in expired)
    ctx.say(
        "Tell the user some of their saved files have expired and ask if they'd "
        "like them removed. Show the expired files:\n"
        + lines
        + "\n\nAsk yes/no per expired file (or 'yes to all' / 'no to all'). "
        "On yes, call artifact_handler__delete_document with that doc's doc_name. "
        "Do not delete without an explicit yes. Do not mention a retention duration."
    )
    return expired


async def _maybe_refresh_pending_jobs(
    ctx: Any,
    conversation: Any,
    *,
    say_ready: bool = True,
    session_id: str = "",
    user_id: str = "",
) -> Dict[str, Any]:
    """Check pending async ingest jobs via PageIndex list_documents.

    Marks jobs ``ready`` when PageIndex lists the ``doc_name`` with
    access-control filtering (so only docs accessible to this user/session
    count). Surfaces a short ready message when a job transitions
    (web/default backstop; WhatsApp usually already got the proactive ping).
    """
    pending = _read_pending_jobs(conversation)
    if not pending:
        return {"refreshed": 0, "became_ready": [], "still_queued": [], "failed": []}

    try:
        from jvagent.action.pageindex.adapter import strip_redundant_md_suffix
    except Exception:

        def strip_redundant_md_suffix(name: str) -> str:  # type: ignore[misc]
            return name

    page_index = await _get_page_index_action(ctx)
    available_docs: set = set()
    if page_index is not None:
        try:
            docs = await page_index.list_documents(
                access_control=True, session_id=session_id, user_id=user_id
            )
            available_docs = {
                str(d.get("doc_name") or "") for d in docs if isinstance(d, dict)
            }
        except Exception:
            pass

    def _resolve_available_name(doc_name: str) -> str:
        """Return the PageIndex name if doc_name or its md-stripped form is listed."""
        name = (doc_name or "").strip()
        if not name:
            return ""
        if name in available_docs:
            return name
        stripped = strip_redundant_md_suffix(name)
        if stripped and stripped in available_docs:
            return stripped
        return ""

    changed = False
    became_ready: List[str] = []
    still_queued: List[str] = []
    failed: List[str] = []

    for job_id, entry in list(pending.items()):
        status = str(entry.get("status") or "queued").lower()
        doc_name = str(entry.get("doc_name") or "")
        if status in ("ready", "ingested"):
            # Heal stale .md suffixes so search uses the PageIndex name.
            resolved = _resolve_available_name(doc_name)
            if resolved and resolved != doc_name:
                entry = dict(entry)
                entry["doc_name"] = resolved
                pending[job_id] = entry
                changed = True
            continue
        if status in _FAILED_JOB_STATUSES:
            if doc_name:
                failed.append(doc_name)
            continue

        resolved = _resolve_available_name(doc_name)
        if resolved:
            prev = status
            entry = dict(entry)
            entry["status"] = "ready"
            entry["ready_at"] = _now_ts()
            if resolved != doc_name:
                entry["doc_name"] = resolved
            pending[job_id] = entry
            changed = True
            if prev not in ("ready", "ingested") and resolved:
                became_ready.append(resolved)
        elif doc_name:
            still_queued.append(doc_name)

    if changed:
        await _write_pending_jobs(conversation, pending)

    if say_ready and became_ready:
        if len(became_ready) == 1:
            phrase = _friendly_file_phrase(became_ready[0])
            ctx.add_directive(
                f"Tell the user {phrase} is ready and they can ask questions about it."
            )
        else:
            ctx.add_directive(
                "Tell the user their files are ready and they can ask questions about them."
            )

    return {
        "refreshed": len(pending),
        "became_ready": became_ready,
        "still_queued": still_queued,
        "failed": failed,
    }


# ─── Tool 0: check_pending_attachments ────────────────────────────────


async def check_pending_attachments(ctx) -> Dict[str, Any]:
    """Check for pending media attachments or ingest jobs before starting ingest.

    Call only when the user mentions ingest/save/upload and you might ask for a
    URL — media attachments are auto-detected by ArtifactHandlerInteractAction
    before the LLM sees the message. Checks (1) visitor.data for media this
    turn and (2) conversation.context for pending ingest jobs. When either is
    true, acknowledge processing rather than ask for a URL.

    Do **not** use this tool to answer ready/finished/done questions — use
    ``check_ingest_status`` for that.
    """
    visitor = ctx.visitor

    # --- Check visitor.data for media attachments this turn (same collector as ingest) ---
    attachments: List[Dict[str, str]] = []
    has_pending_attachments = False
    try:
        from jvagent.action.artifact_handler_interact_action import (
            collect_visitor_media,
        )

        items, _dropped_av = collect_visitor_media(visitor)
        for item in items:
            attachments.append(
                {
                    "filename": str(item.filename or "uploaded_file"),
                    "mime": str(item.mime or ""),
                }
            )
        has_pending_attachments = bool(attachments)
    except Exception:
        pass

    # --- Check conversation context for pending ingest jobs ---
    conversation = await _get_conversation(visitor)
    pending_jobs: List[Dict[str, Any]] = []
    has_pending_jobs = False

    if conversation is not None:
        user_id, _, _ = _resolve_routing(visitor, conversation)
        session_id = _resolve_session_id(visitor)
        await _maybe_refresh_pending_jobs(
            ctx,
            conversation,
            say_ready=False,
            session_id=session_id or "",
            user_id=user_id,
        )
        for jid, entry in _read_pending_jobs(conversation).items():
            job_status = str(entry.get("status") or "").strip().lower()
            if job_status in _PROCESSING_STATUSES or (
                job_status not in _FAILED_JOB_STATUSES
                and job_status not in ("ready", "ingested", "done")
            ):
                has_pending_jobs = True
                job_info: Dict[str, Any] = {
                    "doc_name": str(entry.get("doc_name") or jid),
                    "status": job_status or "unknown",
                }
                pq = entry.get("pending_question")
                if pq:
                    job_info["pending_question"] = str(pq)
                pending_jobs.append(job_info)

    has_any = has_pending_attachments or has_pending_jobs

    if has_any:
        if has_pending_attachments and has_pending_jobs:
            status = "has_both"
        elif has_pending_attachments:
            status = "has_attachments"
        else:
            status = "has_pending_jobs"
        ctx.say(
            "Tell the user a file is already being processed for this chat and "
            "you'll message them when it's ready for query. Do not ask for a URL."
        )
    else:
        status = "none"

    return ctx.tool_response(
        ok=True,
        status=status,
        has_pending_attachments=has_pending_attachments,
        has_pending_jobs=has_pending_jobs,
        attachments=attachments,
        pending_jobs=pending_jobs,
    )


# ─── Tool 1: ingest_document (URL-only; media handled by InteractAction) ──


async def ingest_document(ctx) -> Dict[str, Any]:
    """Ingest a typed URL; optionally save a deferred question.

    This tool handles URL-based ingestion only — the user typed a link and
    wants it saved. Media attachments on the channel are automatically
    detected and ingested by the ``ArtifactHandlerInteractAction`` interact
    action before the LLM sees the message.

    Optional ``question`` is persisted with the job and answered when the
    document is ready (WhatsApp proactive agent turn, or web when the user
    checks ingest status).

    If no URL is provided, returns ``no_url`` and asks for one.
    Async path queues via ArtifactHandlerInteractAction; sync fallback
    assimilates immediately.
    """
    visitor = ctx.visitor
    session_id = _resolve_session_id(visitor)
    if not session_id:
        ctx.say(
            "Tell the user you're having trouble connecting to their chat session "
            "and ask them to reconnect and try again. Do not ingest anything; the "
            "session is unidentifiable."
        )
        return ctx.tool_response(ok=False, status="no_session")

    user_id = str(getattr(visitor, "user_id", "") or "").strip() if visitor else ""

    args = getattr(ctx, "args", None) or {}
    url_arg = str(args.get("url") or "").strip()
    doc_name_arg = str(args.get("doc_name") or "").strip() or None
    question = str(args.get("question") or "").strip() or None

    if not url_arg:
        ctx.say(
            "Tell the user you don't see a link yet and ask them to paste a URL "
            "when ready. Do not invent a URL. Media attachments are handled "
            "automatically."
        )
        return ctx.tool_response(ok=False, status="no_url")

    group_key = _group_key(user_id or session_id)
    conversation = await _get_conversation(visitor)

    await _migrate_vault_session_key(conversation, session_id or "", user_id)

    # Refresh any previously queued jobs before handling this ingest.
    await _maybe_refresh_pending_jobs(
        ctx, conversation, say_ready=True, session_id=session_id or "", user_id=user_id
    )

    page_index = await _get_page_index_action(ctx)
    if page_index is None:
        ctx.say(
            "Tell the user you can't save that right now and ask them to try "
            "again in a moment. Do not retry repeatedly."
        )
        return ctx.tool_response(ok=False, status="no_pageindex")

    use_async = _jvforge_configured()
    dv_action = None
    if use_async:
        dv_action = await _get_artifact_handler_action(ctx)
        if dv_action is None:
            ctx.say(
                "Tell the user you can't reach the artifact handler right now and "
                "ask them to try again in a moment. Do not retry repeatedly."
            )
            return ctx.tool_response(ok=False, status="no_artifact_handler_action")

    # Register the user's access-control group (idempotent).
    await _ensure_access_group(ctx, user_id or session_id, session_id=session_id or "")

    entries = _read_vault(conversation, group_key)
    pending = _read_pending_jobs(conversation)
    user_id, conversation_id, channel = _resolve_routing(visitor, conversation)
    notification_url = None
    if use_async and dv_action is not None:
        try:
            notification_url = await dv_action.get_notify_webhook_url()
        except Exception:
            notification_url = None

    now = _now_ts()
    expires_at = now + _DEFAULT_RETENTION_SECONDS

    # Single URL candidate — always namespace doc_name under the user's vault prefix.
    filename = _filename_from_url(url_arg)
    name_hint = doc_name_arg or filename
    doc_name, display_filename = _vault_doc_ids(user_id or session_id, name_hint)

    pending_q = question
    ingested: List[str] = []
    queued: List[str] = []
    failed: List[str] = []
    saved_question_for: Optional[str] = None

    if use_async and dv_action is not None:
        try:
            result = await dv_action.submit_ingest(
                doc=url_arg,
                doc_name=doc_name,
                user_id=user_id,
                conversation_id=conversation_id,
                session_id=session_id,
                channel=channel,
                is_image=_url_is_image(url_arg),
                notification_url=notification_url,
                metadata={"access": group_key},
                pending_question=pending_q,
                filename=display_filename,
            )
        except Exception:
            failed.append(filename or url_arg)
            ctx.say(
                "Tell the user you weren't able to save that and ask them to try "
                "sending it again. Do not fabricate a reason."
            )
            return ctx.tool_response(ok=False, status="ingest_failed", failed=failed)

        job_id = str((result or {}).get("job_id") or "")
        entry: Dict[str, Any] = {
            "doc_name": doc_name,
            "ingested_at": now,
            "expires_at": expires_at,
            "filename": display_filename,
            "source": "url",
            "notified": False,
            "job_id": job_id or None,
            "status": "queued",
        }
        if pending_q:
            entry["pending_question"] = pending_q
            saved_question_for = doc_name
        entries.append(entry)
        if job_id:
            pending_entry: Dict[str, Any] = {
                "doc_name": doc_name,
                "status": "queued",
                "submitted_at": now,
            }
            if pending_q:
                pending_entry["pending_question"] = pending_q
            pending[job_id] = pending_entry
        queued.append(doc_name)
    else:
        try:
            await page_index.assimilate(
                doc=url_arg,
                doc_name=doc_name,
                metadata={"access": group_key},
                convert_to_markdown=True,
                generate_description=True,
            )
        except Exception:
            failed.append(filename or url_arg)
            ctx.say(
                "Tell the user you weren't able to save that and ask them to try "
                "sending it again. Do not fabricate a reason."
            )
            return ctx.tool_response(ok=False, status="ingest_failed", failed=failed)

        entry = {
            "doc_name": doc_name,
            "ingested_at": now,
            "expires_at": expires_at,
            "filename": display_filename,
            "source": "url",
            "notified": False,
            "status": "ingested",
        }
        if pending_q:
            entry["pending_question"] = pending_q
            saved_question_for = doc_name
        entries.append(entry)
        ingested.append(doc_name)

    if (queued or ingested) and conversation is not None:
        ctx_map = getattr(conversation, "context", None)
        if not isinstance(ctx_map, dict):
            try:
                await conversation.update_context({})
            except Exception:
                pass
            ctx_map = getattr(conversation, "context", None)
        vault = {}
        if isinstance(ctx_map, dict):
            existing = ctx_map.get(_VAULT_CTX_KEY)
            if isinstance(existing, dict):
                vault = dict(existing)
        vault[group_key] = entries
        vault[_PENDING_JOBS_KEY] = pending
        saved_names = queued + ingested
        if saved_names:
            vault[_ACTIVE_DOC_KEY] = saved_names[-1]
        try:
            await conversation.update_context({_VAULT_CTX_KEY: vault})
        except Exception:
            pass

    # ── Reply ──
    if queued and not failed:
        if question and saved_question_for:
            phrase = (
                _friendly_file_phrase(queued[0]) if len(queued) == 1 else "your files"
            )
            ctx.add_directive(
                f"Tell the user {phrase} is being processed and you'll answer "
                f"their question as soon as it's ready. Do not claim it is "
                f"searchable yet."
            )
        else:
            phrase = (
                _friendly_file_phrase(queued[0]) if len(queued) == 1 else "your files"
            )
            ctx.add_directive(
                f"Tell the user {phrase} is being processed and you'll let them "
                f"know when it's ready. Do not claim it is searchable yet."
            )
        status = "queued"
    elif ingested and not failed:
        if question and saved_question_for:
            phrase = (
                _friendly_file_phrase(ingested[0])
                if len(ingested) == 1
                else "your files"
            )
            ctx.add_directive(
                f"Tell the user {phrase} is saved and ready. "
                "Do not mention a retention duration."
            )
        else:
            phrase = (
                _friendly_file_phrase(ingested[0])
                if len(ingested) == 1
                else "your files"
            )
            ctx.add_directive(
                f"Tell the user {phrase} is saved and ready, and they can ask "
                "questions about it. Do not mention a retention duration."
            )
        status = "ingested"
    else:
        ctx.add_directive(
            "Something went wrong and the file could not be saved. "
            "Ask the user to try sending it again."
        )
        return ctx.tool_response(ok=False, status="ingest_failed", failed=failed)

    # Auto-on-activation: surface any newly-expired docs.
    expired = await _maybe_prompt_expired(ctx, conversation, group_key)

    system_parts = [
        "When acknowledging document processing or status updates, do not mention "
        "or allude to prior questions, topics, or artifacts unless the user "
        "explicitly refers to them in the current turn. If status is queued, do "
        "NOT claim the document is searchable yet. If status is ingested/ready, "
        "content questions belong to faq / pageindex__search — always provide "
        "query (the user's question) and doc_name. Never say you cannot access "
        "document content. After answering about a document, treat that doc_name "
        "as the Active document for follow-ups; for later vague questions, prefer "
        "a clearer doc_description match over Active document when they conflict."
    ]
    if status == "ingested" and question and saved_question_for:
        system_parts.append(
            f" Deferred question for '{saved_question_for}': {question!r}. "
            "Answer it now via faq / pageindex__search with query set to the "
            "question and doc_name set to that document. Give the answer directly; "
            "do not also announce that the document is ready — a separate "
            "directive already handles that. After answering, that document is "
            "the Active document for follow-ups."
        )

    resp_kwargs: Dict[str, Any] = {
        "ok": True,
        "status": status,
        "doc_names": queued + ingested,
        "queued": queued,
        "ingested": ingested,
        "failed": failed,
        "expired_count": len(expired),
        "system_message": "".join(system_parts),
    }
    if question and saved_question_for:
        resp_kwargs["pending_question"] = question
        resp_kwargs["pending_question_doc_name"] = saved_question_for
    return ctx.tool_response(**resp_kwargs)


# ─── Tool 2: list_my_documents ────────────────────────────────────────


async def list_my_documents(ctx) -> Dict[str, Any]:
    """List the documents saved in this user's vault, with save age and expiry.

    Also refreshes pending ingest jobs and surfaces newly-expired documents.
    """
    visitor = ctx.visitor
    session_id = _resolve_session_id(visitor)
    if not session_id:
        ctx.say(
            "Tell the user you're having trouble connecting to their chat session "
            "and ask them to reconnect and try again."
        )
        return ctx.tool_response(ok=False, status="no_session")

    user_id = str(getattr(visitor, "user_id", "") or "").strip() if visitor else ""
    group_key = _group_key(user_id or session_id)
    conversation = await _get_conversation(visitor)

    await _migrate_vault_session_key(conversation, session_id or "", user_id)
    await _maybe_refresh_pending_jobs(
        ctx, conversation, say_ready=True, session_id=session_id or "", user_id=user_id
    )

    entries = _read_vault(conversation, group_key)

    now = _now_ts()
    if not entries:
        ctx.say(
            "Tell the user they haven't saved any files yet and offer to save "
            "a document, image, or link when they're ready. Do not mention a "
            "retention duration."
        )
        return ctx.tool_response(ok=True, status="empty", docs=[])

    # Drop already-expired from the live list (they show in the expiry prompt).
    live = [e for e in entries if int(e.get("expires_at", 0) or 0) > now]
    if not live:
        ctx.say(
            "Tell the user all their saved files have expired and ask if they'd "
            "like to clean them up."
        )
        expired = await _maybe_prompt_expired(ctx, conversation, group_key)
        return ctx.tool_response(
            ok=True, status="all_expired", docs=[], expired_count=len(expired)
        )

    lines = "\n".join(_format_doc_line(e, now) for e in live)
    ctx.say(
        "Tell the user here are their saved files:\n"
        + lines
        + "\n\nKeep the list exact. Do not invent extra docs."
    )

    expired = await _maybe_prompt_expired(ctx, conversation, group_key)

    desc_lookup = await _doc_description_lookup(
        ctx, user_id or session_id, session_id or ""
    )

    return ctx.tool_response(
        ok=True,
        status="listed",
        docs=[
            {
                "doc_name": e.get("doc_name"),
                "doc_description": desc_lookup.get(str(e.get("doc_name") or ""), ""),
                "filename": e.get("filename"),
                "ingested_at": e.get("ingested_at"),
                "expires_at": e.get("expires_at"),
                "status": e.get("status"),
            }
            for e in live
        ],
        expired_count=len(expired),
    )


# ─── Tool 3: delete_document ──────────────────────────────────────────


async def delete_document(ctx) -> Dict[str, Any]:
    """Delete one document from the user's vault by doc_name."""
    visitor = ctx.visitor
    session_id = _resolve_session_id(visitor)
    if not session_id:
        ctx.say(
            "Tell the user you're having trouble connecting to their chat session "
            "and ask them to reconnect and try again."
        )
        return ctx.tool_response(ok=False, status="no_session")

    args = getattr(ctx, "args", None) or {}
    doc_name = str(args.get("doc_name") or "").strip()
    if not doc_name:
        ctx.say(
            "Ask the user which file they'd like removed by name from their "
            "saved list."
        )
        return ctx.tool_response(ok=False, status="missing_doc_name")

    user_id = str(getattr(visitor, "user_id", "") or "").strip() if visitor else ""
    group_key = _group_key(user_id or session_id)
    conversation = await _get_conversation(visitor)

    await _migrate_vault_session_key(conversation, session_id or "", user_id)
    entries = _read_vault(conversation, group_key)

    target = None
    for e in entries:
        if str(e.get("doc_name") or "") == doc_name:
            target = e
            break
    if target is None:
        ctx.say(
            "Tell the user you don't see a file by that name in their vault — "
            "it may already have been removed. Do not delete anything else. "
            "Offer list_my_documents if helpful."
        )
        return ctx.tool_response(ok=False, status="not_found")

    if not _doc_name_owned_by_user(user_id or session_id, doc_name):
        ctx.say(
            "Tell the user you don't see a file by that name in their vault — "
            "it may already have been removed. Do not delete anything else. "
            "Offer list_my_documents if helpful."
        )
        return ctx.tool_response(ok=False, status="not_found")

    # 1. PageIndex deletion.
    page_index = await _get_page_index_action(ctx)
    deleted_pi = False
    if page_index is not None:
        try:
            await page_index.delete_document(doc_name=doc_name)
            deleted_pi = True
        except Exception:
            pass

    # 2. Drop from vault index + any pending job for this doc.
    new_entries = [e for e in entries if str(e.get("doc_name") or "") != doc_name]
    await _write_vault(conversation, group_key, new_entries)

    pending = _read_pending_jobs(conversation)
    if pending:
        drop_ids = [
            jid
            for jid, pe in pending.items()
            if str(pe.get("doc_name") or "") == doc_name
        ]
        for jid in drop_ids:
            del pending[jid]
        if drop_ids:
            await _write_pending_jobs(conversation, pending)

    await _clear_active_doc_name_if(conversation, doc_name)

    ctx.say(
        "Tell the user the file has been removed. Keep it brief. Do not list "
        "remaining docs unless asked."
    )
    return ctx.tool_response(
        ok=True,
        status="deleted" if deleted_pi else "index_only",
        doc_name=doc_name,
    )


# ─── Tool 4: review_expired ───────────────────────────────────────────


async def review_expired(ctx) -> Dict[str, Any]:
    """Surface documents past the retention window."""
    visitor = ctx.visitor
    session_id = _resolve_session_id(visitor)
    if not session_id:
        ctx.say(
            "Tell the user you're having trouble connecting to their chat session "
            "and ask them to reconnect and try again."
        )
        return ctx.tool_response(ok=False, status="no_session")

    user_id = str(getattr(visitor, "user_id", "") or "").strip() if visitor else ""
    group_key = _group_key(user_id or session_id)
    conversation = await _get_conversation(visitor)

    await _migrate_vault_session_key(conversation, session_id or "", user_id)
    await _maybe_refresh_pending_jobs(
        ctx, conversation, say_ready=True, session_id=session_id or "", user_id=user_id
    )

    entries = _read_vault(conversation, group_key)

    now = _now_ts()
    expired = _expired_entries(entries, now)
    if not expired:
        ctx.say(
            "Tell the user all their saved files are still current. Do not "
            "mention a retention duration."
        )
        return ctx.tool_response(ok=True, status="none_expired", expired=[])

    # Mark notified so auto-prompt doesn't re-surface them next activation.
    for e in entries:
        if int(e.get("expires_at", 0) or 0) <= now:
            e["notified"] = True
    await _write_vault(conversation, group_key, entries)

    lines = "\n".join(_format_doc_line(e, now) for e in expired)
    ctx.say(
        "Tell the user some of their saved files have expired and ask if they'd "
        "like them removed. Show the expired files:\n"
        + lines
        + "\n\nAsk yes/no per expired file (or 'yes to all' / 'no to all'). "
        "On yes, call artifact_handler__delete_document with that doc's doc_name. "
        "Do not delete without an explicit yes. Do not mention a retention duration."
    )

    return ctx.tool_response(
        ok=True,
        status="expired_listed",
        expired=[
            {
                "doc_name": e.get("doc_name"),
                "filename": e.get("filename"),
                "ingested_at": e.get("ingested_at"),
                "expires_at": e.get("expires_at"),
            }
            for e in expired
        ],
        system_message=(
            "Ask the user yes/no per expired document; on yes call "
            "artifact_handler__delete_document with that doc_name. Do not delete without "
            "an explicit yes."
        ),
    )


# ─── Tool 5: check_ingest_status ───────────────────────────────────────


def _collect_ready_pending_questions(
    pending: Dict[str, Dict[str, Any]],
) -> List[Dict[str, str]]:
    """Return ready jobs that still have a deferred question to answer."""
    out: List[Dict[str, str]] = []
    for jid, entry in pending.items():
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "").lower()
        if status not in ("ready", "ingested"):
            continue
        question = str(entry.get("pending_question") or "").strip()
        if not question:
            continue
        doc_name = str(entry.get("doc_name") or "").strip()
        if not doc_name:
            continue
        out.append(
            {
                # job_id kept for internal clear only — not returned to the LLM
                "job_id": str(jid),
                "doc_name": doc_name,
                "question": question,
            }
        )
    return out


async def _clear_pending_questions(
    conversation: Any,
    group_key: str,
    pending: Dict[str, Dict[str, Any]],
    ready_questions: List[Dict[str, str]],
) -> Dict[str, Dict[str, Any]]:
    """Clear deferred questions from pending jobs and vault after surfacing."""
    if not ready_questions:
        return pending
    clear_ids = {q["job_id"] for q in ready_questions}
    clear_docs = {q["doc_name"] for q in ready_questions if q.get("doc_name")}
    updated = dict(pending)
    for jid in clear_ids:
        entry = updated.get(jid)
        if isinstance(entry, dict) and entry.get("pending_question"):
            entry = dict(entry)
            entry.pop("pending_question", None)
            updated[jid] = entry

    entries = _read_vault(conversation, group_key)
    vault_changed = False
    new_entries: List[Dict[str, Any]] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        e2 = dict(e)
        doc = str(e2.get("doc_name") or "")
        job_id = str(e2.get("job_id") or "")
        if e2.get("pending_question") and (doc in clear_docs or job_id in clear_ids):
            e2.pop("pending_question", None)
            vault_changed = True
        new_entries.append(e2)

    if conversation is not None:
        ctx_map = getattr(conversation, "context", None)
        if not isinstance(ctx_map, dict):
            try:
                await conversation.update_context({})
            except Exception:
                pass
            ctx_map = getattr(conversation, "context", None)
        vault: Dict[str, Any] = {}
        if isinstance(ctx_map, dict):
            existing = ctx_map.get(_VAULT_CTX_KEY)
            if isinstance(existing, dict):
                vault = dict(existing)
        vault[_PENDING_JOBS_KEY] = updated
        if vault_changed:
            vault[group_key] = new_entries
        try:
            await conversation.update_context({_VAULT_CTX_KEY: vault})
        except Exception:
            pass
    return updated


async def check_ingest_status(ctx) -> Dict[str, Any]:
    """Check pending async ingest jobs and report ready / still-processing.

    Call with **no arguments** — do not pass ``doc_name`` or ``url``. Reports
    all pending jobs for this chat session.

    Use when the user asks if a document is ready, finished, done, processed,
    or if processing is complete — including right after upload while a job
    may still be queued. This is the status check during or after processing.
    Do not use ``check_pending_attachments`` for these questions.

    Uses PageIndex list_documents with access control to verify a document
    is available to the user, instead of polling jvforge for job status.
    Also auto-runs (via helpers) on ingest_document / list_my_documents /
    review_expired activation.

    When a ready job has a saved ``pending_question``, surface it so the
    agent replies in one message: ready notice → remind the question →
    answer via faq / pageindex__search using that job's ``doc_name`` (never
    invent a Google Docs/URL id or other non-vault id as ``doc_name``).
    """
    visitor = ctx.visitor
    session_id = _resolve_session_id(visitor)
    if not session_id:
        ctx.say(
            "Tell the user you're having trouble connecting to their chat session "
            "and ask them to reconnect and try again."
        )
        return ctx.tool_response(ok=False, status="no_session")

    user_id = str(getattr(visitor, "user_id", "") or "").strip() if visitor else ""
    group_key = _group_key(user_id or session_id)
    conversation = await _get_conversation(visitor)
    pending_before = _read_pending_jobs(conversation)
    if not pending_before:
        ctx.say(
            "Tell the user nothing is processing right now and offer to save "
            "a document, image, or link."
        )
        return ctx.tool_response(ok=True, status="none_pending", jobs=[])

    # Refresh without double-saying ready; we craft the summary below.
    result = await _maybe_refresh_pending_jobs(
        ctx, conversation, say_ready=False, session_id=session_id or "", user_id=user_id
    )
    pending = _read_pending_jobs(conversation)

    ready_names = [
        str(e.get("doc_name") or "").strip()
        for jid, e in pending.items()
        if str(e.get("status") or "").lower() in ("ready", "ingested")
        and str(e.get("doc_name") or "").strip()
    ]
    # Newly transitioned this call take priority in the user-facing message.
    became = result.get("became_ready") or []
    still = result.get("still_queued") or []
    failed = result.get("failed") or []

    ready_questions = _collect_ready_pending_questions(pending)

    if became and not still and not failed:
        if ready_questions:
            questions_text = "; ".join(q["question"] for q in ready_questions)
            if len(became) == 1:
                phrase = _friendly_file_phrase(became[0])
                ctx.add_directive(
                    f"In one reply: (1) tell the user {phrase} is ready, "
                    f"(2) remind them they asked: {questions_text}, "
                    f"(3) answer that question from pageindex__search results."
                )
            else:
                phrases = [_friendly_file_phrase(n) for n in became]
                ctx.add_directive(
                    f"In one reply: (1) tell the user their files are ready "
                    f"({', '.join(phrases)}), "
                    f"(2) remind them they asked about: {questions_text}, "
                    f"(3) answer from pageindex__search results."
                )
            ctx.say(
                "For each pending question, call pageindex__search with query "
                "set to the pending_question value and doc_name set to that "
                "job's doc_name. Then write one reply in this exact order: "
                "(1) say the document/image is ready, (2) remind them of their "
                "pending question by quoting or paraphrasing it, (3) give the "
                "answer from the search results."
            )
        elif len(became) == 1:
            phrase = _friendly_file_phrase(became[0])
            ctx.add_directive(
                f"Tell the user {phrase} is ready and they can ask questions about it."
            )
        else:
            ctx.add_directive(
                "Tell the user their files are ready and they can ask questions about them."
            )
        status = "ready"
    elif still and not became and not failed:
        if len(still) == 1:
            phrase = _friendly_file_phrase(still[0])
            ctx.add_directive(
                f"Tell the user {phrase} is still processing and you'll "
                "let them know when it's ready."
            )
        else:
            ctx.add_directive(
                "Tell the user their files are still processing and you'll "
                "let them know when they're ready."
            )
        status = "queued"
    elif became and still:
        if ready_questions:
            questions_text = "; ".join(q["question"] for q in ready_questions)
            ready_phrases = [_friendly_file_phrase(n) for n in became]
            ctx.add_directive(
                f"In one reply: (1) tell the user some of their files are ready "
                f"({', '.join(ready_phrases)}) while others are still processing, "
                f"(2) remind them they asked about: {questions_text}, "
                f"(3) answer the ready docs from pageindex__search results."
            )
            ctx.say(
                "For each pending question on a ready doc, call pageindex__search "
                "with query set to the pending_question value and doc_name set to "
                "that job's doc_name. Then write one reply in this exact order: "
                "(1) say which document(s) are ready, (2) remind them of their "
                "pending question by quoting or paraphrasing it, (3) give the "
                "answer from the search results."
            )
        else:
            ready_phrases = [_friendly_file_phrase(n) for n in became]
            ctx.add_directive(
                f"Tell the user some of their files are ready "
                f"({', '.join(ready_phrases)}) while others are still processing."
            )
        status = "partial"
    elif failed and not still and not became:
        ctx.add_directive(
            "Something went wrong while processing the file. "
            "Ask the user to try sending it again."
        )
        status = "failed"
    else:
        # Mixed failed + other, or only ready-from-before.
        if ready_questions and still:
            questions_text = "; ".join(q["question"] for q in ready_questions)
            ctx.add_directive(
                f"In one reply: (1) tell the user some of their files are ready "
                f"while others are still processing, "
                f"(2) remind them they asked about: {questions_text}, "
                f"(3) answer the ready docs from pageindex__search results."
            )
            ctx.say(
                "For each pending question on a ready doc, call pageindex__search "
                "with query set to the pending_question value and doc_name set to "
                "that job's doc_name. Then write one reply in this exact order: "
                "(1) say which document(s) are ready, (2) remind them of their "
                "pending question by quoting or paraphrasing it, (3) give the "
                "answer from the search results."
            )
            status = "partial"
        elif ready_names and not still:
            if ready_questions:
                questions_text = "; ".join(q["question"] for q in ready_questions)
                ctx.add_directive(
                    f"In one reply: (1) tell the user their saved files are ready, "
                    f"(2) remind them they asked about: {questions_text}, "
                    f"(3) answer from pageindex__search results."
                )
                ctx.say(
                    "For each pending question, call pageindex__search with query "
                    "set to the pending_question value and doc_name set to that "
                    "job's doc_name. Then write one reply in this exact order: "
                    "(1) say the document/image is ready, (2) remind them of their "
                    "pending question by quoting or paraphrasing it, (3) give the "
                    "answer from the search results."
                )
            else:
                ctx.add_directive(
                    "Tell the user their saved files are ready and they can ask "
                    "questions about them."
                )
            status = "ready"
        elif still:
            ctx.add_directive(
                "Tell the user their file is still processing and you'll "
                "let them know when it's ready."
            )
            status = "queued"
        else:
            ctx.add_directive("Nothing is processing right now.")
            status = "none_pending"

    # Build the jobs list BEFORE clearing so pending_question is still present.
    # Omit job_id from the LLM payload to avoid confusion with vault doc_name.
    desc_lookup = await _doc_description_lookup(
        ctx, user_id or session_id, session_id or ""
    )
    jobs_list = []
    for _jid, e in pending.items():
        name = str(e.get("doc_name") or "").strip()
        if not name:
            continue
        jobs_list.append(
            {
                "doc_name": name,
                "doc_description": desc_lookup.get(name, ""),
                "status": e.get("status"),
                "submitted_at": e.get("submitted_at"),
                "pending_question": e.get("pending_question"),
            }
        )

    # Only clear deferred questions after we surfaced them for answering.
    if ready_questions and status in ("ready", "partial"):
        pending = await _clear_pending_questions(
            conversation, group_key, pending, ready_questions
        )
    elif ready_questions:
        # Still processing only — keep questions for a later status check.
        ready_questions = []

    active_candidate = ""
    if became:
        active_candidate = str(became[-1] or "").strip()
    elif ready_questions:
        active_candidate = str(
            (ready_questions[-1] or {}).get("doc_name") or ""
        ).strip()
    elif ready_names and status == "ready":
        active_candidate = str(ready_names[-1] or "").strip()
    if active_candidate:
        await _set_active_doc_name(conversation, active_candidate)

    system_message = (
        "Call check_ingest_status with no arguments (do not pass doc_name or url). "
        "When acknowledging document processing or status updates, do not mention "
        "or allude to prior questions, topics, or artifacts unless the user "
        "explicitly refers to them in the current turn, or a job has a "
        "pending_question field. If status is queued, do NOT claim the document "
        "is searchable yet. If status is ready, content questions belong to faq / "
        "pageindex__search — use query (the user's or pending question) and "
        "doc_name from jobs[].doc_name (or Active document / doc_description "
        "match). Never use a Google Docs/Drive URL path id, raw URL, or any "
        "non-vault id as doc_name. Do NOT call ingest_document again for a URL "
        "that is already queued or ready. Never say you cannot access document "
        "content. After answering about a document, treat that doc_name as the "
        "Active document for follow-ups; for later vague questions, prefer a "
        "clearer doc_description match over Active document when they conflict."
    )
    if ready_questions:
        system_message += (
            " One or more jobs have a pending_question field. Call "
            "pageindex__search with query set to the pending_question value and "
            "doc_name set to that job's doc_name from the jobs list. Then write "
            "ONE reply in this exact order: (1) say the document/image is ready, "
            "(2) remind them of their pending question by quoting or paraphrasing "
            "it, (3) give the answer from the search results."
        )
        if active_candidate:
            system_message += (
                f" After answering, Active document is {active_candidate!r}."
            )
    elif became:
        doc_names = ", ".join(became)
        system_message += (
            f" Ready document(s): {doc_names}."
            + (
                f" Active document is {active_candidate!r} for follow-ups."
                if active_candidate
                else ""
            )
            + " If the conversation has a deferred content question about one of "
            "these docs, call pageindex__search with that jobs[].doc_name — do "
            "not re-ingest."
        )

    # When everything is ready (or nothing pending), release task-lock so faq
    # can answer content questions on the next turn.

    return ctx.tool_response(
        ok=True,
        status=status,
        became_ready=became,
        still_queued=still,
        failed=failed,
        jobs=jobs_list,
        system_message=system_message,
    )


# ─── Handlers ─────────────────────────────────────────────────────────
