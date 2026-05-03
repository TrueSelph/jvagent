import asyncio
import copy
import logging
import os
import threading
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Set

from jvspatial.api.auth.api_key_service import APIKeyService
from jvspatial.core.annotations import attribute
from jvspatial.core.context import GraphContext
from jvspatial.db import get_prime_database
from jvspatial.exceptions import DatabaseError, ValidationError

from jvagent.action.google.google_action import GoogleAction
from jvagent.action.pageindex.adapter import strip_redundant_md_suffix
from jvagent.action.pageindex.config import (
    get_pageindex_node_summary,
    initialize_pageindex_database,
)
from jvagent.action.pageindex.documents import (
    _get_app_id_from_node,
    assimilate_document,
    delete_document,
    list_documents,
)
from jvagent.action.pageindex.jvforge_assimilate import (
    assimilate_via_jvforge,
    assimilate_via_jvforge_async,
)
from jvagent.action.pageindex.pageindex_action import (
    ensure_ingestion_config_for_agent,
)
from jvagent.core.public_url import get_public_base_url
from jvagent.env import get_jvagent_jvforge_base_url

from ..jvforge_routing import resolve_effective_jvforge_base
from .drive_ingest_filter import (
    filter_drive_doc_queues_for_ingestible,
    is_drive_file_pageindex_ingestible,
)
from .google_drive_documents import GoogleDriveDocuments
from .webhook_auth import get_or_create_system_user

logger = logging.getLogger(__name__)

_FOLDER_MIME = "application/vnd.google-apps.folder"


def _drive_resolve_docling_ocr(
    docling_ocr_engine: Optional[str],
    ocr: bool,
) -> tuple[bool, Optional[str]]:
    """Match PageIndex multipart semantics for Drive ingest."""
    raw = (docling_ocr_engine or "").strip()
    if raw:
        de = raw.lower()
        if de in ("none", "off", "no", "false", "0"):
            return False, None
        return True, "rapidocr"
    return bool(ocr), None


def _merge_disable_ingestion_from_old(
    old_files: List[Dict], new_files: List[Dict]
) -> None:
    """Copy ``disable_ingestion`` from the previous tree onto the fresh Drive listing (same file ids)."""
    old_by_id: Dict[str, Dict[str, Any]] = {}

    def collect_old(items: List[Dict[str, Any]]) -> None:
        for it in items:
            old_by_id[it["id"]] = it
            nested = it.get("files")
            if nested:
                collect_old(nested)

    def apply_new(items: List[Dict[str, Any]]) -> None:
        for it in items:
            prev = old_by_id.get(it["id"])
            if prev and prev.get("disable_ingestion"):
                it["disable_ingestion"] = True
            else:
                it.setdefault("disable_ingestion", False)
            nested = it.get("files")
            if nested:
                apply_new(nested)

    collect_old(old_files)
    apply_new(new_files)


def _disabled_file_ids(files: List[Dict[str, Any]]) -> Set[str]:
    """Drive file ids marked ``disable_ingestion`` (folders excluded)."""
    out: Set[str] = set()

    def walk(items: List[Dict[str, Any]]) -> None:
        for it in items:
            if it.get("mimeType") != _FOLDER_MIME and it.get("disable_ingestion"):
                fid = it.get("id")
                if fid:
                    out.add(str(fid))
            nested = it.get("files")
            if nested:
                walk(nested)

    walk(files)
    return out


def _queue_item_file_id(item: Any, queue_key: str) -> str:
    if not isinstance(item, dict):
        return ""
    if queue_key == "added" or queue_key == "removed":
        return str(item.get("id", ""))
    # modified: compare_files uses {"id", "old", "new"}; failures may store a plain file dict
    if "new" in item and isinstance(item.get("new"), dict):
        return str(item.get("new", {}).get("id", item.get("id", "")))
    return str(item.get("id", ""))


def _find_file_dict_in_tree(
    items: List[Dict[str, Any]], file_id: str
) -> Optional[Dict[str, Any]]:
    """Return the file dict for ``file_id`` (non-folder) in a nested Drive ``files`` tree."""
    fid = str(file_id)
    for it in items:
        if str(it.get("id")) == fid:
            if it.get("mimeType") == _FOLDER_MIME:
                nested = it.get("files")
                if nested:
                    found = _find_file_dict_in_tree(nested, fid)
                    if found is not None:
                        return found
                continue
            return it
        nested = it.get("files")
        if nested:
            found = _find_file_dict_in_tree(nested, fid)
            if found is not None:
                return found
    return None


def _extract_and_prepend_queue_item(doc_queues: Dict[str, Any], file_id: str) -> bool:
    """Move the first queue entry matching ``file_id`` to the front of its bucket."""
    fid = str(file_id)
    for key in ("added", "modified", "removed"):
        lst = list(doc_queues.get(key) or [])
        for i, item in enumerate(lst):
            if _queue_item_file_id(item, key) == fid:
                found = lst.pop(i)
                doc_queues[key] = [found] + lst
                return True
    return False


def _strip_file_id_from_doc_queues(doc_queues: Dict[str, Any], file_id: str) -> None:
    """Remove every queue entry whose file id matches ``file_id``."""
    fid = str(file_id)
    for key in ("added", "modified", "removed"):
        doc_queues[key] = [
            x for x in (doc_queues.get(key) or []) if _queue_item_file_id(x, key) != fid
        ]


def _filter_queue_for_disabled(
    items: List[Any], disabled: Set[str], queue_key: str
) -> List[Any]:
    return [x for x in items if _queue_item_file_id(x, queue_key) not in disabled]


def _filter_doc_queues_for_disabled(docs: Dict[str, Any], disabled: Set[str]) -> None:
    for key in ("added", "modified", "removed"):
        docs[key] = _filter_queue_for_disabled(list(docs.get(key) or []), disabled, key)


def _sync_drive_node_status_from_queues(node: Any) -> None:
    """Set folder sync status from ingesting vs failed queue depth (not during active_document)."""
    if getattr(node, "active_document", None):
        return
    ing = node.ingesting_documents
    fd = node.failed_documents
    pending_ingest = bool(ing.get("added") or ing.get("modified") or ing.get("removed"))
    pending_fail = bool(fd.get("added") or fd.get("modified") or fd.get("removed"))
    if not pending_ingest and not pending_fail:
        node.status = "completed"
    elif pending_ingest:
        node.status = "pending"
    elif pending_fail:
        node.status = "failed"


_GOOGLE_DRIVE_DOCUMENTS_STATUS_ALLOW = frozenset(
    {"pending", "processing", "completed", "failed"}
)


