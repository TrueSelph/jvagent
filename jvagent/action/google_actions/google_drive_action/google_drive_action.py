import logging
from typing import Any, ClassVar, Dict, List, Optional

from jvspatial.core.annotations import attribute
from ..google_action import GoogleAction

logger = logging.getLogger(__name__)

class GoogleDriveAction(GoogleAction):
    """Action for Google Drive operations using a service account."""

    default_parent_id: str = attribute(
        default="root", description="Default parent folder ID for uploads"
    )

    API_SERVICE_NAME: ClassVar[str] = 'drive'
    API_VERSION: ClassVar[str] = 'v3'
    SCOPES: ClassVar[List[str]] = ['https://www.googleapis.com/auth/drive']

    async def upload_file(
        self, 
        name: str, 
        content: Optional[str] = None, 
        source_url: Optional[str] = None, 
        mime_type: Optional[str] = None,
        parent_folder_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Upload a file to Google Drive."""
        service = await self.get_service()
        parent_id = parent_folder_id or self.default_parent_id
        
        file_metadata = {
            'name': name,
            'parents': [parent_id]
        }
        
        import base64
        import io
        from googleapiclient.http import MediaIoBaseUpload
        import httpx
        
        media = None
        if content:
            file_data = base64.b64decode(content)
            media = MediaIoBaseUpload(io.BytesIO(file_data), mimetype=mime_type, resumable=True)
        elif source_url:
            async with httpx.AsyncClient() as client:
                resp = await client.get(source_url)
                resp.raise_for_status()
                media = MediaIoBaseUpload(io.BytesIO(resp.content), mimetype=mime_type or resp.headers.get('content-type'), resumable=True)
        
        if not media:
            file_metadata['mimeType'] = 'application/vnd.google-apps.folder'
            return service.files().create(body=file_metadata, fields='id, name').execute()
        
        return service.files().create(body=file_metadata, media_body=media, fields='id, name').execute()

    async def delete_file(self, file_id: str) -> Dict[str, Any]:
        """Delete a file from Google Drive."""
        service = await self.get_service()
        service.files().delete(fileId=file_id).execute()
        return {"success": True}

    async def list_files(
        self, folder_id: Optional[str] = None, with_link: bool = False
    ) -> List[Dict[str, Any]]:
        """List files in a Google Drive folder.

        **Args:**

        - folder_id: Optional folder ID to list files from
        - with_link: Whether to include the webViewLink for each file
        """
        service = await self.get_service()
        parent_id = folder_id or self.default_parent_id

        q = f"'{parent_id}' in parents and trashed = false"
        fields = "files(id, name, mimeType)"
        if with_link:
            fields = "files(id, name, mimeType, webViewLink)"

        results = service.files().list(q=q, fields=f"nextPageToken, {fields}").execute()

        files = results.get("files", [])

        if with_link:
            for f in files:
                if "webViewLink" in f:
                    f["url"] = f.pop("webViewLink")

        return files

    async def share_file(
        self, 
        file_id: str, 
        share_type: str = 'link', 
        link_scope: str = 'anyone',
        email: Optional[str] = None,
        role: str = 'reader'
    ) -> Dict[str, Any]:
        """Share a file on Google Drive."""
        service = await self.get_service()
        
        if share_type == 'link':
            permission = {'type': link_scope, 'role': role}
        else:
            permission = {'type': 'user', 'role': role, 'emailAddress': email}
            
        service.permissions().create(fileId=file_id, body=permission).execute()
        
        if share_type == 'link':
            file = service.files().get(fileId=file_id, fields='webViewLink').execute()
            return {'webViewLink': file.get('webViewLink')}
            
        return {'success': True}
