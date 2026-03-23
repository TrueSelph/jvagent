import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from jvspatial.api.auth.api_key_service import APIKeyService
from jvspatial.core.annotations import attribute
from jvspatial.core.context import GraphContext
from jvspatial.db import get_prime_database
from jvspatial.exceptions import DatabaseError, ValidationError

from jvagent.action.pageindex.documents import assimilate_document, delete_document

from ..google_action import GoogleAction
from .google_drive_documents import GoogleDriveDocuments
from .webhook_auth import get_or_create_system_user

logger = logging.getLogger(__name__)

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
        description="Application base URL for webhook generation (APP_BASE_URL env var, e.g., https://myapp.example.com)",
    )

    document_timeout: Optional[int] = attribute(
        default=30,
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

            async def _do_ingest() -> Dict[str, Any]:
                file_bytes = await google_drive_action.get_media(file_id=file_id)
                result = await assimilate_document(
                    file_bytes,
                    doc_name=doc_name,
                    model=model,
                    model_action=model_action,
                    if_add_node_summary=node_summary,
                    persist=True,
                    collection_name=collection_name,
                    doc_url=doc_url,
                    metadata=metadata,
                )
                # await asyncio.sleep(40)  # Sleep for testing purposes
                return {"doc_name": doc_name}

            result = await asyncio.wait_for(_do_ingest(), timeout=timeout_seconds)

            if result.get("doc_name"):
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

                return {
                    "success": True,
                    "doc_name": doc_name,
                    "ingestion_message": (
                        f"Added {doc_name}"
                        if doc_type == "added"
                        else f"Updated {doc_name}"
                    ),
                }
            else:
                raise ValueError("assimilate_document returned no doc_name")
        except asyncio.TimeoutError:
            docs = getattr(google_drive_documents_node, source)
            if doc_type == "added":
                docs["added"].pop(0)
                google_drive_documents_node.failed_documents["added"].append(file_info)
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
            logger.warning(f"Document {doc_name} timed out after {timeout_seconds}s")
            return {
                "success": False,
                "doc_name": doc_name,
                "ingestion_message": f"Timed out ingesting {doc_name}",
            }
        except Exception as e:
            docs = getattr(google_drive_documents_node, source)
            if doc_type == "added":
                docs["added"].pop(0)
                google_drive_documents_node.failed_documents["added"].append(file_info)
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
            logger.error(f"Error ingesting document {doc_name}: {e}")
            return {
                "success": False,
                "doc_name": doc_name,
                "ingestion_message": (
                    f"Failed to ingest {doc_name}"
                    if doc_type == "added"
                    else f"Failed to update {doc_name}"
                ),
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
                google_drive_folders=google_drive_folders,
                remove_deleted_documents=remove_deleted_documents,
                retry_failed_documents=retry_failed_documents,
                google_drive_action=google_drive_action,
                page_index_action=page_index_action,
            )

    async def _ingest_documents_from_google_drive_inner(
        self,
        google_drive_folders: list,
        remove_deleted_documents: bool,
        retry_failed_documents: bool,
        google_drive_action: Any,
        page_index_action: Any,
    ) -> dict:
        """Inner ingestion logic (called with ingestion lock held)."""
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
                    ingesting_documents = google_drive_action.compare_files(
                        old_files=google_drive_documents_node.files, new_files=files
                    )
                    google_drive_documents_node.files = files
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

                    await google_drive_documents_node.save()
                else:
                    ingesting_documents = google_drive_action.compare_files(
                        old_files=[], new_files=files
                    )
                    google_drive_documents_node = await GoogleDriveDocuments.create(
                        folder_id=google_drive_folder_id,
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
                    source=(
                        "failed_documents"
                        if google_drive_documents_node.status == "failed"
                        else "ingesting_documents"
                    ),
                )
                if result["success"]:
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
                    old_file=old_file,
                    source=(
                        "failed_documents"
                        if google_drive_documents_node.status == "failed"
                        else "ingesting_documents"
                    ),
                )
                if result["success"]:
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
        """Delete Google Drive document nodes; returns deleted node ids."""
        deleted: List[str] = []
        google_drive_documents_nodes = await self.nodes(node=GoogleDriveDocuments)
        for node in google_drive_documents_nodes:
            if document_id:
                if node.id == document_id:
                    await node.delete()
                    deleted.append(str(node.id))
            else:
                await node.delete()
                deleted.append(str(node.id))
        return deleted

    async def get_google_drive_documents(self) -> List[Dict[str, Any]]:
        """get google drive documents"""
        google_drive_documents_nodes = await self.nodes(node=GoogleDriveDocuments)
        google_drive_documents = []
        for google_drive_documents_node in google_drive_documents_nodes:
            google_drive_documents.append(
                {
                    "folder_id": google_drive_documents_node.folder_id,
                    "ingesting_documents": google_drive_documents_node.ingesting_documents,
                    "status": google_drive_documents_node.status,
                    "active_document": google_drive_documents_node.active_document,
                    "metadata": google_drive_documents_node.metadata,
                    "files": google_drive_documents_node.files,
                }
            )
        return google_drive_documents

    # action configuration

    async def _apply_env_defaults(self) -> None:
        """Apply environment variable defaults for missing configuration.

        Sets the following from environment variables if not already configured:
        - base_url from APP_BASE_URL

        This allows users to set these values once in their .env file
        instead of configuring them per-action in agent.yaml.
        """
        # Application Base URL
        if not self.base_url or not self.base_url.strip():
            env_base_url = os.environ.get("APP_BASE_URL", "").strip()
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
                "base_url (APP_BASE_URL) is required for webhook URL generation"
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