def _validate_doc_queues_payload(
    data: Dict[str, Any], *, label: str
) -> Dict[str, List[Any]]:
    """Ensure dict has added/modified/removed list keys."""
    for key in ("added", "modified", "removed"):
        if key not in data or not isinstance(data[key], list):
            raise ValidationError(
                message=f"{label} must be an object with list keys "
                f"added, modified, removed",
                details={"label": label, "missing_or_invalid": key},
            )
    return {
        "added": copy.deepcopy(data["added"]),
        "modified": copy.deepcopy(data["modified"]),
        "removed": copy.deepcopy(data["removed"]),
    }


async def _if_add_node_summary_for_jvforge(
    agent_id: str,
    node_summary: Optional[Any],
) -> str:
    """Match PageIndex REST ingest: resolve yes/no for jvforge if_add_node_summary."""
    if node_summary is None:
        await ensure_ingestion_config_for_agent(agent_id)
        return "yes" if get_pageindex_node_summary() else "no"
    if isinstance(node_summary, str):
        sl = node_summary.strip().lower()
        if sl in ("yes", "no"):
            return sl
        if sl in ("true", "1"):
            return "yes"
        if sl in ("false", "0"):
            return "no"
    return "yes" if node_summary else "no"


@dataclass
class DriveIngestConfig:
    """Shared ingest settings for one Drive document (from PageIndex + flags)."""

    collection_name: str
    metadata: Dict[str, Any]
    model: Optional[str]
    model_action: Optional[Any]
    node_summary: Optional[Any]
    agent_id: str
    page_index_action: Any
    convert_to_markdown: bool = False
    ocr: bool = False
    docling_ocr_engine: Optional[str] = None
    normalize_bold_headings: bool = False
    skip_existing_documents: bool = True
    use_jvforge: Optional[bool] = None


# Per-folder locks to prevent duplicate GoogleDriveDocuments on concurrent requests
_sync_locks: Dict[str, asyncio.Lock] = {}
_sync_locks_guard = asyncio.Lock()

# Per-agent locks to serialize ingestion (one document per webhook, no concurrent runs)
_ingestion_locks: Dict[str, asyncio.Lock] = {}
_ingestion_locks_guard = asyncio.Lock()


async def _get_ingestion_lock(agent_id: str) -> asyncio.Lock:
    """Get an async lock for the given agent to serialize ingestion."""
    async with _ingestion_locks_guard:
        if agent_id not in _ingestion_locks:
            _ingestion_locks[agent_id] = asyncio.Lock()
        return _ingestion_locks[agent_id]


async def _get_folder_lock(action_id: str, folder_id: str) -> asyncio.Lock:
    """Get an async lock for the given action+folder to serialize get-or-create."""
    key = f"{action_id}:{folder_id}"
    async with _sync_locks_guard:
        if key not in _sync_locks:
            _sync_locks[key] = asyncio.Lock()
        return _sync_locks[key]


async def _pop_disabled_head_queues(node: Any, queues: Dict[str, Any]) -> bool:
    """Drop disabled file ids from the front of added/modified queues; save if mutated."""
    disabled = _disabled_file_ids(node.files)
    changed = False
    for key in ("added", "modified"):
        lst = queues[key]
        while lst:
            fid = _queue_item_file_id(lst[0], key)
            if fid and fid in disabled:
                lst.pop(0)
                changed = True
            else:
                break
    if changed:
        _sync_drive_node_status_from_queues(node)
        await node.save()
    return changed


async def _pop_skip_head(
    node: Any,
    *,
    source: str,
    doc_type: str,
    doc_name: str,
    ingestion_message: str,
) -> Dict[str, Any]:
    """Remove head item from ingest queue without marking failed (unsupported or duplicate)."""
    docs = getattr(node, source)
    if doc_type == "added":
        if docs["added"]:
            docs["added"].pop(0)
    elif doc_type == "modified":
        if docs["modified"]:
            docs["modified"].pop(0)
    node.active_document = ""
    _sync_drive_node_status_from_queues(node)
    await node.save()
    return {
        "success": True,
        "skipped": True,
        "doc_name": doc_name,
        "ingestion_message": ingestion_message,
    }


def _drive_name_first_segment(name: str) -> str:
    """Characters before the first ``.`` in a filename (e.g. ``a.b.c`` → ``a``)."""
    return (name or "").strip().split(".", 1)[0]


def _build_skip_existing_indexes(
    docs: List[Dict[str, Any]],
) -> tuple[Set[str], Set[str]]:
    """Full indexed names plus first-segment keys for skip-existing matching."""
    indexed_full: Set[str] = set()
    indexed_first: Set[str] = set()
    for d in docs:
        raw = str(d.get("doc_name") or "").strip()
        if not raw:
            continue
        indexed_full.add(raw)
        for variant in (raw, strip_redundant_md_suffix(raw)):
            if not variant:
                continue
            seg = _drive_name_first_segment(variant)
            if seg:
                indexed_first.add(seg)
    return indexed_full, indexed_first


def _drive_fname_matches_indexed(
    fname: Optional[str],
    indexed_full: Set[str],
    indexed_first: Set[str],
) -> bool:
    """True if Drive ``fname`` is duplicate of an indexed doc (full, md-strip, or first segment)."""
    fn = (fname or "").strip()
    if not fn:
        return False
    if fn in indexed_full:
        return True
    norm = strip_redundant_md_suffix(fn)
    if norm and norm in indexed_full:
        return True
    seg = _drive_name_first_segment(fn)
    if seg and seg in indexed_first:
        return True
    if norm:
        seg2 = _drive_name_first_segment(norm)
        if seg2 and seg2 in indexed_first:
            return True
    return False


async def _drive_added_fname_matches_indexed(fname: str, collection_name: str) -> bool:
    """True if ``fname`` should be skipped as already present in PageIndex (same rules as prune)."""
    if not (fname or "").strip():
        return False
    docs = await list_documents(collection_name=collection_name)
    full, first = _build_skip_existing_indexes(docs)
    return _drive_fname_matches_indexed(fname, full, first)


async def _prune_added_queue_skip_existing(
    node: Any,
    collection_name: str,
    *,
    skip_existing_documents: bool,
) -> None:
    """Remove every ``added`` item already represented in PageIndex.

    Matches exact ``doc_name``, ``strip_redundant_md_suffix``, or same leading segment
    before the first dot (e.g. ``ChargeReportForm.doc.md`` vs indexed ``ChargeReportForm.doc``).
    """
    if not skip_existing_documents:
        return
    added = list(node.ingesting_documents.get("added") or [])
    if not added:
        return
    docs = await list_documents(collection_name=collection_name)
    indexed_full, indexed_first = _build_skip_existing_indexes(docs)

    kept = [
        item
        for item in added
        if not isinstance(item, dict)
        or not _drive_fname_matches_indexed(
            item.get("name"), indexed_full, indexed_first
        )
    ]
    if len(kept) == len(added):
        return
    node.ingesting_documents["added"] = kept
    _sync_drive_node_status_from_queues(node)
    await node.save()


