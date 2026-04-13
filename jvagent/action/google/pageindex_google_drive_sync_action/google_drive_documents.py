from typing import Any, Dict, List

from jvspatial.core import Node
from jvspatial.core.annotations import attribute


class GoogleDriveDocuments(Node):
    """Rank profile node."""

    folder_id: str = attribute(
        default="",
        description="ID of the Google Drive folder to index",
    )

    files: List[Dict[str, Any]] = attribute(
        default_factory=list,
        description="The list of files and subfolder found in the folder",
    )

    metadata: Dict[str, Any] = attribute(
        default_factory=dict,
        description="Metadata for documents",
    )

    status: str = attribute(
        default="pending",
        description="Status of the document. (pending, processing, completed, failed)",
    )

    ingesting_documents: Dict[str, Any] = attribute(
        default={"added": [], "modified": [], "removed": []},
        description="contain a list of added, modified and deleted documents",
    )

    failed_documents: Dict[str, Any] = attribute(
        default={"added": [], "modified": [], "removed": []},
        description="contain a list of failed documents",
    )

    active_document: str = attribute(
        default_factory=str,
        description="Document which is currently being processed",
    )
