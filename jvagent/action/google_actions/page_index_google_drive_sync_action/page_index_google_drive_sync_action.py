import json
import logging
import re
import os
import time
import asyncio
from typing import Any, ClassVar, Dict, List, Optional

import httpx
from jvspatial.api.auth.api_key_service import APIKeyService

from jvspatial.core.annotations import attribute
from jvspatial.core.context import GraphContext
from jvspatial.db import get_prime_database
from jvspatial.exceptions import DatabaseError, ValidationError
from .webhook_auth import get_or_create_system_user

from jvagent.action.pageindex.documents import delete_document

from ..google_action import GoogleAction
from .google_drive_documents import GoogleDriveDocuments

logger = logging.getLogger(__name__)


class PageIndexGoogleDriveSyncAction(GoogleAction):
    """Action for operations using a service account."""

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

    active_document:Optional[str] = attribute(
        default=None,
        description="Active document to ingest",
    )


    async def ingest_documents_from_google_drive(
        self, google_drive_folders: list = [], remove_deleted_documents: bool = False, retry_failed_documents: bool = False
    ) -> dict:
        """Recursively extract and ingest PDF documents from Google Drive folders.

        Args:
            google_drive_folders: List of folder configs, e.g.
                [{"folder_id": "<folder_id>", "metadata": {"key": "value"}}]

        Returns:
            Dict with status and list of ingested document names.
        """
        from jvagent.action.pageindex.documents import assimilate_document

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

        document_ingested: Dict[str, List[str]] = {
            "added": [],
            "updated": [],
            "to_be_removed": [],
        }

        collection_name = page_index_action._resolve_collection()
        model_action = await page_index_action.get_model_action()
        node_summary=page_index_action.config.get("node_summary")
        model=page_index_action.config.get("model")


        for google_drive_folder in google_drive_folders:
            google_drive_folder_id = google_drive_folder.get("folder_id")

            # remove duplicates
            google_drive_documents_nodes = await self.nodes(node=GoogleDriveDocuments, folder_id=google_drive_folder_id)
            if len(google_drive_documents_nodes) > 1:
                for google_drive_documents_node in google_drive_documents_nodes:
                    if google_drive_documents_node.status == 'pending':
                        await google_drive_documents_node.delete()
                        break                    


            files = await google_drive_action.list_files(
                with_link=True, folder_id=google_drive_folder_id
            )
            metadata = google_drive_folder.get("metadata", {})
            # get documents node by folder id
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
                        # Create a lookup of existing items by ID
                        existing_items = {
                            item['id']: idx for idx, item in enumerate(
                                google_drive_documents_node.ingesting_documents[key]
                            )
                        }
                        
                        # Replace or add items
                        for item in ingesting_documents[key]:
                            if item['id'] in existing_items:
                                # Replace existing item
                                google_drive_documents_node.ingesting_documents[key][existing_items[item['id']]] = item
                            else:
                                # Add new item
                                google_drive_documents_node.ingesting_documents[key].append(item)

                await google_drive_documents_node.save()
            else:
                ingesting_documents = google_drive_action.compare_files(
                    old_files=[], new_files=files
                )
                google_drive_documents_node = await GoogleDriveDocuments.create(
                    folder_id=google_drive_folder_id, files=files, metadata=metadata, ingesting_documents=ingesting_documents
                )
                await self.connect(google_drive_documents_node)

        ingestion_message = ""
        for google_drive_folder in google_drive_folders:
            google_drive_folder_id = google_drive_folder.get("folder_id")
            google_drive_documents_node = await self.node(
                node="GoogleDriveDocuments", folder_id=google_drive_folder_id
            )

            logger.warning("is active")
            if google_drive_documents_node.status == "failed":
                if retry_failed_documents:
                    ingesting_documents = google_drive_documents_node.failed_documents
                    ingestion_message = f"Retrying failed document: {google_drive_documents_node.folder_id}"
                else:
                    ingestion_message = f"Skipping failed document: {google_drive_documents_node.folder_id}"
                    continue
            else:
                ingesting_documents = google_drive_documents_node.ingesting_documents

            # add new document if active document is empty
            if google_drive_documents_node.active_document:
                logger.warning("active")
                # break
                return {
                    "status": "completed",
                    "message": f"Document {google_drive_documents_node.active_document} is currently being processed. Please try again later.",
                    "documents_ingested": document_ingested,
                }

            google_drive_documents_node.status = "processing"
            await google_drive_documents_node.save()

            if ingesting_documents["added"]:
                new_file = ingesting_documents["added"][0]

                doc_name = new_file.get("name", "")
                doc_url = new_file.get("url", "")
                file_id = new_file.get("id", "")

                google_drive_documents_node.active_document = doc_name
                await google_drive_documents_node.save()
                self.active_document = doc_name
                await self.save()


                try:
                    logger.warning("adding")
                    file_bytes = await google_drive_action.get_media(file_id=file_id)
                    # result = await assimilate_document(
                    #     file_bytes,
                    #     doc_name=doc_name,
                    #     model=model,
                    #     model_action=model_action,
                    #     if_add_node_summary=node_summary,
                    #     persist=True,
                    #     collection_name=collection_name,
                    #     doc_url=doc_url,
                    #     metadata=metadata,
                    # )
                    await asyncio.sleep(40)  # Sleep for 40 seconds
                    result = {"doc_name": doc_name}

                    if result.get("doc_name"):
                        document_ingested["added"].append(doc_name)
                        ingesting_documents["added"].pop(0)
                        ingestion_message = f"Added {doc_name}"
                        self.active_document = None
                        await self.save()
                        google_drive_documents_node.active_document = ""
                        await google_drive_documents_node.save()
                except Exception as e:
                    self.active_document = None
                    await self.save()
                    google_drive_documents_node.status = "failed"
                    google_drive_documents_node.active_document = ""
                    google_drive_documents_node.failed_documents["added"].append(new_file)
                    ingesting_documents["added"].pop(0)
                    await google_drive_documents_node.save()
                    ingestion_message = f"Failed to ingest {doc_name}"
                    logger.error(f"Error ingesting document: {e}")
            

            elif ingesting_documents["modified"]:
                modified_result = ingesting_documents["modified"][0]

                new_file = modified_result['new']
                old_file = modified_result['old']

                doc_name = new_file.get("name", "")
                doc_url = new_file.get("url", "")
                file_id = new_file.get("id", "")

                google_drive_documents_node.active_document = doc_name
                await google_drive_documents_node.save()
                self.active_document = doc_name
                await self.save()

                try:
                    logger.warning("adding")
                    file_bytes = await google_drive_action.get_media(file_id=file_id)
                    # result = await assimilate_document(
                    #     file_bytes,
                    #     doc_name=doc_name,
                    #     model=model,
                    #     model_action=model_action,
                    #     if_add_node_summary=node_summary,
                    #     persist=True,
                    #     collection_name=collection_name,
                    #     doc_url=doc_url,
                    #     metadata=metadata,
                    # )
                    await asyncio.sleep(40)  # Sleep for 40 seconds
                    result = {"doc_name": doc_name}

                    if result.get("doc_name"):
                        document_ingested["updated"].append(doc_name)
                        ingestion_message = f"Updated {doc_name}"
                        # Delete old document
                        await delete_document(
                            old_file.get("name", ""), collection_name=self.agent_id
                        )
                        self.active_document = None
                        await self.save()
                        google_drive_documents_node.active_document = ""
                        ingesting_documents["modified"].pop(0)
                        await google_drive_documents_node.save()
                        
                except Exception as e:
                    self.active_document = None
                    await self.save()
                    google_drive_documents_node.status = "failed"
                    google_drive_documents_node.active_document = ""
                    google_drive_documents_node.failed_documents["modified"].append(new_file)
                    ingesting_documents["modified"].pop(0)
                    await google_drive_documents_node.save()
                    ingestion_message = f"Failed to update {doc_name}"
                    logger.error(f"Error ingesting document: {e}")

            elif ingesting_documents["removed"]:
                remove_docs_names = ""

                for removed_doc in ingesting_documents["removed"]:
                    if remove_deleted_documents:
                        try:
                            await delete_document(removed_doc.get("name", ""), collection_name=self.agent_id)
                            document_ingested["removed"].append(removed_doc.get("name", ""))
                            remove_docs_names += removed_doc.get("name", "") + ", "
                            
                        except Exception as e:
                            google_drive_documents_node.status = "failed"
                            await google_drive_documents_node.save()
                            google_drive_documents_node.failed_documents["removed"].append(removed_doc)
                            ingestion_message = f"Failed to delete {removed_doc.get('name', '')}"
                            logger.error(f"Error deleting document: {e}")
                    
                        ingestion_message = f"Deleted {remove_docs_names[:-2]}"
                    else:
                        document_ingested["to_be_removed"].extend(ingesting_documents["removed"])



            # update ingesting documents
            google_drive_documents_node.ingesting_documents = ingesting_documents
            
            if not ingestion_message:
                google_drive_documents_node.status = "completed"
                break
            
            await google_drive_documents_node.save()

        return {
            "status": "completed",
            "message": ingestion_message,
            "documents_ingested": document_ingested,
        }


    async def delete_google_drive_documents(self, document_id: str=None) -> None:
        """delete google drive documents"""
        node = await GoogleDriveDocuments.find_one({"id": document_id})
        google_drive_documents_nodes = await self.nodes(node=GoogleDriveDocuments)
        for node in google_drive_documents_nodes:
            if document_id:
                if node.id == document_id:
                    await node.delete()
            else:
                await node.delete()
        

    async def get_google_drive_documents(self) -> List[Dict[str, Any]]:
        """get google drive documents"""
        google_drive_documents_nodes = await self.nodes(node=GoogleDriveDocuments)
        google_drive_documents = []
        #   "message": "An unexpected error occurred: Listing failed: 'tuple' object has no attribute 'folder_id' (details: {'error': \"'tuple' object has no attribute 'folder_id'\"})",
        for google_drive_documents_node in google_drive_documents_nodes:
            # 13:13:50 | WARNING | jvagent.action.google_actions.page_index_google_drive_sync_action.page_index_google_drive_sync_action | ('id', 'n.GoogleDriveDocuments.4dc01a84f38c4024b6821303')
            google_drive_documents.append({
                "folder_id": google_drive_documents_node.folder_id,
                "ingesting_documents": google_drive_documents_node.ingesting_documents,
                "status": google_drive_documents_node.status,
                "active_document": google_drive_documents_node.active_document,
                "metadata": google_drive_documents_node.metadata,
                "files": google_drive_documents_node.files
            })
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
            expected_url_base = (
                f"{self.base_url}/api/page_index_google_drive_sync/interact/webhook/{agent_id}"
            )

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
                permissions=["webhook:page_index_google_drive_sync_action"],
                expires_in_days=None,
                allowed_ips=[allowed_ip] if allowed_ip else [],
                allowed_endpoints=["/api/page_index_google_drive_sync/interact/webhook/*"],
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
            logger.debug("Page Index Google Drive Sync action not configured, skipping reload")
            return


        try:
            if not self.webhook_url:
                await self.get_webhook_url()
        
        except Exception as e:
            logger.error(
                f"Error re-registering Page Index Google Drive Sync action during reload: {e}", exc_info=True
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
                f"Error re-registering Page Index Google Drive Sync action during startup: {e}", exc_info=True
            )
    