"""Import graph facades for ActionLoader decomposition."""

from pathlib import Path
from typing import Optional

from . import importer as importer_module
from .importer import JvagentActionsImporter


def ensure_importer(
    base_path: Path,
    existing: Optional[JvagentActionsImporter] = None,
) -> JvagentActionsImporter:
    """Return an importer wired to ``base_path``.

    ``JvagentActionsImporter`` is registered once at module import time in
    ``importer.py``. This helper keeps backward-compatible call sites working by
    updating the shared base path and returning the existing global importer.
    """
    if existing is not None:
        return existing
    importer_module._actions_importer_base_path = base_path
    return importer_module._actions_importer
