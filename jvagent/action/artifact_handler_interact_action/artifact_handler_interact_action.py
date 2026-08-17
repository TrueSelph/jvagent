"""ArtifactHandlerInteractAction — interact-action that automatically detects
media attachments and ingests them, plus hosts the jvforge job reverse-index,
completion notification sub-callback, and LLM tool dispatch for the
artifact_handler skill.

Responsibilities:

1. **InteractAction.execute**: on every turn, inspects ``visitor.data`` for
   uploaded files (documents, images — audio/video are rejected). When media
   is found the utterance is saved as ``pending_question`` and the files are
   ingested via jvforge async (or PageIndex sync fallback). On successful
   ingest, a Tell-the-user directive (WHAT) is queued, ``respond()`` emits the
   ack via ReplyAction immediately, and ``curate_walk_path([])`` drops the rest
   of the walk (including Orchestrator). If no media is attached the action
   unrecords and returns silently.

2. **Reverse-index**: persist a mapping ``job_id -> {user_id, conversation_id,
   session_id, channel, doc_name, agent_id, submitted_at, pending_question}``
   so the notification sub-callback can route a proactive message back to the
   original user. The index is a persisted jvspatial attribute on this action
   node (survives restart), mirroring the ``extraction_job_index`` pattern on
   a product action's job index.

3. **Async jvforge ingest submission**: ``submit_ingest`` is the thin wrapper
   the skill calls instead of ``PageIndexAction.assimilate``. It calls
   ``assimilate_via_jvforge_async`` with the notification URL pointed at this
   action's ``/notify`` endpoint, then registers the returned ``job_id`` in
   the reverse-index (jvforge assigns the id on submit). Returns the jvforge
   queue response (``{status, job_id, queue_position, doc_name, message}``).

4. **LLM tools**: exposes ``artifact_handler__*`` tools that dispatch to the
   skill's ``custom_tools.py`` functions via a ``VaultToolContext`` adapter.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.utils.uploads import (
    DEFAULT_UPLOAD_KEYS,
    MAX_UPLOAD_ITEM_BYTES,
    UploadItem,
    normalize_upload_entry,
)
from jvagent.tooling.tool_decorator import tool

if False:
    from jvagent.action.interact.interact_walker import InteractWalker

_AGENT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), *([".."] * 4))
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

_PERSISTED_JOB_INDEX_ATTR = "jvforge_job_index"

# WhatsApp MediaManager storage names like ``20260724_134410_c624a9f1.pdf``.
_MACHINE_FILENAME_RE = re.compile(r"^\d{8}_\d{6}_[0-9a-fA-F]{6,}(\.[A-Za-z0-9]+)?$")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_ts() -> int:
    return int(time.time())


_PROCESSING_STATUSES = frozenset({"queued", "processing", "pending", "submitted"})


def _safe_filename_segment(
    filename: Optional[str], *, default: str = "document"
) -> str:
    """Sanitize an original filename for storage / PageIndex doc_name tails."""
    base = (filename or "").strip() or default
    safe = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in base)[:80]
    return safe or default


def _vault_doc_ids(user_id: str, filename: Optional[str]) -> Tuple[str, str]:
    """Return ``(doc_name, display_filename)`` as ``{user_id}_{original}``.

    ``display_filename`` is the sanitized original basename (keeps extension for
    user-facing copy). ``doc_name`` matches PageIndex via
    ``strip_redundant_md_suffix`` (e.g. ``o.User...._zz.md`` → ``o.User...._zz``).
    """
    from jvagent.action.pageindex.adapter import strip_redundant_md_suffix

    display = _safe_filename_segment(filename)
    uid = (user_id or "").strip() or "user"
    doc_name = strip_redundant_md_suffix(f"{uid}_{display}") or f"{uid}_{display}"
    return doc_name, display


def _is_unsupported_av(item: UploadItem) -> bool:
    mime = (item.mime or "").lower()
    return mime.startswith("audio/") or mime.startswith("video/")


def _media_kind_phrase(items: List[UploadItem]) -> str:
    """Return ``document(s)``, ``image(s)``, or ``documents and images``."""
    if not items:
        return "document"
    n_image = 0
    n_doc = 0
    for item in items:
        kind = (item.kind or "").lower()
        mime = (item.mime or "").lower()
        if kind == "image" or mime.startswith("image/"):
            n_image += 1
        else:
            n_doc += 1
    if n_image and n_doc:
        return "documents and images"
    if n_image:
        return "image" if n_image == 1 else "images"
    return "document" if n_doc == 1 else "documents"


def _kind_verb_finished(kind_phrase: str) -> Tuple[str, str]:
    """Return ``(is|are, it is|they are)`` for ``kind_phrase``."""
    if kind_phrase in ("documents", "images", "documents and images"):
        return "are", "they are"
    return "is", "it is"


def _url_basename(url: str) -> str:
    try:
        return os.path.basename(urlparse(url).path) or ""
    except Exception:
        return ""


def _looks_like_storage_hash_name(name: str) -> bool:
    return bool(_MACHINE_FILENAME_RE.match((name or "").strip()))


def _whatsapp_payload_filename(visitor: Any) -> str:
    data = getattr(visitor, "data", None)
    if not isinstance(data, dict):
        return ""
    payload = data.get("whatsapp_payload")
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("filename") or "").strip()


def _apply_filename_fallbacks(
    visitor: Any, items: List[UploadItem]
) -> List[UploadItem]:
    """Prefer Meta original name over MediaManager hash basenames."""
    payload_fn = _whatsapp_payload_filename(visitor)
    out: List[UploadItem] = []
    for item in items:
        name = (item.filename or "").strip()
        url_base = _url_basename(item.url or "")
        need_fallback = _looks_like_storage_hash_name(name) or (
            bool(url_base) and name == url_base
        )
        if (
            need_fallback
            and payload_fn
            and not _looks_like_storage_hash_name(payload_fn)
        ):
            out.append(replace(item, filename=payload_fn))
            continue
        out.append(item)
    return out


# Platform DEFAULT_UPLOAD_KEYS plus messenger_media (Facebook channel).
_VAULT_UPLOAD_KEYS = tuple(dict.fromkeys((*DEFAULT_UPLOAD_KEYS, "messenger_media")))


def _is_image_item(item: UploadItem) -> bool:
    kind = (item.kind or "").lower()
    mime = (item.mime or "").lower()
    return kind == "image" or mime.startswith("image/")


def _dedupe_upload_items(items: List[UploadItem]) -> List[UploadItem]:
    """Collapse cross-key duplicates (e.g. WA image in image_urls + whatsapp_media).

    Prefer ``raw`` (base64) over URL-only when they collide. Collapse exact
    duplicate URLs. Drop URL-only images once any image with ``raw`` has
    already been kept.
    """
    kept: List[Optional[UploadItem]] = []
    url_index: Dict[str, int] = {}
    kept_image_with_raw = False

    for item in items:
        url = (item.url or "").strip()
        has_raw = item.raw is not None
        is_image = _is_image_item(item)

        if url and url in url_index:
            idx = url_index[url]
            existing = kept[idx]
            if existing is not None and has_raw and existing.raw is None:
                kept[idx] = item
                if is_image:
                    kept_image_with_raw = True
            else:
                pass
            continue

        if is_image and url and not has_raw and kept_image_with_raw:
            continue

        if url:
            url_index[url] = len(kept)
        if is_image and has_raw:
            kept_image_with_raw = True
        kept.append(item)

    return [item for item in kept if item is not None]


def _collect_visitor_media(visitor: Any) -> Tuple[List[UploadItem], bool]:
    """Collect all attachments from visitor.data keys (web + WhatsApp), deduped."""
    data = getattr(visitor, "data", None)
    if not isinstance(data, dict):
        return [], False
    out: List[UploadItem] = []
    dropped_av = False
    for key in _VAULT_UPLOAD_KEYS:
        entries = data.get(key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            item = normalize_upload_entry(entry)
            if item is None:
                continue
            if _is_unsupported_av(item):
                dropped_av = True
                continue
            out.append(item)
    items = _dedupe_upload_items(_apply_filename_fallbacks(visitor, out))
    return items, dropped_av


def collect_visitor_media(visitor: Any) -> Tuple[List[UploadItem], bool]:
    """Public collector for skill tools — same path as interact ingest."""
    return _collect_visitor_media(visitor)


async def _fetch_url_bytes_for_vault(
    url: str, *, max_bytes: int = MAX_UPLOAD_ITEM_BYTES
) -> Optional[bytes]:
    """Download media in jvagent so jvforge never fetches WhatsApp CDN URLs.

    Uses PageIndex ``fetch_url_bytes_capped`` (SSRF guard + per-hop redirect
    validation) so public interact callers cannot point the server at private
    or link-local targets via ``visitor.data`` media URLs.
    """
    from jvspatial.api.exceptions import ValidationError

    from jvagent.action.pageindex.url_guard import fetch_url_bytes_capped

    target = (url or "").strip()
    if not target.startswith(("http://", "https://")):
        return None
    try:
        content, _fname, _ctype = await fetch_url_bytes_capped(
            target,
            max_bytes=max_bytes,
            read_timeout=60.0,
            user_agent="jvagent-artifact-handler/1.0",
        )
        return content or None
    except ValidationError:
        return None
    except Exception:
        return None


async def _save_upload_to_files(
    visitor: Any, item: UploadItem, session_id: str
) -> Optional[str]:
    if item.raw is None:
        return None

    from jvagent.core.app import App
    from jvagent.core.public_url import get_public_base_url
    from jvagent.core.sandbox import (
        resolve_agent_user,
        resolve_user_sandbox_relpath,
        sanitize_segment,
    )

    app = await App.get()
    if app is None:
        return None

    try:
        agent_id, user_id = await resolve_agent_user(visitor)
    except Exception:
        agent_id = (
            getattr(visitor, "_agent", None)
            and getattr(visitor._agent, "id", "")
            or "unknown"
        )
        user_id = session_id or "_default"
    if not agent_id:
        agent_id = "unknown"
    if not user_id:
        user_id = session_id or "_default"

    base_rel = resolve_user_sandbox_relpath(agent_id, user_id)
    safe_session = sanitize_segment(session_id, default="session")
    safe_name = sanitize_segment(item.filename, default=f"vault_{_now_ts()}")
    # Prefer original filename; on collision append _2, _3, ...
    storage_path = f"{base_rel}/artifact_handler/{safe_session}/{safe_name}"
    try:
        if await app.file_exists(storage_path):
            stem, dot, ext = safe_name.rpartition(".")
            if not dot:
                stem, ext = safe_name, ""
            else:
                ext = f".{ext}"
            n = 2
            while True:
                candidate = f"{stem}_{n}{ext}"
                candidate_path = (
                    f"{base_rel}/artifact_handler/{safe_session}/{candidate}"
                )
                if not await app.file_exists(candidate_path):
                    storage_path = candidate_path
                    safe_name = candidate
                    break
                n += 1
                if n > 100:
                    storage_path = (
                        f"{base_rel}/artifact_handler/{safe_session}/"
                        f"{_now_ts()}_{safe_name}"
                    )
                    break
    except Exception:
        pass
    ts = _now_ts()

    metadata = {
        "mime": item.mime,
        "session_id": session_id,
        "original_filename": item.filename,
        "vault": True,
        "ingested_at": ts,
        "source": "artifact_handler",
    }
    try:
        ok = await app.save_file(storage_path, item.raw, metadata=metadata)
        if not ok:
            return None
    except Exception:
        return None

    try:
        rel_url = await app.get_file_url(storage_path)
    except Exception:
        return None
    if not rel_url:
        return None

    pub = get_public_base_url().rstrip("/")
    if pub and rel_url.startswith("/"):
        final_url = f"{pub}{rel_url}"
        return final_url
    return rel_url


_SKILL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "skills",
    "artifact_handler",
    "scripts",
)


class ArtifactHandlerInteractAction(InteractAction):
    """Interact-action that auto-detects media attachments and ingests them,
    plus hosts the jvforge reverse-index, notification sub-callback, and
    LLM tool dispatch for the artifact_handler skill."""

    description: str = attribute(
        default="Detect media attachments and ingest into the artifact handler.",
        description="Action description",
    )
    weight: int = attribute(
        default=-280, description="Runs after intro, before orchestrator"
    )
    always_execute: bool = attribute(
        default=True, description="Always check for media attachments"
    )

    additional_endpoint_path_templates: ClassVar[List[str]] = [
        "/artifact_handler_action/"
    ]

    jvforge_job_index: Dict[str, Dict[str, Any]] = attribute(
        default_factory=dict,
        description=(
            "Reverse index mapping jvforge job_id -> originating user / "
            "conversation / session / channel, so the completion notification "
            "sub-callback can route a proactive message back to the user."
        ),
    )

    notify_webhook_url: Optional[str] = attribute(
        default=None,
        description=(
            "Full inbound jvforge notify URL (includes api_key query when generated)."
        ),
    )
    notify_webhook_api_key_id: Optional[str] = attribute(
        default=None,
        description="API key id for the artifact_handler notify webhook URL.",
    )

    binds_tools_to_visitor: bool = True

    async def get_tools(self) -> list:
        """Return @tool-decorated capability tools for the orchestrator surface.

        Overrides ``InteractAction.get_tools()`` which returns ``[]`` for
        ``always_execute`` actions. This action publishes six capability tools
        (ingest, list, delete, review_expired, check_ingest_status,
        check_pending_attachments) that the orchestrator must surface for
        skill-based tool dispatch — they are NOT routing triggers for the
        action's ``execute()`` method.
        """
        from jvagent.tooling.tool_decorator import collect_tools

        return collect_tools(self)

    # ── InteractAction entry point ──

    async def execute(self, visitor: "InteractWalker") -> None:
        conversation = visitor.conversation
        session_id = str(getattr(visitor, "session_id", "") or "").strip() or None
        user_id = str(getattr(visitor, "user_id", "") or "").strip() or session_id or ""
        page_index: Any = await self.get_action("PageIndexAction")

        if conversation is None:
            await visitor.unrecord_action_execution()
            await self._inject_accessible_documents_parameter(
                visitor, page_index, user_id, session_id
            )
            return

        try:
            items, dropped_av = _collect_visitor_media(visitor)

            if not items and not dropped_av:
                await visitor.unrecord_action_execution()
                return

            if dropped_av and not items:
                await visitor.add_directive(
                    "Tell the user you can help with documents and images but cannot "
                    "save audio or video, and ask them to send a PDF, photo, Word "
                    "file, or a link instead."
                )
                await visitor.unrecord_action_execution()
                return

            if not session_id:
                await visitor.add_directive(
                    "Tell the user you're having trouble connecting to their chat "
                    "session and ask them to reconnect and try sending it again."
                )
                await visitor.unrecord_action_execution()
                return

            utterance = (getattr(visitor, "utterance", "") or "").strip()
            channel = (
                str(getattr(visitor, "channel", "") or "").strip().lower() or "default"
            )

            group_key = f"private_{user_id}"

            ctx = getattr(conversation, "context", None)
            if not isinstance(ctx, dict):
                ctx = {}

            await self._ensure_access_group(visitor, user_id, session_id)

            if page_index is None:
                await visitor.add_directive(
                    "Tell the user you can't save that right now and ask them to "
                    "try again in a moment."
                )
                await visitor.unrecord_action_execution()
                return

            use_async = self._jvforge_configured()
            dv_action = self
            notification_url = None
            if use_async:
                try:
                    notification_url = await self.get_notify_webhook_url()
                except Exception:
                    notification_url = None
                if not notification_url:
                    await visitor.add_directive(
                        "Tell the user you can't reach the artifact handler right now "
                        "and ask them to try again in a moment."
                    )
                    await visitor.unrecord_action_execution()
                    return

            now = _now_ts()
            _DEFAULT_RETENTION_SECONDS = 30 * 86400
            expires_at = now + _DEFAULT_RETENTION_SECONDS

            pending_question = utterance if utterance else None

            conversation_id = ""
            if conversation is not None:
                conversation_id = str(
                    getattr(conversation, "id", None)
                    or getattr(conversation, "conversation_id", None)
                    or ""
                )

            vault = ctx.get("artifact_handler") if isinstance(ctx, dict) else None
            if not isinstance(vault, dict):
                vault = {}
            else:
                vault = dict(vault)

            entries = vault.get(group_key)
            if not isinstance(entries, list):
                entries = []
            else:
                entries = list(entries)

            pending_jobs = vault.get("pending_ingest_jobs")
            if not isinstance(pending_jobs, dict):
                pending_jobs = {}
            else:
                pending_jobs = dict(pending_jobs)

            seen_urls: set = set()
            ingested: List[str] = []
            queued: List[str] = []
            failed: List[str] = []
            saved_items: List[UploadItem] = []

            for idx, item in enumerate(items):
                # Prefer inline bytes (web base64). For URL-only (WhatsApp PDF/md/doc),
                # download in jvagent then save — do not pass WhatsApp CDN URLs to jvforge.
                ingest_url = ""
                if item.raw is not None:
                    saved_url = await _save_upload_to_files(visitor, item, session_id)
                    if saved_url:
                        ingest_url = saved_url
                    else:
                        failed.append(item.filename or "upload")
                        continue
                elif item.url:
                    fetched = await _fetch_url_bytes_for_vault(item.url)
                    if not fetched:
                        failed.append(item.filename or "upload")
                        continue
                    fetched_item = UploadItem(
                        filename=item.filename or "document",
                        mime=item.mime or "application/octet-stream",
                        kind=item.kind or "file",
                        raw=fetched,
                        url="",
                    )
                    saved_url = await _save_upload_to_files(
                        visitor, fetched_item, session_id
                    )
                    if saved_url:
                        ingest_url = saved_url
                    else:
                        failed.append(item.filename or "upload")
                        continue
                else:
                    continue

                if ingest_url in seen_urls:
                    continue
                seen_urls.add(ingest_url)

                filename = item.filename or "document"
                doc_name, display_filename = _vault_doc_ids(user_id, filename)

                pq = pending_question
                item_is_image = (item.kind or "").lower() == "image" or (
                    item.mime or ""
                ).startswith("image/")
                if use_async and dv_action is not None:
                    try:
                        result = await dv_action.submit_ingest(
                            doc=ingest_url,
                            doc_name=doc_name,
                            user_id=user_id,
                            conversation_id=conversation_id,
                            session_id=session_id,
                            channel=channel,
                            is_image=item_is_image,
                            notification_url=notification_url,
                            metadata={"access": group_key},
                            pending_question=pq,
                            filename=display_filename,
                        )
                    except Exception:
                        failed.append(filename)
                        continue

                    job_id = str((result or {}).get("job_id") or "")
                    entry: Dict[str, Any] = {
                        "doc_name": doc_name,
                        "ingested_at": now,
                        "expires_at": expires_at,
                        "filename": display_filename,
                        "source": "upload",
                        "notified": False,
                        "job_id": job_id or None,
                        "status": "queued",
                    }
                    if pq:
                        entry["pending_question"] = pq
                    entries.append(entry)
                    if job_id:
                        pending_entry: Dict[str, Any] = {
                            "doc_name": doc_name,
                            "status": "queued",
                            "submitted_at": now,
                        }
                        if pq:
                            pending_entry["pending_question"] = pq
                        pending_jobs[job_id] = pending_entry
                    queued.append(doc_name)
                    saved_items.append(item)
                else:
                    try:
                        await page_index.assimilate(
                            doc=ingest_url,
                            doc_name=doc_name,
                            metadata={"access": group_key},
                            convert_to_markdown=True,
                            ocr=item_is_image,
                            docling_ocr_engine="gpt4o" if item_is_image else None,
                            generate_description=True,
                        )
                    except Exception:
                        failed.append(filename)
                        continue

                    entry = {
                        "doc_name": doc_name,
                        "ingested_at": now,
                        "expires_at": expires_at,
                        "filename": display_filename,
                        "source": "upload",
                        "notified": False,
                        "status": "ingested",
                    }
                    if pq:
                        entry["pending_question"] = pq
                    entries.append(entry)
                    ingested.append(doc_name)
                    saved_items.append(item)

            vault[group_key] = entries
            vault["pending_ingest_jobs"] = pending_jobs
            saved_names = queued + ingested
            if saved_names:
                vault["active_doc_name"] = saved_names[-1]
            try:
                await conversation.update_context({"artifact_handler": vault})
            except Exception:
                pass
            saved_count = len(queued) + len(ingested)
            kind_phrase = _media_kind_phrase(saved_items)
            verb_is, finished = _kind_verb_finished(kind_phrase)

            if not saved_count and failed:
                directive = (
                    "Tell the user you weren't able to save that and ask them to try "
                    "sending it again."
                )
                await visitor.add_directive(directive)
                await visitor.unrecord_action_execution()
                return

            if saved_count and failed:
                await visitor.add_directive(
                    f"Inform the user their {kind_phrase} {verb_is} being processed "
                    f"and that you will notify them when {finished} finished. Also "
                    f"say that {len(failed)} could not be saved and should be resent. "
                    "Keep it brief and natural."
                )
            elif saved_count:
                question_clause = ""
                if pending_question:
                    question_clause = (
                        f" (3) state that you'll answer their question about "
                        f'question_topic="{pending_question}" based on the '
                        f"{kind_phrase}, once processing has finished"
                    )
                await visitor.add_directive(
                    f"Ignore the user's current question until {kind_phrase} "
                    f"processing is complete. Your entire reply must only: "
                    f"(1) inform the user that their {kind_phrase} {verb_is} "
                    f"currently being processed, (2) state that you'll notify "
                    f"them as soon as processing is complete,"
                    f"{question_clause}. Do not answer, acknowledge, comment on, "
                    f"or reference the substance of the user's question beyond "
                    f"naming its topic. Do not ask any follow-up or clarifying "
                    f"questions. Produce no additional content."
                )
            else:
                await visitor.unrecord_action_execution()
                return

            # Emit ack now and drop remaining walk path (Orchestrator included).
            await self.respond(visitor)
            await visitor.curate_walk_path([])

            # Mark pending jobs as having the processing ack sent, so the
            # webhook notification knows the ack was already delivered to the
            # user and can send the "ready" message without delay.
            ack_ts = _now_ts()
            for _jid, _pentry in pending_jobs.items():
                if (
                    isinstance(_pentry, dict)
                    and str(_pentry.get("status") or "").lower() in _PROCESSING_STATUSES
                ):
                    _pentry["processing_ack_sent_at"] = ack_ts
            vault["pending_ingest_jobs"] = pending_jobs
            try:
                await conversation.update_context({"artifact_handler": vault})
            except Exception:
                pass
        finally:
            await self._inject_accessible_documents_parameter(
                visitor, page_index, user_id, session_id
            )

    # ── Access group helper ──

    async def _ensure_access_group(
        self, visitor: Any, user_id: str, session_id: str
    ) -> None:
        try:
            access_control: Any = await self.get_action("AccessControlAction")
        except Exception:
            access_control = None
        if access_control is None:
            return
        group = f"private_{user_id}"
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

    # ── Accessible documents parameter ──

    async def _inject_accessible_documents_parameter(
        self,
        visitor: "InteractWalker",
        page_index: Any,
        user_id: str,
        session_id: Optional[str],
    ) -> None:
        """Inject an orchestration-scoped parameter listing the user's accessible
        documents (doc_name + description) so the orchestrator knows what the
        user can reference without needing a tool call first."""
        try:
            interaction = visitor.interaction
            if interaction is None:
                return

            if not user_id:
                return

            if page_index is None:
                try:
                    page_index = await self.get_action("PageIndexAction")
                except Exception:
                    page_index = None
            if page_index is None:
                return

            docs: List[Dict[str, Any]] = []
            try:
                docs = await page_index.list_documents(
                    access_control=True,
                    user_id=user_id,
                    session_id=session_id or "",
                    summary=True,
                )
            except Exception:
                return

            active_doc_name = ""
            conversation = getattr(visitor, "conversation", None)
            if conversation is not None:
                ctx = getattr(conversation, "context", None)
                if isinstance(ctx, dict):
                    vault = ctx.get("artifact_handler")
                    if isinstance(vault, dict):
                        active_doc_name = str(
                            vault.get("active_doc_name") or ""
                        ).strip()

            _SELECTION_RULES = (
                "Document selection (mandatory when documents are listed above):\n"
                "1. Match the user's question to a doc_description; if one clearly "
                "fits, call pageindex__search with that doc_name — do not ask which "
                "document first.\n"
                "2. If Active document is set and the question is a follow-up that "
                "fits that document's description, prefer that doc_name.\n"
                "3. Prefer description match over Active document when they conflict.\n"
                "4. If still unclear, call pageindex__search without doc_name before "
                "asking which document.\n"
                "5. Never reply with only a clarifying question before searching when "
                "documents are listed."
            )

            if not docs:
                response_text = "The user currently has no saved documents."
            else:
                lines: List[str] = []
                for d in docs:
                    if not isinstance(d, dict):
                        continue
                    name = str(d.get("doc_name") or "").strip()
                    if not name:
                        continue
                    desc = str(d.get("doc_description") or "").strip()
                    if desc:
                        lines.append(f"- {name}: {desc}")
                    else:
                        lines.append(f"- {name}")
                if lines:
                    parts = [
                        "The user currently has access to these documents:\n"
                        + "\n".join(lines)
                    ]
                    if active_doc_name:
                        parts.append(f"Active document: {active_doc_name}")
                    parts.append(_SELECTION_RULES)
                    response_text = "\n".join(parts)
                else:
                    response_text = "The user currently has no saved documents."

            try:
                await visitor.add_parameter(
                    {
                        "scope": "orchestration",
                        "condition": (
                            "the user asks a content or factual question that could "
                            "come from their uploaded documents, asks about their "
                            "saved documents, uploads, or files, or references a "
                            "document by name, type, pronoun, or description"
                        ),
                        "response": response_text,
                    }
                )
            except Exception:
                pass
        except Exception:
            pass

    def _jvforge_configured(self) -> bool:
        from jvagent.env import get_jvagent_jvforge_base_url

        return bool((get_jvagent_jvforge_base_url() or "").strip())

    async def get_notify_webhook_url(
        self, allowed_ip: Optional[str] = None, regenerate: bool = False
    ) -> str:
        """Public notify URL (+ api_key) jvforge uses for ingest completion pings."""
        from jvspatial.api.auth.api_key_service import APIKeyService
        from jvspatial.api.exceptions import ValidationError
        from jvspatial.core.context import GraphContext
        from jvspatial.db import get_prime_database
        from jvspatial.exceptions import DatabaseError

        from jvagent.core.public_url import get_public_base_url

        from .webhook_auth import (
            ARTIFACT_HANDLER_NOTIFY_ROUTE_PREFIX,
            WEBHOOK_PERMISSION,
            get_or_create_system_user,
            notify_endpoint_for_agent,
        )

        base_url = (get_public_base_url() or "").strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ValidationError(
                message="Set JVAGENT_PUBLIC_BASE_URL to a valid http(s) URL",
                details={"JVAGENT_PUBLIC_BASE_URL": base_url or "(empty)"},
            )

        try:
            agent = await self.get_agent()
            agent_id = str(agent.id)
            expected_url_base = (
                f"{base_url}/api/{ARTIFACT_HANDLER_NOTIFY_ROUTE_PREFIX}/{agent_id}"
            )
            allowed_endpoint = notify_endpoint_for_agent(agent_id)

            def _key_scoped_to_agent(existing_key: Any) -> bool:
                if existing_key is None or not getattr(
                    existing_key, "is_active", False
                ):
                    return False
                existing_eps = list(
                    getattr(existing_key, "allowed_endpoints", None) or []
                )
                if allowed_endpoint not in existing_eps:
                    return False
                for ep in existing_eps:
                    if ARTIFACT_HANDLER_NOTIFY_ROUTE_PREFIX not in ep:
                        continue
                    if ep.endswith("*"):
                        return False
                return True

            prime_ctx = GraphContext(database=get_prime_database())
            api_key_service = APIKeyService(context=prime_ctx)

            if (
                not regenerate
                and self.notify_webhook_url
                and "?api_key=" in self.notify_webhook_url
                and self.notify_webhook_url.startswith(expected_url_base)
                and self.notify_webhook_api_key_id
            ):
                try:
                    existing_key = await api_key_service.get_key(
                        self.notify_webhook_api_key_id
                    )
                    if _key_scoped_to_agent(existing_key):
                        if allowed_ip is not None:
                            requested_ips = [allowed_ip] if allowed_ip else []
                            existing_ips = (
                                getattr(existing_key, "allowed_ips", None) or []
                            )
                            if requested_ips == existing_ips:
                                return self.notify_webhook_url
                        else:
                            return self.notify_webhook_url
                except Exception:
                    pass

            system_user_id = await get_or_create_system_user()

            if self.notify_webhook_api_key_id:
                try:
                    await api_key_service.revoke_key(
                        self.notify_webhook_api_key_id, system_user_id
                    )
                except Exception:
                    pass

            agent_name = getattr(agent, "name", None) or agent_id
            plaintext_key, api_key = await api_key_service.generate_key(
                user_id=system_user_id,
                name=f"ArtifactHandler notify webhook — {agent_name}",
                permissions=[WEBHOOK_PERMISSION],
                expires_in_days=None,
                allowed_ips=[allowed_ip] if allowed_ip else [],
                allowed_endpoints=[allowed_endpoint],
                key_prefix="jv_",
            )

            self.notify_webhook_api_key_id = api_key.id
            self.notify_webhook_url = f"{expected_url_base}?api_key={plaintext_key}"
            await self.save()
            return self.notify_webhook_url

        except DatabaseError:
            raise
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(
                message=f"Notify webhook URL generation failed: {e}",
                details={},
            )

    # ── Reverse-index API ──

    async def register_job(
        self,
        *,
        job_id: str,
        user_id: str,
        conversation_id: str,
        session_id: str,
        channel: str,
        doc_name: str,
        agent_id: str,
        pending_question: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> None:
        if not job_id:
            return
        question = (pending_question or "").strip() or None
        display_name = (filename or "").strip() or None
        index = dict(self.jvforge_job_index or {})
        index[job_id] = {
            "job_id": job_id,
            "user_id": user_id or "",
            "conversation_id": conversation_id or "",
            "session_id": session_id or "",
            "channel": (channel or "").strip() or "default",
            "doc_name": doc_name or "",
            "filename": display_name or "",
            "agent_id": agent_id or "",
            "submitted_at": _utc_iso(),
            "notified": False,
            "pending_question": question,
        }
        self.jvforge_job_index = index
        try:
            await self.save()
        except Exception:
            pass

    async def lookup_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        if not job_id:
            return None
        index = self.jvforge_job_index or {}
        entry = index.get(job_id)
        return dict(entry) if isinstance(entry, dict) else None

    async def clear_job(self, job_id: str) -> None:
        if not job_id:
            return
        index = dict(self.jvforge_job_index or {})
        if job_id in index:
            del index[job_id]
            self.jvforge_job_index = index
            try:
                await self.save()
            except Exception:
                pass

    async def mark_notified(self, job_id: str) -> None:
        if not job_id:
            return
        index = dict(self.jvforge_job_index or {})
        entry = index.get(job_id)
        if isinstance(entry, dict):
            entry = dict(entry)
            entry["notified"] = True
            entry["notified_at"] = _utc_iso()
            index[job_id] = entry
            self.jvforge_job_index = index
            try:
                await self.save()
            except Exception:
                pass

    async def mark_notifying(self, job_id: str) -> None:
        if not job_id:
            return
        index = dict(self.jvforge_job_index or {})
        entry = index.get(job_id)
        if isinstance(entry, dict):
            entry = dict(entry)
            entry["notifying_at"] = _now_ts()
            index[job_id] = entry
            self.jvforge_job_index = index
            try:
                await self.save()
            except Exception:
                pass

    # ── Async jvforge ingest submission ──

    async def submit_ingest(
        self,
        *,
        doc: str,
        doc_name: str,
        user_id: str,
        conversation_id: str,
        session_id: str,
        channel: str,
        is_image: bool = False,
        notification_url: Optional[str] = None,
        notification_secret: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        pending_question: Optional[str] = None,
        filename: Optional[str] = None,
        image_model: Optional[str] = None,
    ) -> Dict[str, Any]:
        from jvagent.action.pageindex.jvforge_assimilate import (
            assimilate_via_jvforge_async,
        )
        from jvagent.env import get_jvagent_jvforge_base_url

        forge_base = (get_jvagent_jvforge_base_url() or "").strip()
        if not forge_base:
            raise ValueError(
                "JVAGENT_JVFORGE_BASE_URL is not set; artifact_handler requires "
                "jvforge for async ingest."
            )

        agent_id = self.agent_id or ""
        llm_webhook_url = ""
        try:
            page_index: Any = await self.get_action("PageIndexAction")
            if page_index is not None:
                llm_webhook_url = await page_index.get_webhook_url() or ""
        except Exception:
            pass
        if not llm_webhook_url:
            raise ValueError(
                "PageIndexAction webhook URL is unavailable; artifact_handler "
                "cannot submit a jvforge async job without it."
            )

        # Import callback (notification_url) is required and separate from LLM webhook.
        notify = (notification_url or "").strip()
        if not notify:
            try:
                notify = (await self.get_notify_webhook_url() or "").strip()
            except Exception:
                notify = ""
        if not notify:
            raise ValueError(
                "artifact_handler notification_url (import callback) is unavailable; "
                "cannot submit a jvforge async job without it."
            )
        notification_url = notify

        file_url = doc if (doc or "").startswith(("http://", "https://")) else None
        effective_image_model = image_model or ("gpt-4o" if is_image else None)
        docling_ocr_engine = "gpt4o" if is_image else None

        result = await assimilate_via_jvforge_async(
            base_url=forge_base,
            agent_id=agent_id,
            doc_name=doc_name,
            model=None,
            if_add_node_summary=True,
            collection_name=agent_id,
            metadata=metadata,
            doc_description=None,
            doc_url=None,
            convert_to_markdown=True,
            ocr=is_image,
            docling_ocr_engine=docling_ocr_engine,
            normalize_bold_headings=False,
            generate_description=True,
            llm_webhook_url=llm_webhook_url,
            emergency=False,
            file_url=file_url,
            filename=None,
            content=None,
            notification_url=notification_url,
            notification_secret=notification_secret,
            image_model=effective_image_model,
        )

        job_id = str(result.get("job_id") or "")
        if job_id:
            await self.register_job(
                job_id=job_id,
                user_id=user_id,
                conversation_id=conversation_id,
                session_id=session_id,
                channel=channel,
                doc_name=doc_name,
                agent_id=agent_id,
                pending_question=pending_question,
                filename=filename,
            )
        return result

    async def get_job_status(self, job_id: str) -> Dict[str, Any]:
        import httpx

        from jvagent.env import get_jvagent_jvforge_base_url

        jid = (job_id or "").strip()
        if not jid:
            return {"status": "unknown", "error": "missing job_id"}
        forge_base = (get_jvagent_jvforge_base_url() or "").strip().rstrip("/")
        if not forge_base:
            return {"status": "unknown", "error": "JVAGENT_JVFORGE_BASE_URL unset"}
        url = f"{forge_base}/v1/jobs/{jid}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url)
            if resp.status_code == 404:
                return {"status": "not_found", "job_id": jid}
            resp.raise_for_status()
            body = resp.json()
            return (
                body if isinstance(body, dict) else {"status": "unknown", "raw": body}
            )
        except Exception as exc:
            return {"status": "unknown", "job_id": jid, "error": str(exc)}

    # ── LLM tools (dispatched to custom_tools.py via VaultToolContext) ──

    def _load_custom_tools(self):
        module_path = os.path.join(_SKILL_DIR, "custom_tools.py")
        if not os.path.isfile(module_path):
            return None
        spec = importlib.util.spec_from_file_location(
            "artifact_handler_custom_tools", module_path
        )
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    async def _dispatch_tool(self, func_name: str, **kwargs: Any) -> str:
        from jvagent.tooling.tool_executor import get_tool_visitor

        from .vault_tool_context import VaultToolContext

        mod = self._load_custom_tools()
        if mod is None:
            return json.dumps(
                {"ok": False, "status": "error", "error": "custom_tools not found"}
            )

        func = getattr(mod, func_name, None)
        if func is None:
            return json.dumps(
                {"ok": False, "status": "error", "error": f"{func_name} not found"}
            )

        visitor = kwargs.pop("visitor", None) or get_tool_visitor()
        ctx = VaultToolContext(visitor=visitor, args=kwargs, action=self)

        try:
            result = await func(ctx)
        except Exception as exc:
            return json.dumps({"ok": False, "status": "error", "error": str(exc)})

        # Apply any directives recorded by the tool via ctx.add_directive().
        for directive in ctx._directives:
            try:
                await visitor.add_directive(directive)
            except Exception:
                pass
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            return json.dumps(result)
        return json.dumps({"result": "ok"})

    def _ingest_tool_args(
        self,
        *,
        url: Optional[str] = None,
        doc_name: Optional[str] = None,
        question: Optional[str] = None,
    ) -> Dict[str, Any]:
        args: Dict[str, Any] = {}
        if url:
            args["url"] = url
        if doc_name:
            args["doc_name"] = doc_name
        if question:
            args["question"] = question
        return args

    @tool(name="artifact_handler__ingest_document")
    async def _t_ingest_document(
        self,
        visitor: Any = None,
        url: Optional[str] = None,
        doc_name: Optional[str] = None,
        question: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Ingest a typed URL only (not media attachments). Call
        artifact_handler__check_pending_attachments first when the user wants
        to ingest/save/upload and you might ask for a URL — if attachments or
        jobs are already pending, do not ask for a URL. Optionally save a
        question to answer when ready. For 'is it ready/finished?' use
        artifact_handler__check_ingest_status, not check_pending_attachments."""
        return await self._dispatch_tool(
            "ingest_document",
            visitor=visitor,
            **self._ingest_tool_args(url=url, doc_name=doc_name, question=question),
        )

    @tool(name="artifact_handler__list_my_documents")
    async def _t_list_my_documents(self, visitor: Any = None, **kwargs: Any) -> str:
        """List the documents the user has saved, with save age and expiry."""
        return await self._dispatch_tool("list_my_documents", visitor=visitor)

    @tool(name="artifact_handler__delete_document")
    async def _t_delete_document(
        self, doc_name: str, visitor: Any = None, **kwargs: Any
    ) -> str:
        """Delete one document from the user's vault by doc_name."""
        return await self._dispatch_tool(
            "delete_document", visitor=visitor, doc_name=doc_name
        )

    @tool(name="artifact_handler__review_expired")
    async def _t_review_expired(self, visitor: Any = None, **kwargs: Any) -> str:
        """Surface documents past the retention window."""
        return await self._dispatch_tool("review_expired", visitor=visitor)

    @tool(name="artifact_handler__check_ingest_status")
    async def _t_check_ingest_status(
        self,
        visitor: Any = None,
        **kwargs: Any,
    ) -> str:
        """Check whether processing documents are ready for querying (queued vs ready).

        Call with **no parameters** — do not pass doc_name or url. Checks all
        pending ingest jobs for this chat session. Use when the user asks if a
        document is ready, finished, done, processed, or if processing is
        complete. Do not use artifact_handler__check_pending_attachments for
        these questions. Queued means not searchable yet. When ready, answer
        content questions via pageindex__search using jobs[].doc_name from the
        result — never a Google Docs/URL id, and do not re-ingest.
        """
        return await self._dispatch_tool("check_ingest_status", visitor=visitor)

    @tool(name="artifact_handler__check_pending_attachments")
    async def _t_check_pending_attachments(
        self,
        visitor: Any = None,
        **kwargs: Any,
    ) -> str:
        """Check for pending media attachments or ingest jobs before starting
        ingest. Call only when the user mentions ingest/save/upload and you
        might ask for a URL — media may already be auto-detected. If pending,
        acknowledge processing and do not ask for a URL. Do not use this tool
        to answer whether a document is ready or finished; use
        artifact_handler__check_ingest_status for that."""
        return await self._dispatch_tool("check_pending_attachments", visitor=visitor)


from . import (  # noqa: F401  # register notify webhook when action module loads
    endpoints,
)