class PageIndexGoogleDriveSyncAction(GoogleAction):
    """Sync Google Drive folders into PageIndex using OAuth2 (inherits GoogleAction)."""

    google_drive_folders: List[dict] = attribute(
        default_factory=list,
        description="List of Google Drive folder configurations to monitor and ingest. Each folder config should include 'folder_id':str and optional 'metadata':dict to attach to ingested documents. ",
    )

    page_index_action: str = attribute(
        default="PageIndexAction",
        description="The action to use for ingesting documents.",
    )

    webhook_url: Optional[str] = attribute(
        default=None,
        description="Webhook URL (auto-generated if not provided)",
    )

    webhook_api_key_id: Optional[str] = attribute(
        default=None, description="ID of the API key used for webhook authentication"
    )

    base_url: Optional[str] = attribute(
        default=None,
        description="Application base URL for webhook generation (JVAGENT_PUBLIC_BASE_URL env var, e.g., https://myapp.example.com)",
    )

    document_timeout: Optional[int] = attribute(
        default=600,
        description="Document timeout",
    )

    async def _mark_drive_ingest_failed(
        self,
        google_drive_documents_node: Any,
        *,
        source: str,
        doc_type: str,
        file_info: Dict[str, Any],
    ) -> None:
        docs = getattr(google_drive_documents_node, source)
        if doc_type == "added":
            docs["added"].pop(0)
            google_drive_documents_node.failed_documents["added"].append(file_info)
        else:
            docs["modified"].pop(0)
            google_drive_documents_node.failed_documents["modified"].append(file_info)
        google_drive_documents_node.active_document = ""
        _sync_drive_node_status_from_queues(google_drive_documents_node)
        await google_drive_documents_node.save()

    async def _execute_drive_document_ingest(
        self,
        *,
        google_drive_action: Any,
        doc_name: str,
        file_id: str,
        doc_url: str,
        cfg: DriveIngestConfig,
        cancel_event: threading.Event,
    ) -> Dict[str, Any]:
        file_bytes = await google_drive_action.get_media(file_id=file_id)
        forge_base = (get_jvagent_jvforge_base_url() or "").strip()
        effective_forge = resolve_effective_jvforge_base(
            forge_base, use_jvforge=cfg.use_jvforge
        )
        if cfg.normalize_bold_headings and not effective_forge:
            raise ValidationError(
                "normalize_bold_headings requires JVAGENT_JVFORGE_BASE_URL "
                "(bold-line normalization runs on jvforge only)."
            )
        if effective_forge:
            summary_for_forge = await _if_add_node_summary_for_jvforge(
                cfg.agent_id, cfg.node_summary
            )
            llm_wh_url = await cfg.page_index_action.get_webhook_url()
            async_mode = (
                os.environ.get("JVAGENT_JVFORGE_ASYNC", "false").lower() == "true"
            )
            if async_mode:
                q = await assimilate_via_jvforge_async(
                    base_url=effective_forge,
                    agent_id=cfg.agent_id,
                    filename=doc_name,
                    content=file_bytes,
                    doc_name=doc_name,
                    model=cfg.model,
                    if_add_node_summary=summary_for_forge,
                    collection_name=cfg.collection_name,
                    metadata=cfg.metadata or None,
                    doc_description=None,
                    doc_url=doc_url or None,
                    convert_to_markdown=cfg.convert_to_markdown,
                    ocr=cfg.ocr,
                    docling_ocr_engine=cfg.docling_ocr_engine,
                    normalize_bold_headings=cfg.normalize_bold_headings,
                    llm_webhook_url=llm_wh_url,
                    emergency=False,
                )
                return {
                    "doc_name": doc_name,
                    "jvforge_job_id": q.get("job_id"),
                    "jvforge_queue_status": q.get("status"),
                }
            await assimilate_via_jvforge(
                base_url=effective_forge,
                agent_id=cfg.agent_id,
                filename=doc_name,
                content=file_bytes,
                doc_name=doc_name,
                model=cfg.model,
                if_add_node_summary=summary_for_forge,
                collection_name=cfg.collection_name,
                metadata=cfg.metadata or None,
                doc_description=None,
                doc_url=doc_url or None,
                convert_to_markdown=cfg.convert_to_markdown,
                ocr=cfg.ocr,
                docling_ocr_engine=cfg.docling_ocr_engine,
                normalize_bold_headings=cfg.normalize_bold_headings,
                llm_webhook_url=llm_wh_url,
            )
            return {"doc_name": doc_name}
        # assimilate_document expects threading.Event (worker-thread cancel); not asyncio.Event.
        await assimilate_document(
            file_bytes,
            doc_name=doc_name,
            model=cfg.model,
            model_action=cfg.model_action,
            if_add_node_summary=cfg.node_summary,
            persist=True,
            collection_name=cfg.collection_name,
            doc_url=doc_url,
            metadata=cfg.metadata,
            cancel_event=cancel_event,
            convert_to_markdown=cfg.convert_to_markdown,
            ocr=cfg.ocr,
            docling_ocr_engine=cfg.docling_ocr_engine,
        )
        return {"doc_name": doc_name}

    async def _process_single_document(
        self,
        google_drive_documents_node: Any,
        google_drive_action: Any,
        file_info: Dict[str, Any],
        doc_type: str,
        cfg: DriveIngestConfig,
        old_file: Optional[Dict[str, Any]] = None,
        source: str = "ingesting_documents",
    ) -> Dict[str, Any]:
        """Process one document (added or modified). Sets active_document, ingests, clears on completion.

        source: 'ingesting_documents' or 'failed_documents' (for retries).
        Returns dict with success (bool), doc_name (str), and ingestion_message (str).
        Always clears active_document on exit (success or exception).
        """
        doc_name = file_info.get("name", "")
        file_id = file_info.get("id", "")
        doc_url = file_info.get("url", "")

        google_drive_documents_node.active_document = doc_name
        google_drive_documents_node.status = "processing"
        await google_drive_documents_node.save()

        timeout_seconds = self.document_timeout or 60

        try:
            cancel_event = threading.Event()

            if not is_drive_file_pageindex_ingestible(
                doc_name, str(file_info.get("mimeType") or "")
            ):
                logger.info(
                    "Skipping unsupported Google Drive file for PageIndex: %s",
                    doc_name,
                )
                return await _pop_skip_head(
                    google_drive_documents_node,
                    source=source,
                    doc_type=doc_type,
                    doc_name=doc_name,
                    ingestion_message=f"Skipped unsupported file type: {doc_name}",
                )

            if (
                cfg.skip_existing_documents
                and doc_type == "added"
                and doc_name
                and await _drive_added_fname_matches_indexed(
                    doc_name, cfg.collection_name
                )
            ):
                return await _pop_skip_head(
                    google_drive_documents_node,
                    source=source,
                    doc_type=doc_type,
                    doc_name=doc_name,
                    ingestion_message=f"Skipped (already in index): {doc_name}",
                )

            try:
                result = await asyncio.wait_for(
                    self._execute_drive_document_ingest(
                        google_drive_action=google_drive_action,
                        doc_name=doc_name,
                        file_id=file_id,
                        doc_url=doc_url,
                        cfg=cfg,
                        cancel_event=cancel_event,
                    ),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError:
                cancel_event.set()
                await self._mark_drive_ingest_failed(
                    google_drive_documents_node,
                    source=source,
                    doc_type=doc_type,
                    file_info=file_info,
                )
                logger.warning(
                    "Document %s timed out after %ss", doc_name, timeout_seconds
                )
                return {
                    "success": False,
                    "skipped": False,
                    "doc_name": doc_name,
                    "ingestion_message": f"Timed out ingesting {doc_name}",
                }
            except Exception:
                await self._mark_drive_ingest_failed(
                    google_drive_documents_node,
                    source=source,
                    doc_type=doc_type,
                    file_info=file_info,
                )
                logger.exception("Error ingesting document %s", doc_name)
                return {
                    "success": False,
                    "skipped": False,
                    "doc_name": doc_name,
                    "ingestion_message": (
                        f"Failed to ingest {doc_name}"
                        if doc_type == "added"
                        else f"Failed to update {doc_name}"
                    ),
                }

            if not result.get("doc_name"):
                await self._mark_drive_ingest_failed(
                    google_drive_documents_node,
                    source=source,
                    doc_type=doc_type,
                    file_info=file_info,
                )
                logger.error("Ingestion returned no doc_name for %s", doc_name)
                return {
                    "success": False,
                    "skipped": False,
                    "doc_name": doc_name,
                    "ingestion_message": (
                        f"Failed to ingest {doc_name}"
                        if doc_type == "added"
                        else f"Failed to update {doc_name}"
                    ),
                }

            try:
                docs = getattr(google_drive_documents_node, source)
                if doc_type == "added":
                    docs["added"].pop(0)
                else:
                    docs["modified"].pop(0)
                    if old_file:
                        await delete_document(
                            old_file.get("name", ""),
                            collection_name=cfg.collection_name,
                        )
                google_drive_documents_node.active_document = ""
                _sync_drive_node_status_from_queues(google_drive_documents_node)
                await google_drive_documents_node.save()
            except Exception:
                logger.exception(
                    "post-ingest sync state failed for %s (document may be indexed)",
                    doc_name,
                )
                msg = (
                    f"Added {doc_name}"
                    if doc_type == "added"
                    else f"Updated {doc_name}"
                )
                jid = result.get("jvforge_job_id")
                if jid:
                    msg = f"{msg} (jvforge job {jid})"
                msg = f"{msg} (warning: sync state not updated)"
                return {
                    "success": True,
                    "skipped": False,
                    "doc_name": doc_name,
                    "ingestion_message": msg,
                }

            msg = f"Added {doc_name}" if doc_type == "added" else f"Updated {doc_name}"
            jid = result.get("jvforge_job_id")
            if jid:
                msg = f"{msg} (jvforge job {jid})"

            return {
                "success": True,
                "skipped": False,
                "doc_name": doc_name,
                "ingestion_message": msg,
            }
        finally:
            # Ensure active_document is always cleared
            if google_drive_documents_node.active_document:
                google_drive_documents_node.active_document = ""
                await google_drive_documents_node.save()

    async def ingest_documents_from_google_drive(
        self,
        google_drive_folders: Optional[List[dict]] = None,
        remove_deleted_documents: bool = False,
        retry_failed_documents: bool = False,
        convert_to_markdown: bool = False,
        ocr: bool = False,
        docling_ocr_engine: Optional[str] = None,
        normalize_bold_headings: bool = False,
        skip_existing_documents: bool = True,
        use_jvforge: Optional[bool] = None,
    ) -> dict:
        """Recursively extract and ingest PDF documents from Google Drive folders.

        Processes one document per invocation. If active_document is set, returns immediately.
        Uses agent-level lock to prevent concurrent ingestion from multiple webhook calls.

        Args:
            google_drive_folders: List of folder configs, e.g.
                [{"folder_id": "<folder_id>", "metadata": {"key": "value"}}]

        Returns:
            Dict with status and ``documents_ingested``:
            ``added``, ``updated``, ``removed`` (deleted from index when
            ``remove_deleted_documents``), ``to_be_removed`` (names cleared from
            queue when auto-delete is off).
        """
        empty_result: Dict[str, Any] = {
            "status": "error",
            "message": "",
            "documents_ingested": {
                "added": [],
                "updated": [],
                "removed": [],
                "to_be_removed": [],
            },
        }
        google_drive_action: Optional[Any] = await self.get_action("GoogleDriveAction")
        page_index_action: Optional[Any] = await self.get_action(self.page_index_action)
        if not google_drive_action:
            logger.warning(
                "No GoogleDriveAction found! Unable to retrieve documents from Google Drive"
            )
            empty_result["message"] = "No GoogleDriveAction found"
            return empty_result
        if not page_index_action:
            logger.warning(
                "No %s found! Unable to ingest documents from Google Drive",
                self.page_index_action,
            )
            empty_result["message"] = f"No {self.page_index_action} found"
            return empty_result

        if not google_drive_folders:
            google_drive_folders = self.google_drive_folders
        if not google_drive_folders:
            empty_result["message"] = "No folders to ingest"
            return empty_result

        agent = await self.get_agent()
        agent_id = str(agent.id)
        ingestion_lock = await _get_ingestion_lock(agent_id)
        async with ingestion_lock:
            return await self._ingest_documents_from_google_drive_inner(
                agent_id=agent_id,
                google_drive_folders=google_drive_folders,
                remove_deleted_documents=remove_deleted_documents,
                retry_failed_documents=retry_failed_documents,
                google_drive_action=google_drive_action,
                page_index_action=page_index_action,
                convert_to_markdown=convert_to_markdown,
                ocr=ocr,
                docling_ocr_engine=docling_ocr_engine,
                normalize_bold_headings=normalize_bold_headings,
                skip_existing_documents=skip_existing_documents,
                use_jvforge=use_jvforge,
            )

    async def _phase_sync_google_drive_folders(
        self,
        google_drive_folders: list,
        google_drive_action: Any,
        collection_name: str,
        skip_existing_documents: bool,
    ) -> None:
        """Phase A: list Drive trees, merge queues, persist ``GoogleDriveDocuments`` nodes."""
        for google_drive_folder in google_drive_folders:
            google_drive_folder_id = google_drive_folder.get("folder_id")

            try:
                root_meta = await google_drive_action.get_file_metadata(
                    google_drive_folder_id, fields="id, name"
                )
                folder_name = str(root_meta.get("name") or "")
            except Exception:
                logger.warning(
                    "Could not fetch Drive folder name for folder_id=%s",
                    google_drive_folder_id,
                    exc_info=True,
                )
                folder_name = ""

            files = await google_drive_action.list_files(
                with_link=True, folder_id=google_drive_folder_id
            )
            metadata = google_drive_folder.get("metadata", {})

            lock = await _get_folder_lock(str(self.id), google_drive_folder_id)
            async with lock:
                google_drive_documents_node = await self.node(
                    node="GoogleDriveDocuments", folder_id=google_drive_folder_id
                )

                if google_drive_documents_node:
                    old_files = google_drive_documents_node.files
                    _merge_disable_ingestion_from_old(old_files, files)
                    ingesting_documents = google_drive_action.compare_files(
                        old_files=old_files, new_files=files
                    )
                    filter_drive_doc_queues_for_ingestible(ingesting_documents)
                    google_drive_documents_node.files = files
                    google_drive_documents_node.folder_name = folder_name
                    google_drive_documents_node.metadata = metadata

                    for key in google_drive_documents_node.ingesting_documents:
                        if key in ingesting_documents:
                            existing_items = {
                                _queue_item_file_id(item, key): idx
                                for idx, item in enumerate(
                                    google_drive_documents_node.ingesting_documents[key]
                                )
                                if _queue_item_file_id(item, key)
                            }
                            for item in ingesting_documents[key]:
                                fid = _queue_item_file_id(item, key)
                                if not fid:
                                    google_drive_documents_node.ingesting_documents[
                                        key
                                    ].append(item)
                                elif fid in existing_items:
                                    google_drive_documents_node.ingesting_documents[
                                        key
                                    ][existing_items[fid]] = item
                                else:
                                    google_drive_documents_node.ingesting_documents[
                                        key
                                    ].append(item)

                    filter_drive_doc_queues_for_ingestible(
                        google_drive_documents_node.ingesting_documents
                    )
                    filter_drive_doc_queues_for_ingestible(
                        google_drive_documents_node.failed_documents
                    )

                    disabled = _disabled_file_ids(google_drive_documents_node.files)
                    _filter_doc_queues_for_disabled(
                        google_drive_documents_node.ingesting_documents, disabled
                    )
                    _filter_doc_queues_for_disabled(
                        google_drive_documents_node.failed_documents, disabled
                    )
                    await _prune_added_queue_skip_existing(
                        google_drive_documents_node,
                        collection_name,
                        skip_existing_documents=skip_existing_documents,
                    )
                    _sync_drive_node_status_from_queues(google_drive_documents_node)
                    await google_drive_documents_node.save()
                else:
                    _merge_disable_ingestion_from_old([], files)
                    ingesting_documents = google_drive_action.compare_files(
                        old_files=[], new_files=files
                    )
                    filter_drive_doc_queues_for_ingestible(ingesting_documents)
                    disabled = _disabled_file_ids(files)
                    _filter_doc_queues_for_disabled(ingesting_documents, disabled)
                    google_drive_documents_node = await GoogleDriveDocuments.create(
                        folder_id=google_drive_folder_id,
                        folder_name=folder_name,
                        files=files,
                        metadata=metadata,
                        ingesting_documents=ingesting_documents,
                    )
                    await _prune_added_queue_skip_existing(
                        google_drive_documents_node,
                        collection_name,
                        skip_existing_documents=skip_existing_documents,
                    )
                    await self.connect(google_drive_documents_node)

    async def _check_active_google_drive_document(
        self,
        google_drive_folders: list,
        document_ingested: Dict[str, List[str]],
    ) -> Optional[dict]:
        """Phase B: if any folder has ``active_document``, return early response."""
        for google_drive_folder in google_drive_folders:
            google_drive_folder_id = google_drive_folder.get("folder_id")
            google_drive_documents_node = await self.node(
                node="GoogleDriveDocuments", folder_id=google_drive_folder_id
            )
            if (
                google_drive_documents_node
                and google_drive_documents_node.active_document
            ):
                return {
                    "status": "completed",
                    "message": (
                        f"Document {google_drive_documents_node.active_document} "
                        "is currently being processed. Please try again later."
                    ),
                    "documents_ingested": document_ingested,
                }
        return None

    async def _phase_pick_and_process_google_drive_document(
        self,
        google_drive_folders: list,
        remove_deleted_documents: bool,
        retry_failed_documents: bool,
        google_drive_action: Any,
        cfg_template: DriveIngestConfig,
        document_ingested: Dict[str, List[str]],
    ) -> dict:
        """Phase C: pick one queued document (or removals batch) and process."""
        collection_name = cfg_template.collection_name
        for google_drive_folder in google_drive_folders:
            google_drive_folder_id = google_drive_folder.get("folder_id")
            google_drive_documents_node = await self.node(
                node="GoogleDriveDocuments", folder_id=google_drive_folder_id
            )

            if not google_drive_documents_node:
                continue

            ing = google_drive_documents_node.ingesting_documents
            fd = google_drive_documents_node.failed_documents
            has_ingest = bool(ing["added"] or ing["modified"] or ing["removed"])
            has_failed = bool(fd["added"] or fd["modified"] or fd["removed"])
            if has_ingest:
                ingesting_documents = ing
                ingest_source = "ingesting_documents"
            elif retry_failed_documents and has_failed:
                ingesting_documents = fd
                ingest_source = "failed_documents"
            else:
                continue

            await _pop_disabled_head_queues(
                google_drive_documents_node, ingesting_documents
            )

            metadata = google_drive_folder.get("metadata", {})
            cfg = replace(cfg_template, metadata=dict(metadata or {}))

            if ingesting_documents["added"]:
                new_file = ingesting_documents["added"][0]
                result = await self._process_single_document(
                    google_drive_documents_node=google_drive_documents_node,
                    google_drive_action=google_drive_action,
                    file_info=new_file,
                    doc_type="added",
                    cfg=cfg,
                    source=ingest_source,
                )
                if result["success"] and not result.get("skipped"):
                    document_ingested["added"].append(result["doc_name"])
                return {
                    "status": "completed",
                    "message": result["ingestion_message"],
                    "documents_ingested": document_ingested,
                }

            if ingesting_documents["modified"]:
                modified_result = ingesting_documents["modified"][0]
                if isinstance(modified_result, dict) and "new" in modified_result:
                    new_file = modified_result["new"]
                    old_file = modified_result["old"]
                else:
                    new_file = modified_result
                    old_file = None
                result = await self._process_single_document(
                    google_drive_documents_node=google_drive_documents_node,
                    google_drive_action=google_drive_action,
                    file_info=new_file,
                    doc_type="modified",
                    cfg=cfg,
                    old_file=old_file,
                    source=ingest_source,
                )
                if result["success"] and not result.get("skipped"):
                    document_ingested["updated"].append(result["doc_name"])
                return {
                    "status": "completed",
                    "message": result["ingestion_message"],
                    "documents_ingested": document_ingested,
                }

            if ingesting_documents["removed"]:
                if remove_deleted_documents:
                    remove_docs_names = []
                    for removed_doc in list(ingesting_documents["removed"]):
                        try:
                            await delete_document(
                                removed_doc.get("name", ""),
                                collection_name=collection_name,
                            )
                            document_ingested["removed"].append(
                                removed_doc.get("name", "")
                            )
                            remove_docs_names.append(removed_doc.get("name", ""))
                            ingesting_documents["removed"].remove(removed_doc)
                        except Exception as e:
                            google_drive_documents_node.failed_documents[
                                "removed"
                            ].append(removed_doc)
                            ingesting_documents["removed"].remove(removed_doc)
                            _sync_drive_node_status_from_queues(
                                google_drive_documents_node
                            )
                            await google_drive_documents_node.save()
                            logger.error(
                                "Error deleting document: %s", e, exc_info=True
                            )
                            return {
                                "status": "completed",
                                "message": f"Failed to delete {removed_doc.get('name', '')}",
                                "documents_ingested": document_ingested,
                            }
                else:
                    document_ingested["to_be_removed"].extend(
                        [r.get("name", "") for r in ingesting_documents["removed"]]
                    )
                    ingesting_documents["removed"] = []
                    remove_docs_names = []

                if ingest_source == "ingesting_documents":
                    google_drive_documents_node.ingesting_documents = (
                        ingesting_documents
                    )
                _sync_drive_node_status_from_queues(google_drive_documents_node)
                await google_drive_documents_node.save()
                ingestion_message = (
                    f"Deleted {', '.join(remove_docs_names)}"
                    if remove_docs_names
                    else "Removed documents processed"
                )
                return {
                    "status": "completed",
                    "message": ingestion_message,
                    "documents_ingested": document_ingested,
                }

            if (
                not ingesting_documents["added"]
                and not ingesting_documents["modified"]
                and not ingesting_documents["removed"]
            ):
                _sync_drive_node_status_from_queues(google_drive_documents_node)
                await google_drive_documents_node.save()
                break

        logger.warning("No pending documents to ingest")
        return {
            "status": "completed",
            "message": "No pending documents to ingest",
            "documents_ingested": document_ingested,
        }

    async def _ingest_documents_from_google_drive_inner(
        self,
        agent_id: str,
        google_drive_folders: list,
        remove_deleted_documents: bool,
        retry_failed_documents: bool,
        google_drive_action: Any,
        page_index_action: Any,
        convert_to_markdown: bool = False,
        ocr: bool = False,
        docling_ocr_engine: Optional[str] = None,
        normalize_bold_headings: bool = False,
        skip_existing_documents: bool = True,
        use_jvforge: Optional[bool] = None,
    ) -> dict:
        """Inner ingestion logic (called with ingestion lock held)."""
        ocr_eff, docling_eff = _drive_resolve_docling_ocr(docling_ocr_engine, ocr)
        initialize_pageindex_database(app_id=await _get_app_id_from_node())
        logger.info(
            "PageIndex Google Drive Sync: starting ingestion for %d folder(s)",
            len(google_drive_folders),
        )
        document_ingested: Dict[str, List[str]] = {
            "added": [],
            "updated": [],
            "removed": [],
            "to_be_removed": [],
        }

        collection_name = page_index_action.resolve_collection()
        model_action = await page_index_action.get_model_action()
        node_summary = page_index_action.config.get("node_summary")
        model = page_index_action.config.get("model")

        cfg_template = DriveIngestConfig(
            collection_name=collection_name,
            metadata={},
            model=model,
            model_action=model_action,
            node_summary=node_summary,
            agent_id=agent_id,
            page_index_action=page_index_action,
            convert_to_markdown=convert_to_markdown,
            ocr=ocr_eff,
            docling_ocr_engine=docling_eff,
            normalize_bold_headings=normalize_bold_headings,
            skip_existing_documents=skip_existing_documents,
            use_jvforge=use_jvforge,
        )

        await self._phase_sync_google_drive_folders(
            google_drive_folders,
            google_drive_action,
            collection_name,
            skip_existing_documents,
        )

        busy = await self._check_active_google_drive_document(
            google_drive_folders, document_ingested
        )
        if busy is not None:
            return busy

        return await self._phase_pick_and_process_google_drive_document(
            google_drive_folders=google_drive_folders,
            remove_deleted_documents=remove_deleted_documents,
            retry_failed_documents=retry_failed_documents,
            google_drive_action=google_drive_action,
            cfg_template=cfg_template,
            document_ingested=document_ingested,
        )

    async def update_google_drive_documents(
        self,
        folder_id: str,
        *,
        folder_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        status: Optional[str] = None,
        ingesting_documents: Optional[Dict[str, Any]] = None,
        failed_documents: Optional[Dict[str, Any]] = None,
        active_document: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update a connected ``GoogleDriveDocuments`` node (lookup by Drive ``folder_id``)."""
        node = await self.node(node="GoogleDriveDocuments", folder_id=folder_id)
        if not node:
            raise ValidationError(
                message=f"No GoogleDriveDocuments node for folder_id={folder_id}",
                details={"folder_id": folder_id},
            )

        if folder_name is not None:
            node.folder_name = folder_name
        if metadata is not None:
            base = dict(node.metadata) if node.metadata else {}
            base.update(metadata)
            node.metadata = base

        queues_touched = False
        if ingesting_documents is not None:
            node.ingesting_documents = _validate_doc_queues_payload(
                ingesting_documents, label="ingesting_documents"
            )
            queues_touched = True
        if failed_documents is not None:
            node.failed_documents = _validate_doc_queues_payload(
                failed_documents, label="failed_documents"
            )
            queues_touched = True
        if active_document is not None:
            node.active_document = active_document

        if status is not None:
            s = str(status).strip()
            if s not in _GOOGLE_DRIVE_DOCUMENTS_STATUS_ALLOW:
                raise ValidationError(
                    message="Invalid status for GoogleDriveDocuments node",
                    details={
                        "status": s,
                        "allowed": sorted(_GOOGLE_DRIVE_DOCUMENTS_STATUS_ALLOW),
                    },
                )
            node.status = s
        elif queues_touched:
            _sync_drive_node_status_from_queues(node)

        await node.save()

        ing = node.ingesting_documents
        fd = node.failed_documents
        return {
            "folder_id": node.folder_id,
            "folder_name": node.folder_name or "",
            "metadata": dict(node.metadata) if node.metadata else {},
            "status": node.status,
            "active_document": node.active_document or "",
            "ingesting_documents": {
                "added": list(ing.get("added") or []),
                "modified": list(ing.get("modified") or []),
                "removed": list(ing.get("removed") or []),
            },
            "failed_documents": {
                "added": list(fd.get("added") or []),
                "modified": list(fd.get("modified") or []),
                "removed": list(fd.get("removed") or []),
            },
        }

    async def delete_google_drive_documents(
        self, document_id: Optional[str] = None
    ) -> List[str]:
        """Delete Google Drive folder sync nodes.

        When ``document_id`` is set, it is the **Google Drive folder id**
        (``GoogleDriveDocuments.folder_id``), not the graph node id.
        When omitted, all folder nodes for this action are deleted.
        """
        deleted: List[str] = []
        google_drive_documents_nodes = await self.nodes(node=GoogleDriveDocuments)
        for node in google_drive_documents_nodes:
            if document_id:
                if str(node.folder_id) == str(document_id):
                    await node.delete()
                    deleted.append(str(node.id))
            else:
                await node.delete()
                deleted.append(str(node.id))
        return deleted

    async def prioritize_google_drive_file_for_ingest(
        self, folder_id: str, file_id: str
    ) -> Dict[str, Any]:
        """Move a file to the front of its ingest or failed bucket, or enqueue it for re-ingest."""
        google_drive_documents_node = await self.node(
            node="GoogleDriveDocuments", folder_id=folder_id
        )
        if not google_drive_documents_node:
            raise ValidationError(
                message=f"No GoogleDriveDocuments node for folder_id={folder_id}",
                details={"folder_id": folder_id},
            )

        tree_file = _find_file_dict_in_tree(
            list(google_drive_documents_node.files or []), file_id
        )
        if tree_file and tree_file.get("disable_ingestion"):
            raise ValidationError(
                message="Cannot prioritize a file with Skip ingest enabled",
                details={"folder_id": folder_id, "file_id": file_id},
            )

        prioritized_in: Optional[str] = None
        if _extract_and_prepend_queue_item(
            google_drive_documents_node.ingesting_documents, file_id
        ):
            prioritized_in = "ingesting"
        elif _extract_and_prepend_queue_item(
            google_drive_documents_node.failed_documents, file_id
        ):
            prioritized_in = "failed"
        else:
            if not tree_file:
                raise ValidationError(
                    message=f"File id not found under folder: {file_id}",
                    details={"folder_id": folder_id, "file_id": file_id},
                )
            if not is_drive_file_pageindex_ingestible(
                str(tree_file.get("name") or ""),
                str(tree_file.get("mimeType") or ""),
            ):
                raise ValidationError(
                    message="File type is not supported for PageIndex ingestion",
                    details={"folder_id": folder_id, "file_id": file_id},
                )
            mod = list(
                google_drive_documents_node.ingesting_documents.get("modified") or []
            )
            google_drive_documents_node.ingesting_documents["modified"] = [
                {"new": copy.deepcopy(tree_file), "old": None},
            ] + mod
            prioritized_in = "enqueued"

        filter_drive_doc_queues_for_ingestible(
            google_drive_documents_node.ingesting_documents
        )
        filter_drive_doc_queues_for_ingestible(
            google_drive_documents_node.failed_documents
        )
        disabled = _disabled_file_ids(google_drive_documents_node.files)
        _filter_doc_queues_for_disabled(
            google_drive_documents_node.ingesting_documents, disabled
        )
        _filter_doc_queues_for_disabled(
            google_drive_documents_node.failed_documents, disabled
        )
        _sync_drive_node_status_from_queues(google_drive_documents_node)
        await google_drive_documents_node.save()
        return {
            "folder_id": folder_id,
            "file_id": str(file_id),
            "prioritized_in": prioritized_in,
        }

    async def clear_google_drive_file_from_queues(
        self, folder_id: str, file_id: str
    ) -> Dict[str, Any]:
        """Remove a file from ingesting and failed queues (does not change PageIndex index)."""
        google_drive_documents_node = await self.node(
            node="GoogleDriveDocuments", folder_id=folder_id
        )
        if not google_drive_documents_node:
            raise ValidationError(
                message=f"No GoogleDriveDocuments node for folder_id={folder_id}",
                details={"folder_id": folder_id},
            )

        _strip_file_id_from_doc_queues(
            google_drive_documents_node.ingesting_documents, file_id
        )
        _strip_file_id_from_doc_queues(
            google_drive_documents_node.failed_documents, file_id
        )
        _sync_drive_node_status_from_queues(google_drive_documents_node)
        await google_drive_documents_node.save()
        return {
            "folder_id": folder_id,
            "file_id": str(file_id),
            "cleared": True,
        }

    async def set_google_drive_file_ingestion(
        self,
        folder_id: str,
        file_id: str,
        disable_ingestion: bool,
    ) -> Dict[str, Any]:
        """Set ``disable_ingestion`` on a file in ``files`` and drop it from queues when disabling."""
        google_drive_documents_node = await self.node(
            node="GoogleDriveDocuments", folder_id=folder_id
        )
        if not google_drive_documents_node:
            raise ValidationError(
                message=f"No GoogleDriveDocuments node for folder_id={folder_id}",
                details={"folder_id": folder_id},
            )

        found = False

        def walk(items: List[Dict[str, Any]]) -> None:
            nonlocal found
            for it in items:
                if str(it.get("id")) == str(file_id):
                    it["disable_ingestion"] = bool(disable_ingestion)
                    found = True
                nested = it.get("files")
                if nested:
                    walk(nested)

        walk(google_drive_documents_node.files)
        if not found:
            raise ValidationError(
                message=f"File id not found under folder: {file_id}",
                details={"folder_id": folder_id, "file_id": file_id},
            )

        if disable_ingestion:
            for q in (
                google_drive_documents_node.ingesting_documents,
                google_drive_documents_node.failed_documents,
            ):
                for key in ("added", "modified", "removed"):
                    q[key] = [
                        x
                        for x in (q.get(key) or [])
                        if _queue_item_file_id(x, key) != str(file_id)
                    ]

        _sync_drive_node_status_from_queues(google_drive_documents_node)
        await google_drive_documents_node.save()
        return {
            "folder_id": folder_id,
            "file_id": file_id,
            "disable_ingestion": bool(disable_ingestion),
        }

    async def get_google_drive_documents(self) -> List[Dict[str, Any]]:
        """Return all connected ``GoogleDriveDocuments`` folder sync nodes for this action."""
        google_drive_documents_nodes = await self.nodes(node=GoogleDriveDocuments)
        google_drive_documents = []
        for google_drive_documents_node in google_drive_documents_nodes:
            fid = google_drive_documents_node.folder_id
            google_drive_documents.append(
                {
                    "node_id": str(google_drive_documents_node.id),
                    "document_id": fid,
                    "folder_id": fid,
                    "folder_name": google_drive_documents_node.folder_name or "",
                    "ingesting_documents": google_drive_documents_node.ingesting_documents,
                    "status": google_drive_documents_node.status,
                    "active_document": google_drive_documents_node.active_document,
                    "metadata": google_drive_documents_node.metadata,
                    "files": google_drive_documents_node.files,
                    "failed_documents": google_drive_documents_node.failed_documents,
                }
            )
        return google_drive_documents

    # action configuration

    async def _apply_env_defaults(self) -> None:
        """Apply environment variable defaults for missing configuration.

        Sets the following from environment variables if not already configured:
        - base_url from JVAGENT_PUBLIC_BASE_URL

        This allows users to set these values once in their .env file
        instead of configuring them per-action in agent.yaml.
        """
        # Application Base URL
        if not self.base_url or not self.base_url.strip():
            env_base_url = get_public_base_url()
            if env_base_url:
                self.base_url = env_base_url
                await self.save()

    def is_configured(self) -> bool:
        """Return True if this action has a valid ``base_url`` for webhook generation."""
        # Check for required fields - must be non-empty strings
        if not self.base_url:
            return False

        # Validate URL formats
        if not self.base_url.startswith(("http://", "https://")):
            return False

        return True

    async def get_webhook_url(
        self, allowed_ip: Optional[str] = None, regenerate: bool = False
    ) -> str:
        """Generate or retrieve secure webhook URL with API key authentication."""
        if not self.base_url or not self.base_url.strip():
            raise ValidationError(
                "base_url (JVAGENT_PUBLIC_BASE_URL) is required for webhook URL generation"
            )
        if not self.base_url.startswith(("http://", "https://")):
            raise ValidationError(
                f"base_url must be a valid HTTP/HTTPS URL, got: {self.base_url}"
            )

        try:
            agent = await self.get_agent()
            agent_id = str(agent.id)
            expected_url_base = f"{self.base_url}/api/page_index_google_drive_sync/interact/webhook/{agent_id}"

            prime_ctx = GraphContext(database=get_prime_database())
            api_key_service = APIKeyService(context=prime_ctx)

            if (
                not regenerate
                and self.webhook_url
                and "?api_key=" in self.webhook_url
                and self.webhook_url.startswith(expected_url_base)
            ):
                # When allowed_ip is specified, verify existing key's IPs match
                if allowed_ip is not None and self.webhook_api_key_id:
                    try:
                        existing_key = await api_key_service.get_key(
                            self.webhook_api_key_id
                        )
                        if existing_key and existing_key.is_active:
                            requested_ips = [allowed_ip] if allowed_ip else []
                            existing_ips = (
                                getattr(existing_key, "allowed_ips", None) or []
                            )
                            if requested_ips == existing_ips:
                                return self.webhook_url
                        # IP mismatch or key invalid - fall through to regenerate
                    except Exception:
                        pass  # Fall through to regenerate on error
                else:
                    return self.webhook_url

            system_user_id = await get_or_create_system_user()

            if regenerate and self.webhook_api_key_id:
                try:
                    await api_key_service.revoke_key(
                        self.webhook_api_key_id, system_user_id
                    )
                except Exception:
                    pass

            plaintext_key, api_key = await api_key_service.generate_key(
                user_id=system_user_id,
                name=f"PageIndex Google Drive Sync Webhook - {agent.name}",
                permissions=["webhook:pageindex_google_drive_sync_action"],
                expires_in_days=None,
                allowed_ips=[allowed_ip] if allowed_ip else [],
                allowed_endpoints=[
                    "/api/page_index_google_drive_sync/interact/webhook/*"
                ],
                key_prefix="jv_",
            )

            self.webhook_api_key_id = api_key.id
            self.webhook_url = f"{expected_url_base}?api_key={plaintext_key}"
            await self.save()
            return self.webhook_url

        except DatabaseError:
            raise
        except Exception as e:
            raise ValidationError("Webhook URL generation failed: %s" % (e,))

    async def _ensure_webhook_url_if_configured(
        self, *, require_enabled: bool, skip_log_context: Optional[str]
    ) -> None:
        await self._apply_env_defaults()
        if not self.is_configured():
            if skip_log_context:
                logger.debug(
                    "Page Index Google Drive Sync action not configured, skipping %s",
                    skip_log_context,
                )
            return
        if require_enabled and not self.enabled:
            return
        try:
            if not self.webhook_url:
                await self.get_webhook_url()
        except Exception as e:
            logger.error(
                "Error ensuring Page Index Google Drive Sync webhook URL: %s",
                e,
                exc_info=True,
            )

    async def on_register(self) -> None:
        """Called when action is registered. Validates configuration."""
        await self._apply_env_defaults()
        if not self.is_configured():
            logger.debug("Page Index Google Drive Sync action not configured")
            return
        logger.debug("Page Index Google Drive Sync action registered")

    async def on_reload(self) -> None:
        """Called when action is reloaded. Re-registers session with current webhook URL."""
        await self._ensure_webhook_url_if_configured(
            require_enabled=False, skip_log_context="reload"
        )

    async def on_startup(self) -> None:
        """Initialize webhook URL when the action is enabled at app startup."""
        await self._ensure_webhook_url_if_configured(
            require_enabled=True, skip_log_context=None
        )

    async def on_deregister(self) -> None:
        await super().on_deregister()
        aid = str(self.id)
        prefix = f"{aid}:"
        async with _sync_locks_guard:
            for k in list(_sync_locks.keys()):
                if k.startswith(prefix):
                    del _sync_locks[k]
        agid = self.agent_id
        if agid:
            async with _ingestion_locks_guard:
                _ingestion_locks.pop(agid, None)


from . import endpoints  # noqa: F401  # register HTTP routes when action module loads
