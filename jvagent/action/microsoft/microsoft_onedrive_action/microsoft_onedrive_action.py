import base64
import json
import logging
from typing import Annotated, Any, ClassVar, Dict, List, Optional

import httpx
from jvspatial.env import env

from jvagent.tooling.tool_decorator import tool

from ..microsoft_action import MicrosoftAction

logger = logging.getLogger(__name__)

FOLDER_MIME = "application/vnd.microsoft.graph.folder"


def _default_parent_path() -> str:
    return (env("ONEDRIVE_PARENT_FOLDER_ID") or "root").strip()


class MicrosoftOneDriveAction(MicrosoftAction):
    """OneDrive / SharePoint personal drive via Microsoft Graph."""

    SCOPES: ClassVar[List[str]] = [
        "offline_access",
        "User.Read",
        "Files.ReadWrite.All",
    ]

    def _parent_segment(self, parent_folder_id: Optional[str]) -> str:
        pid = parent_folder_id or _default_parent_path()
        if pid in ("root",):
            return "root"
        return f"items/{pid}"

    async def upload_file(
        self,
        name: str,
        content: Optional[str] = None,
        source_url: Optional[str] = None,
        mime_type: Optional[str] = None,
        parent_folder_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        parent_seg = self._parent_segment(parent_folder_id)
        if not content and not source_url:
            payload = {
                "name": name,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "rename",
            }
            return await self.graph_json(
                "POST",
                f"/me/drive/{parent_seg}/children",
                json_body=payload,
            )

        body_bytes: bytes
        ct = mime_type or "application/octet-stream"
        if content:
            body_bytes = base64.b64decode(content)
        else:
            async with httpx.AsyncClient() as client:
                r = await client.get(source_url or "")
                r.raise_for_status()
                body_bytes = r.content
                ct = mime_type or r.headers.get("content-type") or ct

        path_enc = name.replace("'", "''")
        url_path = f"/me/drive/{parent_seg}:/{path_enc}:/content"
        resp = await self.graph_request(
            "PUT",
            url_path,
            content=body_bytes,
            headers={"Content-Type": ct},
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Upload failed {resp.status_code}: {resp.text[:400]}")
        meta = resp.json()
        return {"id": meta.get("id"), "name": meta.get("name"), "mimeType": ct}

    async def delete_file(self, file_id: str) -> bool:
        await self.graph_json("DELETE", f"/me/drive/items/{file_id}", ok=(204,))
        return True

    async def list_files(
        self,
        folder_id: Optional[str] = None,
        with_link: bool = False,
        depth: int = 5,
    ) -> List[Dict[str, Any]]:
        if depth < 0:
            return []
        parent = folder_id or _default_parent_path()
        seg = "root" if parent in ("root",) else f"items/{parent}"
        resp = await self.graph_request(
            "GET",
            f"/me/drive/{seg}/children",
            params={"$select": "id,name,folder,file,webUrl,lastModifiedDateTime,size"},
        )
        if resp.status_code != 200:
            raise RuntimeError(resp.text[:400])
        data = resp.json()
        items = data.get("value") or []
        out: List[Dict[str, Any]] = []
        for it in items:
            is_folder = "folder" in it
            entry: Dict[str, Any] = {
                "id": it.get("id"),
                "name": it.get("name"),
                "mimeType": (
                    FOLDER_MIME
                    if is_folder
                    else (it.get("file") or {}).get(
                        "mimeType", "application/octet-stream"
                    )
                ),
            }
            if it.get("lastModifiedDateTime"):
                entry["modifiedTime"] = it.get("lastModifiedDateTime")
            if with_link and it.get("webUrl"):
                entry["url"] = it.get("webUrl")
            if is_folder and depth > 0:
                entry["files"] = await self.list_files(
                    folder_id=it.get("id"),
                    with_link=with_link,
                    depth=depth - 1,
                )
            elif is_folder:
                entry["files"] = []
            out.append(entry)
        return out

    async def share_file(
        self,
        file_id: str,
        share_type: str = "link",
        link_scope: str = "anyone",
        email: Optional[str] = None,
        role: str = "read",
    ) -> Dict[str, Any]:
        if share_type == "link":
            stype = "view" if role in ("reader", "read", "view") else "edit"
            scope = "anonymous" if link_scope == "anyone" else "organization"
            body = {"type": stype, "scope": scope}
            data = await self.graph_json(
                "POST",
                f"/me/drive/items/{file_id}/createLink",
                json_body=body,
            )
            link_url = (data or {}).get("link", {}).get("webUrl")
            if link_url:
                return {"webViewLink": link_url}
            return {"success": True, "result": data}
        invite_role = "read" if role in ("reader", "read") else "write"
        payload = {
            "recipients": [{"email": email}],
            "roles": [invite_role],
        }
        data = await self.graph_json(
            "POST",
            f"/me/drive/items/{file_id}/invite",
            json_body=payload,
        )
        return {"success": True, "result": data}

    def compare_files(
        self,
        old_files: List[Dict[str, Any]],
        new_files: List[Dict[str, Any]],
        ignore_folders: bool = True,
    ) -> Dict[str, List[Dict[str, Any]]]:
        def flatten_to_dict(
            items: List[Dict[str, Any]], lookup: Optional[Dict[str, Any]] = None
        ) -> Dict[str, Any]:
            if lookup is None:
                lookup = {}
            for item in items:
                item_copy = {k: v for k, v in item.items() if k != "files"}
                lookup[item["id"]] = item_copy
                if item.get("files"):
                    flatten_to_dict(item["files"], lookup)
            return lookup

        old_map = flatten_to_dict(old_files)
        new_map = flatten_to_dict(new_files)
        old_ids = set(old_map.keys())
        new_ids = set(new_map.keys())

        added: List[Dict[str, Any]] = []
        for fid in new_ids - old_ids:
            if new_map[fid].get("mimeType") == FOLDER_MIME and ignore_folders:
                continue
            added.append(new_map[fid])

        removed: List[Dict[str, Any]] = []
        for fid in old_ids - new_ids:
            if old_map[fid].get("mimeType") == FOLDER_MIME and ignore_folders:
                continue
            removed.append(old_map[fid])

        modified: List[Dict[str, Any]] = []
        for fid in old_ids & new_ids:
            if old_map[fid].get("mimeType") == FOLDER_MIME and ignore_folders:
                continue
            if old_map[fid] != new_map[fid]:
                modified.append({"id": fid, "old": old_map[fid], "new": new_map[fid]})

        return {"added": added, "removed": removed, "modified": modified}

    @tool(name="onedrive__list_files")
    async def _t_list_files(
        self,
        folder_id: Annotated[
            Optional[str], "ID of the folder to list (root if omitted)."
        ] = None,
        with_link: Annotated[
            Optional[bool], "Include sharing links (default: false)."
        ] = None,
        depth: Annotated[
            Optional[int], "Recursion depth for subfolders (default: 5)."
        ] = None,
    ) -> str:
        """List files and folders in a OneDrive folder."""
        results = await self.list_files(
            folder_id=(folder_id or "") or None,
            with_link=with_link if with_link is not None else False,
            depth=depth if depth is not None else 5,
        )
        return json.dumps(results, indent=2)

    @tool(name="onedrive__upload_file")
    async def _t_upload_file(
        self,
        name: Annotated[str, "Name for the uploaded file."],
        content: Annotated[
            Optional[str], "Base64-encoded file content (use this or source_url)."
        ] = None,
        source_url: Annotated[
            Optional[str],
            "URL to download file content from (use this or content).",
        ] = None,
        mime_type: Annotated[Optional[str], "MIME type of the file."] = None,
        parent_folder_id: Annotated[Optional[str], "ID of the parent folder."] = None,
    ) -> str:
        """Upload a file to OneDrive."""
        result = await self.upload_file(
            name=name,
            content=(content or "") or None,
            source_url=(source_url or "") or None,
            mime_type=(mime_type or "") or None,
            parent_folder_id=(parent_folder_id or "") or None,
        )
        return json.dumps(result, indent=2)

    @tool(name="onedrive__share_file")
    async def _t_share_file(
        self,
        file_id: Annotated[str, "The ID of the file to share."],
        share_type: Annotated[
            Optional[str], "Type of sharing: 'link' or 'user' (default: 'link')."
        ] = None,
        link_scope: Annotated[
            Optional[str],
            "Link scope: 'anyone' or 'organization' (default: 'anyone'). "
            "Only used when share_type='link'.",
        ] = None,
        email: Annotated[
            Optional[str],
            "Email address for user-level sharing. " "Required when share_type='user'.",
        ] = None,
        role: Annotated[
            Optional[str], "Permission role: 'read' or 'write' (default: 'read')."
        ] = None,
    ) -> str:
        """Share a OneDrive file by creating a shareable link or granting access to a specific user. Use share_type='link' for public/organization links or share_type='user' to invite a specific email address."""  # noqa: E501
        result = await self.share_file(
            file_id=file_id,
            share_type=share_type if share_type is not None else "link",
            link_scope=link_scope if link_scope is not None else "anyone",
            email=(email or "") or None,
            role=role if role is not None else "read",
        )
        return json.dumps(result, indent=2)

    @tool(name="onedrive__delete_file")
    async def _t_delete_file(
        self,
        file_id: Annotated[str, "The ID of the file to delete."],
    ) -> str:
        """Permanently delete a file from OneDrive."""
        deleted = await self.delete_file(file_id=file_id)
        return json.dumps({"deleted": deleted}, indent=2)
