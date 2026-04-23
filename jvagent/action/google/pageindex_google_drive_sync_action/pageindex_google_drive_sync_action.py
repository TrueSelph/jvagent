import asyncio
import logging
import os
import threading
from typing import Any, Dict, List, Optional, Set

from jvspatial.api.auth.api_key_service import APIKeyService
from jvspatial.core.annotations import attribute
from jvspatial.core.context import GraphContext
from jvspatial.db import get_prime_database
from jvspatial.exceptions import DatabaseError, ValidationError

from jvagent.action.pageindex.config import (
    get_pageindex_node_summary,
    initialize_pageindex_database,
)
from jvagent.action.pageindex.documents import (
    _get_app_id_from_node,
    assimilate_document,
    delete_document,
)
from jvagent.action.pageindex.jvforge_assimilate import (
    assimilate_via_jvforge,
    assimilate_via_jvforge_async,
)
from jvagent.action.pageindex.pageindex_retrieval_interact_action import (
    ensure_ingestion_config_for_agent,
)
from jvagent.core.public_url import get_public_base_url
from jvagent.env import get_jvagent_jvforge_base_url

from ..google_action import GoogleAction
from .drive_ingest_filter import (
    filter_drive_doc_queues_for_ingestible,
    is_drive_file_pageindex_ingestible,
)
from .google_drive_documents import GoogleDriveDocuments
from .webhook_auth import get_or_create_system_user

logger = logging.getLogger(__name__)

_FOLDER_MIME = "application/vnd.google-apps.folder"


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


def _filter_queue_for_disabled(
    items: List[Any], disabled: Set[str], queue_key: str
) -> List[Any]:
    return [x for x in items if _queue_item_file_id(x, queue_key) not in disabled]


def _filter_doc_queues_for_disabled(docs: Dict[str, Any], disabled: Set[str]) -> None:
    for key in ("added", "modified", "removed"):
        docs[key] = _filter_queue_for_disabled(list(docs.get(key) or []), disabled, key)


def _recompute_google_drive_node_idle_status(node: Any) -> None:
    """If no work remains in ingesting or failed queues, mark folder sync completed."""
    ing = node.ingesting_documents
    fd = node.failed_documents
    pending_ingest = bool(ing["added"] or ing["modified"] or ing["removed"])
    pending_fail = bool(fd["added"] or fd["modified"] or fd["removed"])
    if not pending_ingest and not pending_fail:
        node.status = "completed"


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
        _recompute_google_drive_node_idle_status(node)
        await node.save()
    return changed


async def _pop_skip_unsupported_head(
    node: Any,
    *,
    source: str,
    doc_type: str,
    doc_name: str,
) -> Dict[str, Any]:
    """Remove head item from ingest queue without marking failed; used for unsupported types."""
    docs = getattr(node, source)
    if doc_type == "added":
        if docs["added"]:
            docs["added"].pop(0)
    elif doc_type == "modified":
        if docs["modified"]:
            docs["modified"].pop(0)
    node.active_document = ""
    ingesting = node.ingesting_documents
    node.status = (
        "completed"
        if (
            not ingesting["added"]
            and not ingesting["modified"]
            and not ingesting["removed"]
        )
        else "pending"
    )
    await node.save()
    return {
        "success": True,
        "skipped": True,
        "doc_name": doc_name,
        "ingestion_message": f"Skipped unsupported file type: {doc_name}",
    }


