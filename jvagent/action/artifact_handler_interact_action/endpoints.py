"""ArtifactHandlerInteractAction import + notification callback endpoint.

jvforge POSTs to ``/api/artifact_handler_action/notify/{agent_id}`` with a
``process_document_url`` when an async ingest job finishes. The vault
downloads the artifact, imports the pageindex_graph into PageIndex, then
sends a proactive notification (WhatsApp or Messenger) with a ready notice
and an optional answer. WhatsApp/Messenger send is scheduled with
``jvspatial.create_task`` (generate, then send); if a call returns a
scheduled object it is awaited before 200 so Lambda cannot freeze the send.

On failure the endpoint returns 503 + Retry-After so jvforge retries the
callback. On success it returns 200.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

from fastapi import Request
from fastapi.responses import JSONResponse
from jvspatial import create_task
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response

from .artifact_handler_interact_action import _evict_action_cache
from .ready_message import (  # noqa: F401 — re-export for tests / notify
    _canned_ready_message,
    _canned_ready_message_multi,
    _empty_content_ready_message,
    _file_kind_label,
    _file_type_word,
    _friendly_file_phrase,
    _generate_ready_message,
    _generate_ready_message_multi,
    _ready_document_text,
    _should_quote_filename,
    _truncate_ready_text,
    _useful_source_text,
)

logger = logging.getLogger(__name__)

_RETRY_AFTER_SECONDS = 30
_READY_DESC_LOOKUP_TIMEOUT_S = 5.0
_READY_GENERATE_TIMEOUT_S = 12.0


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
            logger.error(
                "artifact_handler_notify: agent not found agent_id=%s",
                agent_id,
            )
            return None
        action = await agent.get_action_by_type("ArtifactHandlerInteractAction")
        if action is None:
            logger.error(
                "artifact_handler_notify: ArtifactHandlerInteractAction missing "
                "agent_id=%s",
                agent_id,
            )
        return action
    except Exception:
        logger.exception(
            "artifact_handler_notify: action resolve failed agent_id=%s",
            agent_id,
        )
        return None


async def _reload_action(action: Any) -> Any:
    """Reload the action node from DB so lookup_job sees a persisted index."""
    action_id = getattr(action, "id", None)
    if not action_id:
        return action
    try:
        from jvagent.action.base import Action

        await _evict_action_cache(action_id)
        fresh = await Action.get(action_id)
        return fresh if fresh is not None else action
    except Exception:
        logger.exception(
            "artifact_handler_notify: action reload failed action_id=%s",
            action_id,
        )
        return action


def _job_index_from_record(record: Any) -> Dict[str, Any]:
    """``jvforge_job_index`` from a raw find() node document (not entity cache)."""
    if not isinstance(record, dict):
        return {}
    ctx = record.get("context")
    if not isinstance(ctx, dict):
        ctx = {}
    idx = ctx.get("jvforge_job_index")
    if idx is None:
        idx = record.get("jvforge_job_index")
    return idx if isinstance(idx, dict) else {}


async def _scan_sibling_actions_for_job(
    agent_id: str,
    job_id: str,
    *,
    skip_id: Optional[str] = None,
) -> tuple[Optional[Any], Optional[Dict[str, Any]]]:
    """Load every ArtifactHandlerInteractAction for this agent and find job_id."""
    try:
        from jvagent.action.identity import (
            find_records_by_archetype,
            load_action_from_record,
        )

        records = await find_records_by_archetype(
            agent_id, "ArtifactHandlerInteractAction"
        )
    except Exception:
        logger.exception(
            "artifact_handler_notify: sibling scan failed job_id=%s agent_id=%s",
            job_id,
            agent_id,
        )
        return None, None
    if not isinstance(records, list):
        records = []
    loaded: List[Tuple[Any, Dict[str, Any]]] = []
    for record in records:
        rid = str((record or {}).get("id") or "")
        raw_index = _job_index_from_record(record)
        raw_hit = bool(job_id) and job_id in raw_index
        if skip_id and rid == skip_id and not raw_hit:
            continue
        if raw_hit:
            await _evict_action_cache(rid)
        try:
            other = await load_action_from_record(record)
        except Exception:
            logger.exception(
                "artifact_handler_notify: load action failed job_id=%s agent_id=%s "
                "action_id=%s",
                job_id,
                agent_id,
                rid or "?",
            )
            other = None
        if other is None:
            continue
        other = await _reload_action(other)
        loaded.append((other, raw_index))
    for other, raw_index in loaded:
        entry = await other.lookup_job(job_id)
        if not entry:
            raw_entry = raw_index.get(job_id)
            if isinstance(raw_entry, dict):
                merged = dict(getattr(other, "jvforge_job_index", None) or {})
                merged[job_id] = dict(raw_entry)
                other.jvforge_job_index = merged
                entry = dict(raw_entry)
        if entry:
            return other, entry
    return None, None


def _is_trusted_notify_artifact_url(process_document_url: str, job_id: str) -> bool:
    """True when the URL is this job's jvforge artifact on the configured forge origin."""
    from jvagent.action.pageindex.url_guard import (
        is_trusted_jvforge_url,
        rewrite_process_document_url_to_jvforge_base,
    )

    raw = (process_document_url or "").strip()
    jid = (job_id or "").strip()
    if not raw or not jid:
        return False
    path = unquote(urlparse(raw).path or "").rstrip("/")
    if path != f"/v1/artifacts/{jid}":
        return False
    rewritten = rewrite_process_document_url_to_jvforge_base(raw)
    return is_trusted_jvforge_url(rewritten)


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
        logger.exception("artifact_handler notify: desc lookup list_documents failed")
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


