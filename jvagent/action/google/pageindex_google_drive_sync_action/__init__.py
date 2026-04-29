"""PageIndex Google Drive sync action package."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .pageindex_google_drive_sync_action import PageIndexGoogleDriveSyncAction

__all__ = ["PageIndexGoogleDriveSyncAction"]


def __getattr__(name: str):
    if name == "PageIndexGoogleDriveSyncAction":
        from .pageindex_google_drive_sync_action import PageIndexGoogleDriveSyncAction

        return PageIndexGoogleDriveSyncAction
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