class PageIndexGoogleDriveSyncAction(GoogleAction):
    """Sync Google Drive folders into PageIndex using OAuth2 (inherits GoogleAction)."""

    google_drive_folders: List[dict] = attribute(
        default_factory=list,
        description="List of Google Drive folder configurations to monitor and ingest. Each folder config should include 'folder_id':str and optional 'metadata':dict to attach to ingested documents. ",
    )

    page_index_action: str = attribute(
        default="PageIndexRetrievalInteractAction",
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

    async def _process_single_document(
        self,
        google_drive_documents_node: Any,
        google_drive_action: Any,
        file_info: Dict[str, Any],
        doc_type: str,
        collection_name: str,
        metadata: Dict[str, Any],
        model: Optional[str],
        model_action: Optional[Any],
        node_summary: Optional[Any],
        agent_id: str,
        page_index_action: Any,
        old_file: Optional[Dict[str, Any]] = None,
        source: str = "ingesting_documents",
        convert_to_markdown: bool = False,
        ocr: bool = False,
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
                return await _pop_skip_unsupported_head(
                    google_drive_documents_node,
                    source=source,
                    doc_type=doc_type,
                    doc_name=doc_name,
                )

            async def _mark_ingest_failed() -> None:
                docs = getattr(google_drive_documents_node, source)
                if doc_type == "added":
                    docs["added"].pop(0)
                    google_drive_documents_node.failed_documents["added"].append(
                        file_info
                    )
                else:
                    docs["modified"].pop(0)
                    google_drive_documents_node.failed_documents["modified"].append(
                        file_info
                    )
                google_drive_documents_node.active_document = ""
                ingesting = google_drive_documents_node.ingesting_documents
                google_drive_documents_node.status = (
                    "failed"
                    if (
                        not ingesting["added"]
                        and not ingesting["modified"]
                        and not ingesting["removed"]
                    )
                    else "pending"
                )
                await google_drive_documents_node.save()

            async def _do_ingest() -> Dict[str, Any]:
                file_bytes = await google_drive_action.get_media(file_id=file_id)
                forge_base = get_jvagent_jvforge_base_url()
                if forge_base:
                    summary_for_forge = await _if_add_node_summary_for_jvforge(
                        agent_id, node_summary
                    )
                    llm_wh_url = await page_index_action.get_webhook_url()
                    async_mode = (
                        os.environ.get("JVAGENT_JVFORGE_ASYNC", "false").lower()
                        == "true"
                    )
                    if async_mode:
                        q = await assimilate_via_jvforge_async(
                            base_url=forge_base,
                            agent_id=agent_id,
                            filename=doc_name,
                            content=file_bytes,
                            doc_name=doc_name,
                            model=model,
                            if_add_node_summary=summary_for_forge,
                            collection_name=collection_name,
                            metadata=metadata or None,
                            doc_description=None,
                            doc_url=doc_url or None,
                            convert_to_markdown=convert_to_markdown,
                            ocr=ocr,
                            llm_webhook_url=llm_wh_url,
                            emergency=False,
                        )
                        return {
                            "doc_name": doc_name,
                            "jvforge_job_id": q.get("job_id"),
                            "jvforge_queue_status": q.get("status"),
                        }
                    await assimilate_via_jvforge(
                        base_url=forge_base,
                        agent_id=agent_id,
                        filename=doc_name,
                        content=file_bytes,
                        doc_name=doc_name,
                        model=model,
                        if_add_node_summary=summary_for_forge,
                        collection_name=collection_name,
                        metadata=metadata or None,
                        doc_description=None,
                        doc_url=doc_url or None,
                        convert_to_markdown=convert_to_markdown,
                        ocr=ocr,
                        llm_webhook_url=llm_wh_url,
                    )
                    return {"doc_name": doc_name}
                await assimilate_document(
                    file_bytes,
                    doc_name=doc_name,
                    model=model,
                    model_action=model_action,
                    if_add_node_summary=node_summary,
                    persist=True,
                    collection_name=collection_name,
                    doc_url=doc_url,
                    metadata=metadata,
                    cancel_event=cancel_event,
                    convert_to_markdown=convert_to_markdown,
                    ocr=ocr,
                )
                return {"doc_name": doc_name}

            try:
                result = await asyncio.wait_for(_do_ingest(), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                cancel_event.set()
                await _mark_ingest_failed()
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
                await _mark_ingest_failed()
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
                await _mark_ingest_failed()
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
                            collection_name=collection_name,
                        )
                google_drive_documents_node.active_document = ""
                ingesting = google_drive_documents_node.ingesting_documents
                google_drive_documents_node.status = (
                    "completed"
                    if (
                        not ingesting["added"]
                        and not ingesting["modified"]
                        and not ingesting["removed"]
                    )
                    else "pending"
                )
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
        google_drive_folders: list = [],
        remove_deleted_documents: bool = False,
        retry_failed_documents: bool = False,
        convert_to_markdown: bool = False,
        ocr: bool = False,
    ) -> dict:
        """Recursively extract and ingest PDF documents from Google Drive folders.

        Processes one document per invocation. If active_document is set, returns immediately.
        Uses agent-level lock to prevent concurrent ingestion from multiple webhook calls.

        Args:
            google_drive_folders: List of folder configs, e.g.
                [{"folder_id": "<folder_id>", "metadata": {"key": "value"}}]

        Returns:
            Dict with status and list of ingested document names.
        """
        empty_result: Dict[str, Any] = {
            "status": "error",
            "message": "",
            "documents_ingested": {"added": [], "updated": [], "to_be_removed": []},
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
                f"No {self.page_index_action} found! Unable to ingest documents from Google Drive"
            )
            empty_result["message"] = f"No {self.page_index_action} found"
            return empty_result

        if not google_drive_folders:
            google_drive_folders = self.google_drive_folders
        if not self.google_drive_folders and not google_drive_folders:
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
            )

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
    ) -> dict:
        """Inner ingestion logic (called with ingestion lock held)."""
        if get_jvagent_jvforge_base_url():
            initialize_pageindex_database(app_id=await _get_app_id_from_node())
        logger.info(
            "PageIndex Google Drive Sync: starting ingestion for %d folder(s)",
            len(google_drive_folders),
        )
        document_ingested: Dict[str, List[str]] = {
            "added": [],
            "updated": [],
            "to_be_removed": [],
        }

        collection_name = page_index_action._resolve_collection()
        model_action = await page_index_action.get_model_action()
        node_summary = page_index_action.config.get("node_summary")
        model = page_index_action.config.get("model")

        # Phase A: Sync files for all folders
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
                                item["id"]: idx
                                for idx, item in enumerate(
                                    google_drive_documents_node.ingesting_documents[key]
                                )
                            }
                            for item in ingesting_documents[key]:
                                if item["id"] in existing_items:
                                    google_drive_documents_node.ingesting_documents[
                                        key
                                    ][existing_items[item["id"]]] = item
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
                    _recompute_google_drive_node_idle_status(
                        google_drive_documents_node
                    )
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
                    await self.connect(google_drive_documents_node)

        # Phase B: Check for active document across all folders first
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
                    "message": f"Document {google_drive_documents_node.active_document} is currently being processed. Please try again later.",
                    "documents_ingested": document_ingested,
                }

        # Phase C: Pick and process one document
        for google_drive_folder in google_drive_folders:
            google_drive_folder_id = google_drive_folder.get("folder_id")
            google_drive_documents_node = await self.node(
                node="GoogleDriveDocuments", folder_id=google_drive_folder_id
            )

            if not google_drive_documents_node:
                continue

            if google_drive_documents_node.status == "completed":
                continue
            elif google_drive_documents_node.status == "failed":
                if retry_failed_documents:
                    ingesting_documents = google_drive_documents_node.failed_documents
                else:
                    continue
            else:
                ingesting_documents = google_drive_documents_node.ingesting_documents

            await _pop_disabled_head_queues(
                google_drive_documents_node, ingesting_documents
            )

            metadata = google_drive_folder.get("metadata", {})

            if ingesting_documents["added"]:
                new_file = ingesting_documents["added"][0]
                result = await self._process_single_document(
                    google_drive_documents_node=google_drive_documents_node,
                    google_drive_action=google_drive_action,
                    file_info=new_file,
                    doc_type="added",
                    collection_name=collection_name,
                    metadata=metadata,
                    model=model,
                    model_action=model_action,
                    node_summary=node_summary,
                    agent_id=agent_id,
                    page_index_action=page_index_action,
                    source=(
                        "failed_documents"
                        if google_drive_documents_node.status == "failed"
                        else "ingesting_documents"
                    ),
                    convert_to_markdown=convert_to_markdown,
                    ocr=ocr,
                )
                if result["success"] and not result.get("skipped"):
                    document_ingested["added"].append(result["doc_name"])
                return {
                    "status": "completed",
                    "message": result["ingestion_message"],
                    "documents_ingested": document_ingested,
                }

            elif ingesting_documents["modified"]:
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
                    collection_name=collection_name,
                    metadata=metadata,
                    model=model,
                    model_action=model_action,
                    node_summary=node_summary,
                    agent_id=agent_id,
                    page_index_action=page_index_action,
                    old_file=old_file,
                    source=(
                        "failed_documents"
                        if google_drive_documents_node.status == "failed"
                        else "ingesting_documents"
                    ),
                    convert_to_markdown=convert_to_markdown,
                    ocr=ocr,
                )
                if result["success"] and not result.get("skipped"):
                    document_ingested["updated"].append(result["doc_name"])
                return {
                    "status": "completed",
                    "message": result["ingestion_message"],
                    "documents_ingested": document_ingested,
                }

            elif ingesting_documents["removed"]:
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
                            ingesting = google_drive_documents_node.ingesting_documents
                            google_drive_documents_node.status = (
                                "failed"
                                if (
                                    not ingesting["added"]
                                    and not ingesting["modified"]
                                    and not ingesting["removed"]
                                )
                                else "pending"
                            )
                            await google_drive_documents_node.save()
                            logger.error(f"Error deleting document: {e}")
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

                google_drive_documents_node.ingesting_documents = ingesting_documents
                google_drive_documents_node.status = (
                    "completed"
                    if (
                        not ingesting_documents["added"]
                        and not ingesting_documents["modified"]
                        and not ingesting_documents["removed"]
                    )
                    else "pending"
                )
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
                google_drive_documents_node.status = "completed"
                await google_drive_documents_node.save()
                break

        logger.warning("No pending documents to ingest")
        return {
            "status": "completed",
            "message": "No pending documents to ingest",
            "documents_ingested": document_ingested,
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

        _recompute_google_drive_node_idle_status(google_drive_documents_node)
        await google_drive_documents_node.save()
        return {
            "folder_id": folder_id,
            "file_id": file_id,
            "disable_ingestion": bool(disable_ingestion),
        }

    async def get_google_drive_documents(self) -> List[Dict[str, Any]]:
        """get google drive documents"""
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
        """Check if the WhatsApp action has required configuration.

        Required configuration:
        - base_url: Application base URL for webhook generation

        Returns:
            True if required configuration is present and valid, False otherwise.
        """
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
            raise ValidationError(f"Webhook URL generation failed: {e}")

    async def on_register(self) -> None:
        """Called when action is registered. Validates configuration."""
        await self._apply_env_defaults()
        if not self.is_configured():
            logger.debug("Page Index Google Drive Sync action not configured")
            return
        logger.debug("Page Index Google Drive Sync action registered")

    async def on_reload(self) -> None:
        """Called when action is reloaded. Re-registers session with current webhook URL."""
        await self._apply_env_defaults()
        if not self.is_configured():
            logger.debug(
                "Page Index Google Drive Sync action not configured, skipping reload"
            )
            return

        try:
            if not self.webhook_url:
                await self.get_webhook_url()

        except Exception as e:
            logger.error(
                f"Error re-registering Page Index Google Drive Sync action during reload: {e}",
                exc_info=True,
            )

    async def on_startup(self) -> None:
        """Initialize filter and adapter, attempt session registration with configurable timeout."""
        await self._apply_env_defaults()
        if not self.is_configured() or not self.enabled:
            return

        try:
            if not self.webhook_url:
                await self.get_webhook_url()

        except Exception as e:
            logger.error(
                f"Error re-registering Page Index Google Drive Sync action during startup: {e}",
                exc_info=True,
            )
