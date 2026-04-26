"""Google Docs Action — create, read, update, and comment on Google Docs documents."""

import logging
from typing import Any, ClassVar, Dict, List, Optional

from jvspatial.core.annotations import attribute

from ..google_action import GoogleAction

logger = logging.getLogger(__name__)


class GoogleDocsAction(GoogleAction):
    """Action for Google Docs operations using OAuth2 (user-delegated credentials).

    Provides:
    - Create documents from structured content
    - Read document content (plain text)
    - Update document content (append, insert at range)
    - Insert and manage comments
    - Apply basic formatting
    """

    output_format: str = attribute(
        default="google_doc",
        description="Preferred output format: 'google_doc' or 'markdown'",
    )

    API_SERVICE_NAME: ClassVar[str] = "docs"
    API_VERSION: ClassVar[str] = "v1"
    SCOPES: ClassVar[List[str]] = [
        "https://www.googleapis.com/auth/documents",
        "https://www.googleapis.com/auth/drive.file",
    ]

    async def create_document(
        self,
        title: str,
    ) -> Dict[str, Any]:
        """Create a new empty Google Doc.

        Returns the document ID and URL.
        """
        service = await self.get_service()
        doc = service.documents().create(body={"title": title}).execute()
        return {
            "document_id": doc.get("documentId"),
            "title": doc.get("title"),
            "url": f"https://docs.google.com/document/d/{doc.get('documentId')}/edit",
        }

    async def read_document(self, document_id: str) -> Dict[str, Any]:
        """Read a document's content and return structured data."""
        service = await self.get_service()
        doc = service.documents().get(documentId=document_id).execute()

        # Extract plain text from the document body
        paragraphs = []
        content = doc.get("body", {}).get("content", [])
        for elem in content:
            paragraph = elem.get("paragraph", {})
            if not paragraph:
                continue
            elements = paragraph.get("elements", [])
            text_parts = []
            for pe in elements:
                text_run = pe.get("textRun", {})
                if text_run:
                    text_parts.append(text_run.get("content", ""))
            if text_parts:
                paragraphs.append("".join(text_parts))

        return {
            "document_id": document_id,
            "title": doc.get("title"),
            "paragraphs": paragraphs,
            "plain_text": "\n".join(paragraphs),
        }

    async def append_text(
        self,
        document_id: str,
        text: str,
    ) -> bool:
        """Append text to the end of a document."""
        service = await self.get_service()

        # Get the end index of the document body
        doc = service.documents().get(documentId=document_id).execute()
        body = doc.get("body", {})
        content = body.get("content", [])
        if not content:
            return False

        last_segment = content[-1]
        end_index = last_segment.get("endIndex", 1)

        requests = [
            {
                "insertText": {
                    "location": {"index": end_index - 1},
                    "text": text,
                }
            }
        ]

        service.documents().batchUpdate(
            documentId=document_id, body={"requests": requests}
        ).execute()
        return True

    async def insert_text_at(
        self,
        document_id: str,
        text: str,
        index: int,
    ) -> bool:
        """Insert text at a specific index in the document."""
        service = await self.get_service()
        requests = [
            {
                "insertText": {
                    "location": {"index": index},
                    "text": text,
                }
            }
        ]
        service.documents().batchUpdate(
            documentId=document_id, body={"requests": requests}
        ).execute()
        return True

    async def insert_comment(
        self,
        document_id: str,
        text: str,
        content: str,
    ) -> Dict[str, Any]:
        """Insert a comment on a document (via Drive API comments resource).

        Note: The Docs API does not natively support creating comments.
        This uses the Drive API's comments resource as a workaround.
        """
        from googleapiclient.discovery import build

        # Build the Drive service for comment operations
        creds = await self._load_credentials()
        drive_service = build("drive", "v3", credentials=creds)

        comment_body = {"content": text, "anchor": content}
        comment = (
            drive_service.comments()
            .create(fileId=document_id, body=comment_body)
            .execute()
        )
        return {
            "comment_id": comment.get("id"),
            "content": comment.get("content"),
            "created_time": comment.get("createdTime"),
        }

    async def replace_text(
        self,
        document_id: str,
        old_text: str,
        new_text: str,
    ) -> bool:
        """Replace all occurrences of old_text with new_text in the document."""
        service = await self.get_service()
        requests = [
            {
                "replaceAllText": {
                    "containsText": {
                        "text": old_text,
                        "matchCase": False,
                    },
                    "replaceText": new_text,
                }
            }
        ]
        service.documents().batchUpdate(
            documentId=document_id, body={"requests": requests}
        ).execute()
        return True

    async def batch_update(
        self,
        document_id: str,
        requests: List[Dict[str, Any]],
    ) -> bool:
        """Execute a batch of arbitrary update requests on a document.

        Args:
            document_id: The Google Doc ID.
            requests: List of Google Docs API request objects.
                     See https://developers.google.com/docs/api/reference/rest/v1/documents/request
        """
        service = await self.get_service()
        service.documents().batchUpdate(
            documentId=document_id, body={"requests": requests}
        ).execute()
        return True
