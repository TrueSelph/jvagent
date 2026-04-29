"""Optional Google Drive upload for generated PDFs."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Dict, Optional

from ._document_args import DocumentPdfParams, default_drive_filename


async def upload_pdf_to_drive_if_configured(
    visitor: Any,
    pdf_path: Path,
    params: DocumentPdfParams,
) -> Optional[Dict[str, Any]]:
    """Upload PDF to Drive when drive_output_folder_id is set. Returns None if skipped."""
    if not params.drive_output_folder_id:
        return None

    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    drive_action = await resolver.resolve("GoogleDriveAction")
    if drive_action is None:
        return {"error": "GoogleDriveAction not available"}

    try:
        pdf_content = pdf_path.read_bytes()
        encoded = base64.b64encode(pdf_content).decode("utf-8")
        filename = default_drive_filename(params)

        result = await drive_action.upload_file(
            name=filename,
            content=encoded,
            mime_type="application/pdf",
            parent_folder_id=params.drive_output_folder_id,
        )

        file_id = result.get("id")
        return {
            "file_id": file_id,
            "filename": filename,
            "url": (
                f"https://drive.google.com/file/d/{file_id}/view" if file_id else None
            ),
        }
    except Exception as e:
        return {"error": str(e)}