async def _await_ready_step(label: str, coro: Any, timeout: float, job_id: str) -> Any:
    """Await a ready-message step; None on timeout or exception."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.error(
            "%s timed out job_id=%s timeout_s=%s",
            label,
            job_id,
            timeout,
        )
        return None
    except Exception:
        logger.exception(
            "%s failed job_id=%s",
            label,
            job_id,
        )
        return None


async def _await_create_task(coro: Any, *, name: str, job_id: str, kind: str) -> None:
    """Shape B: run via ``create_task``; await a non-None scheduled return."""
    del job_id, kind
    scheduled = await create_task(coro, name=name)
    if scheduled is not None:
        await scheduled


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
    internal_doc_name: str = "",
    pending_question: str = "",
) -> bool:
    """Send a WhatsApp message directly via the WhatsApp API.

    Creates an interaction for record-keeping, sets the response, and sends
    the message via ``api.send_message()`` — never via the response bus,
    which would trigger the orchestrator and produce a duplicate reply.
    """
    memory = await agent.get_memory()
    if not memory:
        logger.error(
            "_publish_whatsapp_message: agent has no memory job_id=%s user_id=%s",
            job_id,
            user_id,
        )
        return False

    conversation = None
    if conversation_id:
        try:
            from jvagent.memory.conversation import Conversation

            conversation = await Conversation.get(conversation_id)
        except Exception:
            logger.exception(
                "_publish_whatsapp_message: Conversation.get failed job_id=%s "
                "conversation_id=%s",
                job_id,
                conversation_id,
            )
            conversation = None

    if conversation is None:
        user = await memory.get_user(user_id, create_if_missing=False)
        if not user:
            logger.error(
                "_publish_whatsapp_message: user not found job_id=%s user_id=%s",
                job_id,
                user_id,
            )
            return False
        if session_id:
            conversation = await user.get_conversation_by_session(session_id)
        if conversation is None:
            logger.error(
                "_publish_whatsapp_message: conversation not found job_id=%s "
                "user_id=%s session_id=%s conversation_id=%s",
                job_id,
                user_id,
                session_id,
                conversation_id,
            )
            return False

    effective_session_id = (
        session_id or str(getattr(conversation, "session_id", "") or "").strip() or ""
    )
    if not effective_session_id:
        logger.error(
            "_publish_whatsapp_message: no effective session_id job_id=%s "
            "user_id=%s conversation_id=%s",
            job_id,
            user_id,
            conversation_id,
        )
        return False

    interaction = await conversation.add_interaction(
        utterance="",
        channel="whatsapp",
        session_id=effective_session_id,
    )
    if not interaction:
        logger.error(
            "_publish_whatsapp_message: add_interaction returned None job_id=%s "
            "user_id=%s conversation_id=%s",
            job_id,
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

    if (
        answered
        and (internal_doc_name or "").strip()
        and (pending_question or "").strip()
    ):
        from .vault_events import answered_pending_event, record_vault_event

        await record_vault_event(
            interaction,
            answered_pending_event(internal_doc_name, pending_question),
        )

    await interaction.save()

    whatsapp_action = await agent.get_action_by_type("WhatsAppAction")
    if whatsapp_action is None:
        logger.error(
            "_publish_whatsapp_message: WhatsAppAction missing job_id=%s user_id=%s",
            job_id,
            user_id,
        )
        return False

    try:
        if not whatsapp_action.is_configured():
            logger.error(
                "_publish_whatsapp_message: WhatsAppAction not configured "
                "job_id=%s user_id=%s",
                job_id,
                user_id,
            )
            return False
    except Exception:
        logger.exception(
            "_publish_whatsapp_message: is_configured failed job_id=%s user_id=%s",
            job_id,
            user_id,
        )
        return False

    try:
        api = await whatsapp_action.api()
    except Exception:
        logger.exception(
            "_publish_whatsapp_message: api() failed job_id=%s user_id=%s",
            job_id,
            user_id,
        )
        return False

    try:
        result = await api.send_message(
            phone=user_id,
            message=content,
        )
        ok = isinstance(result, dict) and bool(result.get("ok", True))
        if not ok:
            logger.error(
                "_publish_whatsapp_message: send_message not ok job_id=%s "
                "user_id=%s result=%r",
                job_id,
                user_id,
                result,
            )
        return ok
    except Exception:
        logger.exception(
            "_publish_whatsapp_message: send_message failed job_id=%s user_id=%s",
            job_id,
            user_id,
        )
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
    internal_doc_name: str = "",
    pending_question: str = "",
) -> bool:
    """Send a Facebook Messenger message via the registered FacebookAction.

    Creates an interaction for record-keeping, sets the response, and sends
    via ``FacebookAPI.send_text_message()`` on the live FacebookAction held by
    the registered MessengerAdapter — never via ``response_bus.publish``, which
    would append a duplicate reply onto the interaction.
    """
    memory = await agent.get_memory()
    if not memory:
        logger.error("_publish_messenger_message: agent has no memory, cannot send")
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
            logger.error(
                "_publish_messenger_message: user not found user_id=%s", user_id
            )
            return False
        if session_id:
            conversation = await user.get_conversation_by_session(session_id)
        if conversation is None:
            logger.error(
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
        logger.error(
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
        logger.error(
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

    if (
        answered
        and (internal_doc_name or "").strip()
        and (pending_question or "").strip()
    ):
        from .vault_events import answered_pending_event, record_vault_event

        await record_vault_event(
            interaction,
            answered_pending_event(internal_doc_name, pending_question),
        )

    await interaction.save()

    # Use the already-registered live FacebookAction held by MessengerAdapter
    # (startup-resolved Page token). Never call api() on a fresh find_one instance.
    try:
        response_bus = await agent.get_response_bus()
    except Exception:
        logger.exception("_publish_messenger_message: get_response_bus failed")
        return False
    if not response_bus:
        logger.error("_publish_messenger_message: no response bus")
        return False

    adapter = response_bus._channel_adapters.get("messenger")
    if not adapter or not getattr(adapter, "_initialized", False):
        facebook_action = await agent.get_action_by_type("FacebookAction")
        if facebook_action is None:
            logger.error(
                "_publish_messenger_message: FacebookAction not found on agent"
            )
            return False
        try:
            await facebook_action.ensure_page_access_token()
            await facebook_action.ensure_adapter_registered()
        except Exception:
            logger.exception("_publish_messenger_message: ensure adapter/token failed")
            return False
        adapter = response_bus._channel_adapters.get("messenger")
        if not adapter:
            logger.error("_publish_messenger_message: MessengerAdapter not registered")
            return False

    facebook_action = getattr(adapter, "action", None)
    if facebook_action is None:
        logger.error(
            "_publish_messenger_message: MessengerAdapter has no FacebookAction"
        )
        return False

    try:
        if not facebook_action.is_configured():
            logger.error("_publish_messenger_message: FacebookAction not configured")
            return False
    except Exception:
        logger.exception(
            "_publish_messenger_message: FacebookAction is_configured() failed"
        )
        return False

    try:
        api = facebook_action.api()
    except Exception:
        logger.exception(
            "_publish_messenger_message: FacebookAction.api() failed on "
            "registered action"
        )
        return False

    try:
        result = await asyncio.to_thread(api.send_text_message, user_id, content)
        if isinstance(result, dict) and result.get("error"):
            logger.error(
                "_publish_messenger_message: send_text_message error for "
                "user_id=%s: %s",
                user_id,
                result.get("error"),
            )
            return False
        return True
    except Exception:
        logger.exception(
            "_publish_messenger_message: send_message failed job_id=%s user_id=%s",
            job_id,
            user_id,
        )
        return False


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
            logger.error(
                "artifact_handler import: artifact fetch failed attempt=%s/%s: %s",
                attempt,
                _ARTIFACT_404_RETRIES,
                msg,
            )
            return None

    if not raw_bytes:
        logger.error("artifact_handler import: empty artifact body")
        return None

    try:
        graph = json.loads(raw_bytes)
    except Exception:
        logger.exception("artifact_handler import: artifact is not JSON")
        return None

    if not isinstance(graph, dict):
        logger.error("artifact_handler import: artifact JSON is not an object")
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
        logger.exception(
            "artifact_handler import: PageIndex import failed agent_id=%s",
            agent_id,
        )
        return None

    return effective_name


async def _close_reverse_index_job(action: Any, job_id: str) -> None:
    """Mark notified and drop the jvforge reverse-index entry."""
    if action is None or not job_id:
        return
    try:
        await action.mark_notified(job_id)
        await action.clear_job(job_id)
    except Exception:
        logger.exception(
            "artifact_handler_notify: clear_job failed job_id=%s",
            job_id,
        )


async def _notify_and_close_job(
    send_coro: Any,
    *,
    action: Any,
    job_id: str,
    channel: str,
    agent_id: str,
) -> bool:
    """Await a channel send, then close the reverse-index job on success."""
    sent = bool(await send_coro)
    if sent:
        await _close_reverse_index_job(action, job_id)
        return True
    logger.error(
        "artifact_handler_notify: %s send failed job_id=%s "
        "agent_id=%s; leaving reverse-index job for retry",
        channel,
        job_id,
        agent_id,
    )
    return False


@endpoint(
    "/artifact_handler_action/notify/{agent_id}",
    methods=["POST"],
    webhook=True,
    auth=False,
    tags=["ArtifactHandlerInteractAction"],
    summary="jvforge async ingest completion callback (import + notification)",
    description=(
        "jvforge POSTs ``process_document_url`` when an async ingest job finishes. "
        "Authorization is a known reverse-index ``job_id`` plus a trusted "
        "jvforge ``/v1/artifacts/{job_id}`` URL."
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
        1. Resolve the ArtifactHandlerInteractAction and reload from DB.
        2. Require a known ``job_id`` in the reverse index (blocks replay/spam import).
        3. Require ``process_document_url`` to be this job's trusted jvforge artifact.
        4. Download artifact from ``process_document_url`` and import into PageIndex.
        5. Mark the job as ``ready`` in conversation ``pending_ingest_jobs``.
        6. For WhatsApp/Messenger: two sequential ``create_task`` calls
           (generate, then send). If a call returns a scheduled object it is
           awaited before 200 so Lambda cannot freeze the send.
           ``mark_notified`` / ``clear_job`` run only after a successful send.
        7. Return 200 on success, 503 + Retry-After on failure (so jvforge retries).
           A failed channel send leaves the reverse-index job in place.
    """
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
    if action is not None:
        action = await _reload_action(action)

    # Job lookup BEFORE download/import — unknown jobs must not trigger
    # expensive PageIndex writes (replay / forged callbacks). Scan every
    # ArtifactHandlerInteractAction for this agent; get_action_by_type may
    # return a different node than the ingest Lambda saved. 503 so jvforge
    # retries when the reverse-index save is not yet visible.
    action_id = str(getattr(action, "id", None) or "") if action is not None else ""
    entry = await action.lookup_job(job_id) if action is not None else None
    if not entry:
        other, other_entry = await _scan_sibling_actions_for_job(
            agent_id,
            job_id,
            skip_id=action_id or None,
        )
        if other is not None and other_entry:
            action = other
            entry = other_entry
        else:
            logger.error(
                "artifact_handler_notify: unknown job_id=%s agent_id=%s "
                "action_id=%s",
                job_id,
                agent_id,
                action_id or "-",
            )
            return JSONResponse(
                status_code=503,
                content={"detail": "unknown job_id"},
                headers={"Retry-After": str(_RETRY_AFTER_SECONDS)},
            )

    entry_agent = str(entry.get("agent_id") or "").strip()
    if entry_agent and entry_agent != agent_id:
        logger.error(
            "artifact_handler_notify: job_id=%s belongs to agent_id=%s not %s",
            job_id,
            entry_agent,
            agent_id,
        )
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

    if not _is_trusted_notify_artifact_url(process_document_url, job_id):
        logger.error(
            "artifact_handler_notify: untrusted artifact URL job_id=%s agent_id=%s",
            job_id,
            agent_id,
        )
        return JSONResponse(
            status_code=403,
            content={
                "detail": "process_document_url is not this job's trusted artifact"
            },
        )

    from jvagent.core.agent import Agent

    if await Agent.get(agent_id) is None:
        logger.error(
            "artifact_handler_notify: agent not found after lookup job_id=%s "
            "agent_id=%s",
            job_id,
            agent_id,
        )
        return JSONResponse(
            status_code=503,
            content={"detail": "agent not found"},
            headers={"Retry-After": str(_RETRY_AFTER_SECONDS)},
        )

    imported_doc_name = await _download_and_import_graph(process_document_url, agent_id)
    if not imported_doc_name:
        logger.error(
            "artifact_handler_notify: graph import failed job_id=%s agent_id=%s",
            job_id,
            agent_id,
        )
        return JSONResponse(
            status_code=503,
            content={"detail": "graph import failed"},
            headers={"Retry-After": str(_RETRY_AFTER_SECONDS)},
        )

    user_id = str(entry.get("user_id") or "").strip()
    session_id = str(entry.get("session_id") or "").strip()
    conversation_id = str(entry.get("conversation_id") or "").strip()
    channel = str(entry.get("channel") or "").strip().lower() or "default"
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

            from .job_status import PROCESSING_STATUSES, apply_ingest_job_status

            conv = await Conversation.get(conversation_id)
            if conv is not None:
                pending = {}
                ctx = getattr(conv, "context", None)
                if isinstance(ctx, dict):
                    vault = ctx.get("artifact_handler")
                    if isinstance(vault, dict):
                        raw = vault.get("pending_ingest_jobs")
                        if isinstance(raw, dict):
                            pending = raw
                prev = (
                    pending.get(job_id) if isinstance(pending.get(job_id), dict) else {}
                )
                prev_status = str(prev.get("status") or "").lower()
                if prev_status in PROCESSING_STATUSES or prev_status == "":
                    ok = await apply_ingest_job_status(
                        conv,
                        job_id,
                        "ready",
                        doc_name=internal_doc_name or None,
                    )
                    if not ok:
                        logger.error(
                            "artifact_handler_notify: mark-ready failed job_id=%s",
                            job_id,
                        )
        except Exception:
            logger.exception(
                "artifact_handler_notify: mark-ready failed job_id=%s",
                job_id,
            )
    # ── Send proactive notifications.
    # WhatsApp and Messenger get push messages; web/default relies on
    # check_ingest_status polling (TODO: add web push in a future phase).
    # Two sequential Shape B tasks (generate, then send). Await a non-None
    # create_task return so Lambda cannot 200-and-freeze the send. Close the
    # reverse-index job only after a successful send.
    notified = False
    if user_id and channel in ("whatsapp", "messenger"):
        content_box: List[Tuple[str, bool]] = []

        async def _generate() -> None:
            content_box.append(
                await _generate_ready_content(
                    agent_id=agent_id,
                    job_id=job_id or "",
                    internal_doc_name=internal_doc_name,
                    display_doc=display_doc,
                    pending_question=pending_question,
                )
            )

        await _await_create_task(
            _generate(),
            name=f"artifact_handler_generate_{job_id}",
            job_id=job_id or "",
            kind="generate",
        )
        if content_box:
            content, answered = content_box[0]
        else:
            content = _canned_ready_message(
                display_doc,
                pending_question=pending_question or None,
            )
            answered = False

        sent_box: List[bool] = []

        async def _send() -> None:
            from jvagent.core.agent import Agent

            try:
                agent = await Agent.get(agent_id)
                if agent is None:
                    logger.error(
                        "artifact_handler_notify: agent not found for send "
                        "agent_id=%s job_id=%s",
                        agent_id,
                        job_id,
                    )
                    sent_box.append(False)
                    return
                if channel == "whatsapp":
                    send_coro = _publish_whatsapp_message(
                        agent=agent,
                        user_id=user_id,
                        session_id=session_id,
                        conversation_id=conversation_id,
                        content=content,
                        display_doc=display_doc,
                        job_id=job_id or "",
                        answered=answered,
                        internal_doc_name=internal_doc_name,
                        pending_question=pending_question,
                    )
                else:
                    send_coro = _publish_messenger_message(
                        agent=agent,
                        user_id=user_id,
                        session_id=session_id,
                        conversation_id=conversation_id,
                        content=content,
                        display_doc=display_doc,
                        job_id=job_id or "",
                        answered=answered,
                        internal_doc_name=internal_doc_name,
                        pending_question=pending_question,
                    )
                sent_box.append(
                    await _notify_and_close_job(
                        send_coro,
                        action=action,
                        job_id=job_id or "",
                        channel=channel,
                        agent_id=agent_id,
                    )
                )
            except Exception:
                logger.exception(
                    "artifact_handler_notify: send failed job_id=%s",
                    job_id,
                )
                sent_box.append(False)

        await _await_create_task(
            _send(),
            name=f"artifact_handler_{channel}_{job_id}",
            job_id=job_id or "",
            kind="send",
        )
        sent = bool(sent_box and sent_box[0])
        if not sent:
            return JSONResponse(
                status_code=503,
                content={"detail": f"{channel} notify failed"},
                headers={"Retry-After": str(_RETRY_AFTER_SECONDS)},
            )
        notified = True
    else:
        await _close_reverse_index_job(action, job_id)

    return {
        "status": "imported",
        "job_id": job_id,
        "notified": notified,
        "doc_name": imported_doc_name,
    }


