from jvspatial.core import Node
from typing import List, Dict, Any
from jvspatial.core.annotations import attribute

class GoogleDriveDocuments(Node):
    """Rank profile node."""
    
    folder_id: str = attribute(
        default="",
        description="ID of the Google Drive folder to index",
    )

    files: list[dict, Any] = attribute(
        default_factory=list,
        description="The list of files and subfolder found in the folder",
    )

    metadata: Dict[str, Any] = attribute(
        default_factory=dict,
        description="Metadata for documents",
    )