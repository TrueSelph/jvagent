"""Google Docs Action — create, read, update, and comment on Google Docs documents."""

import io
import logging
import re
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from jvspatial.core.annotations import attribute

from ..google_action import GoogleAction

logger = logging.getLogger(__name__)


class GoogleDocsAction(GoogleAction):
    """Action for Google Docs operations using OAuth2 (user-delegated credentials)."""

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

    async def _get_drive_service(self):
        creds = await self._load_credentials()
        return build("drive", "v3", credentials=creds)

    @staticmethod
    def _doc_url(document_id: str) -> str:
        return f"https://docs.google.com/document/d/{document_id}/edit"

    async def create_document(self, title: str) -> Dict[str, Any]:
        service = await self.get_service()
        doc = service.documents().create(body={"title": title}).execute()
        doc_id = doc.get("documentId")
        return {
            "document_id": doc_id,
            "title": doc.get("title"),
            "url": self._doc_url(doc_id),
        }

    async def copy_template_document(
        self,
        template_document_id: str,
        title: str,
        folder_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        drive = await self._get_drive_service()
        body: Dict[str, Any] = {"name": title}
        if folder_id:
            body["parents"] = [folder_id]
        copied = (
            drive.files()
            .copy(fileId=template_document_id, body=body, fields="id,name,parents")
            .execute()
        )
        doc_id = copied.get("id")
        return {
            "document_id": doc_id,
            "title": copied.get("name", title),
            "url": self._doc_url(doc_id),
            "folder_id": folder_id,
            "source_template_id": template_document_id,
        }

    async def read_document(self, document_id: str) -> Dict[str, Any]:
        service = await self.get_service()
        doc = service.documents().get(documentId=document_id).execute()

        paragraphs: List[str] = []
        content = doc.get("body", {}).get("content", [])
        for elem in content:
            paragraph = elem.get("paragraph", {})
            if not paragraph:
                continue
            elements = paragraph.get("elements", [])
            text_parts: List[str] = []
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
            "body": doc.get("body", {}),
        }

    async def append_text(self, document_id: str, text: str) -> bool:
        service = await self.get_service()
        doc = service.documents().get(documentId=document_id).execute()
        content = doc.get("body", {}).get("content", [])
        if not content:
            return False
        end_index = content[-1].get("endIndex", 1)
        requests = [
            {"insertText": {"location": {"index": end_index - 1}, "text": text}}
        ]
        service.documents().batchUpdate(
            documentId=document_id, body={"requests": requests}
        ).execute()
        return True

    async def replace_document_body(self, document_id: str, content: str) -> bool:
        service = await self.get_service()
        doc = service.documents().get(documentId=document_id).execute()
        doc_content = doc.get("body", {}).get("content", [])
        end_index = doc_content[-1].get("endIndex", 1) if doc_content else 1
        requests: List[Dict[str, Any]] = []
        if end_index > 2:
            requests.append(
                {
                    "deleteContentRange": {
                        "range": {
                            "startIndex": 1,
                            "endIndex": end_index - 1,
                        }
                    }
                }
            )
        requests.append(
            {"insertText": {"location": {"index": 1}, "text": content or ""}}
        )
        service.documents().batchUpdate(
            documentId=document_id, body={"requests": requests}
        ).execute()
        return True

    async def insert_text_at(self, document_id: str, text: str, index: int) -> bool:
        service = await self.get_service()
        requests = [{"insertText": {"location": {"index": index}, "text": text}}]
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
        drive_service = await self._get_drive_service()
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

    async def list_comments(self, document_id: str) -> List[Dict[str, Any]]:
        drive_service = await self._get_drive_service()
        result = (
            drive_service.comments()
            .list(
                fileId=document_id,
                fields="comments(id,content,resolved,createdTime,modifiedTime,author/displayName)",
            )
            .execute()
        )
        return result.get("comments", [])

    async def replace_text(
        self, document_id: str, old_text: str, new_text: str
    ) -> bool:
        service = await self.get_service()
        requests = [
            {
                "replaceAllText": {
                    "containsText": {"text": old_text, "matchCase": False},
                    "replaceText": new_text,
                }
            }
        ]
        service.documents().batchUpdate(
            documentId=document_id, body={"requests": requests}
        ).execute()
        return True

    async def replace_named_placeholders(
        self,
        document_id: str,
        values: Dict[str, str],
    ) -> Dict[str, Any]:
        replaced = 0
        for key, value in (values or {}).items():
            if not key:
                continue
            await self.replace_text(
                document_id=document_id,
                old_text=f"{{{{ {key} }}}}",
                new_text=str(value),
            )
            await self.replace_text(
                document_id=document_id, old_text=f"{{{{{key}}}}}", new_text=str(value)
            )
            replaced += 1
        return {"replaced_placeholders": replaced}

    def _markdown_to_doc_ops(
        self, markdown: str
    ) -> Tuple[str, List[Dict[str, Any]], List[Tuple[int, int]]]:
        lines = markdown.splitlines()
        out_lines: List[str] = []
        style_ranges: List[Dict[str, Any]] = []
        bullet_ranges: List[Tuple[int, int]] = []
        cursor = 1
        bullet_start: Optional[int] = None
        bullet_end: Optional[int] = None

        for raw in lines:
            line = raw.rstrip()
            style = "NORMAL_TEXT"
            clean = line
            if line.startswith("### "):
                clean = line[4:]
                style = "HEADING_3"
            elif line.startswith("## "):
                clean = line[3:]
                style = "HEADING_2"
            elif line.startswith("# "):
                clean = line[2:]
                style = "HEADING_1"
            elif re.match(r"^\s*[-*]\s+", line):
                clean = re.sub(r"^\s*[-*]\s+", "", line)
                if bullet_start is None:
                    bullet_start = cursor
            else:
                if bullet_start is not None and bullet_end is not None:
                    bullet_ranges.append((bullet_start, bullet_end))
                bullet_start = None
                bullet_end = None

            line_text = f"{clean}\n"
            start = cursor
            end = start + len(line_text)
            out_lines.append(line_text)
            style_ranges.append({"start": start, "end": end, "style": style})
            cursor = end
            if bullet_start is not None:
                bullet_end = end

        if bullet_start is not None and bullet_end is not None:
            bullet_ranges.append((bullet_start, bullet_end))

        return "".join(out_lines), style_ranges, bullet_ranges

    async def render_markdown_blocks(
        self, document_id: str, markdown: str
    ) -> Dict[str, Any]:
        service = await self.get_service()
        text, styles, bullets = self._markdown_to_doc_ops(markdown)
        await self.replace_document_body(document_id=document_id, content=text)

        requests: List[Dict[str, Any]] = []
        for item in styles:
            requests.append(
                {
                    "updateParagraphStyle": {
                        "range": {
                            "startIndex": item["start"],
                            "endIndex": max(item["start"] + 1, item["end"]),
                        },
                        "paragraphStyle": {"namedStyleType": item["style"]},
                        "fields": "namedStyleType",
                    }
                }
            )

        for start, end in bullets:
            if end > start:
                requests.append(
                    {
                        "createParagraphBullets": {
                            "range": {"startIndex": start, "endIndex": end},
                            "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                        }
                    }
                )

        if requests:
            service.documents().batchUpdate(
                documentId=document_id, body={"requests": requests}
            ).execute()
        return {
            "rendered": True,
            "paragraphs": len(styles),
            "bullet_blocks": len(bullets),
        }

    async def export_pdf(self, document_id: str) -> bytes:
        drive_service = await self._get_drive_service()
        request = drive_service.files().export_media(
            fileId=document_id, mimeType="application/pdf"
        )
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        fh.seek(0)
        return fh.read()

    async def batch_update(
        self,
        document_id: str,
        requests: List[Dict[str, Any]],
    ) -> bool:
        service = await self.get_service()
        service.documents().batchUpdate(
            documentId=document_id, body={"requests": requests}
        ).execute()
        return True