async def _generate_ready_content(
    *,
    agent_id: str,
    job_id: str,
    internal_doc_name: str,
    display_doc: str,
    pending_question: str,
) -> Tuple[str, bool]:
    """Build ready-notice text: 5s desc lookup + 12s generate, else canned.

    Returns ``(content, answered)``. ``answered`` is True only when generate
    produced a message before timeout.
    """
    from jvagent.core.agent import Agent

    try:
        agent = await Agent.get(agent_id)
        if agent is None:
            logger.error(
                "_generate_ready_content: agent not found agent_id=%s job_id=%s",
                agent_id,
                job_id,
            )
            return (
                _canned_ready_message(
                    display_doc,
                    pending_question=pending_question or None,
                ),
                False,
            )

        action = await _resolve_action(agent_id)
        single_entry = {
            "internal_doc_name": internal_doc_name,
            "display_doc": display_doc,
            "pending_question": pending_question,
        }
        desc_lookup = await _await_ready_step(
            "_generate_ready_content: desc lookup",
            _doc_description_lookup(agent, [single_entry]),
            _READY_DESC_LOOKUP_TIMEOUT_S,
            job_id,
        )
        if not isinstance(desc_lookup, dict):
            desc_lookup = {}
        doc_description = desc_lookup.get(internal_doc_name, "")

        content: Optional[str] = None
        answered = False
        if pending_question and internal_doc_name and action is not None:
            content = await _await_ready_step(
                "_generate_ready_content: generate",
                _generate_ready_message(
                    agent=agent,
                    vault_action=action,
                    internal_doc_name=internal_doc_name,
                    display_doc=display_doc,
                    utterance=pending_question,
                    doc_description=doc_description or None,
                ),
                _READY_GENERATE_TIMEOUT_S,
                job_id,
            )
            if content:
                answered = True

        if not content:
            content = _canned_ready_message(
                display_doc,
                doc_description=doc_description,
                pending_question=pending_question or None,
            )
        return content, answered
    except Exception:
        logger.exception(
            "_generate_ready_content: using canned ready message job_id=%s",
            job_id,
        )
        return (
            _canned_ready_message(
                display_doc,
                pending_question=pending_question or None,
            ),
            False,
        )


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
) -> bool:
    """Generate ready text then publish via WhatsApp (used by tests)."""
    from jvagent.core.agent import Agent

    content, answered = await _generate_ready_content(
        agent_id=agent_id,
        job_id=job_id,
        internal_doc_name=internal_doc_name,
        display_doc=display_doc,
        pending_question=pending_question,
    )
    agent = await Agent.get(agent_id)
    if agent is None:
        return False
    return await _publish_whatsapp_message(
        agent=agent,
        user_id=user_id,
        session_id=session_id,
        conversation_id=conversation_id,
        content=content,
        display_doc=display_doc,
        job_id=job_id,
        answered=answered,
        internal_doc_name=internal_doc_name,
        pending_question=pending_question,
    )
