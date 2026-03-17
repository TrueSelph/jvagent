import json
import logging
import re
from typing import Any, ClassVar, Dict, List, Optional

import httpx
from jvspatial.core.annotations import attribute

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

    async def ingest_documents_from_google_drive(
        self, google_drive_folders: list = [], remove_deleted_documents: bool = False
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

        for google_drive_folder in google_drive_folders:
            google_drive_folder_id = google_drive_folder.get("folder_id")
            files = await google_drive_action.list_files(
                with_link=True, folder_id=google_drive_folder_id
            )
            metadata = google_drive_folder.get("metadata", {})
            old_folder_result = await self.node(
                node="GoogleDriveDocuments", folder_id=google_drive_folder_id
            )

            if old_folder_result:
                file_result = google_drive_action.compare_files(
                    old_files=old_folder_result.files, new_files=files
                )
                old_folder_result.files = files
                old_folder_result.metadata = metadata
                await old_folder_result.save()
            else:
                file_result = google_drive_action.compare_files(
                    old_files=[], new_files=files
                )
                google_drive_documents_node = await GoogleDriveDocuments.create(
                    folder_id=google_drive_folder_id, files=files, metadata=metadata
                )
                await self.connect(google_drive_documents_node)

            message = "Documents already ingested!"

            for new_file in file_result["added"]:
                doc_name = new_file.get("name", "")
                url = new_file.get("url", "")
                file_id = new_file.get("id", "")
                mime_type = new_file.get("mimeType", "")
                if mime_type == "application/vnd.google-apps.folder":
                    continue

                file_bytes = await google_drive_action.get_media(file_id)

                await assimilate_document(
                    file_bytes,
                    doc_name=doc_name,
                    model=page_index_action.config.get("model"),
                    model_action=model_action,
                    if_add_node_summary=page_index_action.config.get("node_summary"),
                    persist=True,
                    collection_name=collection_name,
                    doc_url=url,
                    metadata=metadata,
                )
                message = "Documents ingested successfully!"
                document_ingested["added"].append(doc_name)
                break

            for modified_result in file_result["modified"]:
                new_file = modified_result["new"]
                old_file = modified_result["old"]

                doc_name = new_file.get("name", "")
                url = new_file.get("url", "")
                file_id = new_file.get("id", "")

                # Delete old document
                await delete_document(
                    old_file.get("name", ""), collection_name=self.agent_id
                )

                # Download file from Google Drive URL
                # Convert web view link to direct download link
                mime_type = new_file.get("mimeType", "")
                if mime_type == "application/vnd.google-apps.folder":
                    continue

                file_bytes = await google_drive_action.get_media(file_id)

                await assimilate_document(
                    file_bytes,
                    doc_name=doc_name,
                    model=page_index_action.config.get("model"),
                    model_action=model_action,
                    if_add_node_summary=page_index_action.config.get("node_summary"),
                    persist=True,
                    collection_name=collection_name,
                    doc_url=url,
                    metadata=metadata,
                )

                message = "Documents ingested successfully!"
                document_ingested["updated"].append(doc_name)
                break

            for deleted_file in file_result["removed"]:
                doc_name = deleted_file.get("name", "")
                document_ingested["to_be_removed"].append(doc_name)

                # Delete existing document chunks
                if remove_deleted_documents:
                    await delete_document(doc_name, collection_name=self.agent_id)

            break

        return {
            "status": "completed",
            "message": message,
            "documents_ingested": document_ingested,
        }
