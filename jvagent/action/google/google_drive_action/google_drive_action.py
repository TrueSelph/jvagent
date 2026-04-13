import io
import logging
from typing import Any, ClassVar, Dict, List, Optional

from googleapiclient.http import MediaIoBaseDownload
from jvspatial.core.annotations import attribute
from jvspatial.env import env

from ..google_action import GoogleAction

logger = logging.getLogger(__name__)


class GoogleDriveAction(GoogleAction):
    """Action for Google Drive operations using OAuth2 (user-delegated credentials)."""

    # default_parent_id: str = attribute(
    #     default="root", description="Default parent folder ID for uploads"
    # )

    API_SERVICE_NAME: ClassVar[str] = "drive"
    API_VERSION: ClassVar[str] = "v3"
    SCOPES: ClassVar[List[str]] = ["https://www.googleapis.com/auth/drive"]

    @staticmethod
    def _env_default_parent_id() -> str:
        return env("GOOGLE_DRIVE_PARENT_FOLDER_ID")

    async def upload_file(
        self,
        name: str,
        content: Optional[str] = None,
        source_url: Optional[str] = None,
        mime_type: Optional[str] = None,
        parent_folder_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upload a file to Google Drive."""
        service = await self.get_service()
        parent_id = parent_folder_id or self._env_default_parent_id()

        file_metadata = {"name": name, "parents": [parent_id]}

        import base64
        import io

        import httpx
        from googleapiclient.http import MediaIoBaseUpload

        media = None
        if content:
            file_data = base64.b64decode(content)
            media = MediaIoBaseUpload(
                io.BytesIO(file_data), mimetype=mime_type, resumable=True
            )
        elif source_url:
            async with httpx.AsyncClient() as client:
                resp = await client.get(source_url)
                resp.raise_for_status()
                media = MediaIoBaseUpload(
                    io.BytesIO(resp.content),
                    mimetype=mime_type or resp.headers.get("content-type"),
                    resumable=True,
                )

        if not media:
            file_metadata["mimeType"] = "application/vnd.google-apps.folder"
            return (
                service.files().create(body=file_metadata, fields="id, name").execute()
            )

        return (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id, name")
            .execute()
        )

    async def delete_file(self, file_id: str) -> bool:
        """Delete a file from Google Drive."""
        service = await self.get_service()
        service.files().delete(fileId=file_id).execute()
        return True

    async def list_files(
        self, folder_id: Optional[str] = None, with_link: bool = False, depth: int = 5
    ) -> List[Dict[str, Any]]:
        """
        List files and folders recursively up to a specified depth.
        """
        if depth < 0:
            return []

        service = await self.get_service()
        parent_id = folder_id or self._env_default_parent_id()

        q = f"'{parent_id}' in parents and trashed = false"
        fields = (
            "files(id, name, mimeType, createdTime, modifiedTime"
            + (", webViewLink" if with_link else "")
            + ")"
        )

        # Note: .execute() is usually synchronous in the standard google-api-python-client.
        # If using a wrapper like aiogoogle, ensure you await this call.
        results = service.files().list(q=q, fields=f"nextPageToken, {fields}").execute()
        files = results.get("files", [])

        for f in files:
            # Standardize the link key if requested
            if with_link and "webViewLink" in f:
                f["url"] = f.pop("webViewLink")

            # If it's a folder, look deeper
            if f["mimeType"] == "application/vnd.google-apps.folder":
                if depth > 0:
                    # Recursive call to get children
                    f["files"] = await self.list_files(
                        folder_id=f["id"], with_link=with_link, depth=depth - 1
                    )
                else:
                    # If we hit depth limit, provide an empty list or omit
                    f["files"] = []

        return files

    async def share_file(
        self,
        file_id: str,
        share_type: str = "link",
        link_scope: str = "anyone",
        email: Optional[str] = None,
        role: str = "reader",
    ) -> Dict[str, Any]:
        """Share a file on Google Drive."""
        service = await self.get_service()

        if share_type == "link":
            permission = {"type": link_scope, "role": role}
        else:
            permission = {"type": "user", "role": role, "emailAddress": email}

        service.permissions().create(fileId=file_id, body=permission).execute()

        if share_type == "link":
            file = service.files().get(fileId=file_id, fields="webViewLink").execute()
            return {"webViewLink": file.get("webViewLink")}

        return {"success": True}

    async def get_media(self, file_id: str) -> bytes:
        """
        Download a file's content from Google Drive.
        Handles both regular binary files and Google Workspace documents (Docs, Sheets, etc.).
        """
        service = await self.get_service()

        # 1. Fetch metadata to determine if it's a Google Doc that needs exporting
        file_metadata = (
            service.files().get(fileId=file_id, fields="name, mimeType").execute()
        )

        mime_type = file_metadata.get("mimeType", "")

        # 2. Define the request based on file type
        if mime_type.startswith("application/vnd.google-apps."):
            # Handle Google Docs by exporting to PDF by default
            # You can change 'application/pdf' to other formats (e.g., docx, xlsx)
            request = service.files().export_media(
                fileId=file_id, mimeType="application/pdf"
            )
        else:
            # Standard binary download for images, PDFs, ZIPs, etc.
            request = service.files().get_media(fileId=file_id)

        # 3. Perform the download using a buffer
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)

        done = False
        while not done:
            # Standard google-api-client execute() is synchronous
            status, done = downloader.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                # logger.debug(f"Download progress for {file_id}: {progress}%")

        # 4. Return the bytes
        return fh.getvalue()

    def compare_files(
        self, old_files: List[Dict], new_files: List[Dict], ignore_folders: bool = True
    ) -> Dict[str, List[Dict]]:
        """
        Compares two nested file lists and returns added, removed, and modified items.
        files format: [
            {
            "id": "<folder_id>",
            "name": "new folder",
            "mimeType": "application/vnd.google-apps.folder",
            "createdTime": "2026-03-13T13:49:56.485Z",
            "modifiedTime": "2026-03-13T13:49:56.485Z",
            "files": [...],
            },
            ...
        ]
        return {
            "added": [
                {
                    "id": "<file_id>",
                    "name": "my-cv.pdf",
                    "mimeType": "application/pdf"
                }
            ],
            "removed": [...],
            "modified": [
                {
                    "id": "<folder_id>",
                    "old": { ... },
                    "new": { ... }
                }
            ]
        }
        """

        def flatten_to_dict(items, lookup=None):
            if lookup is None:
                lookup = {}
            for item in items:
                # Store a copy of the item without the nested 'files' for clean comparison
                item_copy = {k: v for k, v in item.items() if k != "files"}
                lookup[item["id"]] = item_copy

                # Recurse if there are nested files
                if "files" in item and item["files"]:
                    flatten_to_dict(item["files"], lookup)
            return lookup

        old_map = flatten_to_dict(old_files)
        new_map = flatten_to_dict(new_files)

        old_ids = set(old_map.keys())
        new_ids = set(new_map.keys())

        # # 1. Added: IDs in new but not in old
        # added = [new_map[fid] for fid in (new_ids - old_ids)]

        # # 2. Removed: IDs in old but not in new
        # removed = [old_map[fid] for fid in (old_ids - new_ids)]

        # # 3. Modified: IDs in both, but content (like name) changed
        # modified = []
        # for fid in old_ids & new_ids:
        #     if old_map[fid] != new_map[fid]:
        #         modified.append({"id": fid, "old": old_map[fid], "new": new_map[fid]})

        # 1. Added: IDs in new but not in old
        added = []
        for fid in new_ids - old_ids:
            # Skip if it's a folder
            if (
                new_map[fid].get("mimeType") == "application/vnd.google-apps.folder"
                and ignore_folders
            ):
                continue
            added.append(new_map[fid])

        # 2. Removed: IDs in old but not in new
        removed = []
        for fid in old_ids - new_ids:
            # Skip if it's a folder
            if (
                old_map[fid].get("mimeType") == "application/vnd.google-apps.folder"
                and ignore_folders
            ):
                continue
            removed.append(old_map[fid])

        # 3. Modified: IDs in both, but content (like name) changed
        modified = []
        for fid in old_ids & new_ids:
            # Skip if it's a folder
            if (
                old_map[fid].get("mimeType") == "application/vnd.google-apps.folder"
                and ignore_folders
            ):
                continue
            if old_map[fid] != new_map[fid]:
                modified.append({"id": fid, "old": old_map[fid], "new": new_map[fid]})

        return {"added": added, "removed": removed, "modified": modified}
