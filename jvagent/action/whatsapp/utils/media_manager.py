"""Media Manager for WhatsApp Action using jvspatial storage."""

import datetime
import logging
from typing import Any, Dict, List, Optional

from jvagent.core.app import App
from jvspatial.storage.security import FileValidator, PathSanitizer

logger = logging.getLogger(__name__)


class MediaManager:
    """Manages WhatsApp media using jvspatial storage and security libraries."""

    def __init__(self, storage_dir: str = "whatsapp_media"):
        """Initialize MediaManager.

        Args:
            storage_dir: Root directory within storage for WhatsApp media.
        """
        self.storage_dir = PathSanitizer.sanitize_path(storage_dir)
        # Allow octet-stream as fallback when detection fails
        allowed = FileValidator.DEFAULT_ALLOWED_MIME_TYPES | {"application/octet-stream"}
        self.validator = FileValidator(
            max_size_mb=20,  # WhatsApp typical limit
            allowed_mime_types=allowed,
            strict_mime_check=False,  # Allow some flexibility
        )

    async def save_media(
        self,
        user_id: str,
        media_bytes: bytes,
        mime_type: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> Optional[str]:
        """Saves media to storage and returns the URL.

        Args:
            user_id: The ID of the user who sent/received the media.
            media_bytes: The raw bytes of the media file.
            mime_type: The MIME type of the media.
            filename: The original filename, if available.

        Returns:
            The URL to the stored media, or None if saving failed.
        """
        app = await App.get()
        if not app:
            logger.error("Could not get App instance for media storage")
            return None

        # Sanitize user_id for path usage
        safe_user_id = PathSanitizer.sanitize_path(user_id)
        
        # Validate file
        try:
            name_to_validate = filename or "media"
            # Help validator with extension if missing
            if "." not in name_to_validate and mime_type:
                import mimetypes
                ext = mimetypes.guess_extension(mime_type)
                if ext:
                    name_to_validate += ext
                    
            validation = self.validator.validate_file(
                content=media_bytes, 
                filename=name_to_validate,
                expected_mime_type=mime_type
            )
            extension = validation["extension"]
        except Exception as e:
            logger.warning(f"Media validation failed for user {user_id}: {e}")
            return None

        # Generate unique path: whatsapp_media/user_id/timestamp_uuid.ext
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        save_filename = f"{timestamp}_{unique_id}{extension}"
        storage_path = f"{self.storage_dir}/{safe_user_id}/{save_filename}"

        metadata = {
            "user_id": user_id,
            "original_filename": filename,
            "mime_type": validation["mime_type"],
            "size": validation["size_bytes"],
            "created_at": datetime.datetime.now().isoformat(),
        }

        try:
            success = await app.save_file(storage_path, media_bytes, metadata=metadata)
            if success:
                # Returns the standard URL
                return await app.get_file_url(storage_path)
            else:
                logger.error(f"Failed to save media to {storage_path}")
                return None
        except Exception as e:
            logger.error(f"Error saving media to {storage_path}: {e}")
            return None

    async def get_proxy_url(
        self, storage_path: str, expires_in: int = 3600, one_time: bool = False
    ) -> Optional[str]:
        """Creates a secure proxy URL for a piece of media.

        Args:
            storage_path: The relative path to the media in storage.
            expires_in: Expiration time in seconds.
            one_time: Whether the URL is for one-time use.

        Returns:
            A secure proxy URL string, or None if creation failed.
        """
        app = await App.get()
        if not app:
            return None

        return await app.create_proxy_url(
            path=storage_path, expires_in=expires_in, one_time=one_time
        )

    async def get_by_user_id(self, user_id: str) -> List[Dict[str, Any]]:
        """Retrieves a list of media files for a specific user.

        Args:
            user_id: The ID of the user.

        Returns:
            List of dicts containing path and url for each media file.
        """
        app = await App.get()
        if not app:
            return []

        safe_user_id = PathSanitizer.sanitize_path(user_id)
        prefix = f"{self.storage_dir}/{safe_user_id}/"
        
        file_interface = await app.get_file_interface()
        if not file_interface:
            return []

        try:
            # Using jvspatial's list_files
            files = await file_interface.list_files(prefix=prefix)
            results = []
            for f in files:
                path = f["path"]
                url = await app.get_file_url(path)
                results.append({
                    "path": path, 
                    "url": url,
                    "mime_type": f.get("content_type"),
                    "size": f.get("size"),
                    "created_at": f.get("created_at")
                })
            return results
        except Exception as e:
            logger.error(f"Error listing media for user {user_id}: {e}")
            return []

    async def delete_old_media(self, days: int = 30):
        """Deletes media older than the specified number of days using jvspatial lifecycle.

        Args:
            days: Number of days of retention.
        """
        app = await App.get()
        if not app:
            return

        file_interface = await app.get_file_interface()
        if not file_interface:
            return

        now = datetime.datetime.now()
        threshold = now - datetime.timedelta(days=days)

        try:
            # List all files in the whatsapp_media directory
            all_media = await file_interface.list_files(prefix=self.storage_dir)
            for item in all_media:
                created_at_str = item.get("created_at")
                if created_at_str:
                    try:
                        created_at = datetime.datetime.fromisoformat(created_at_str)
                        if created_at < threshold:
                            path = item["path"]
                            await app.delete_file(path)
                            logger.info(f"Deleted old media: {path} (created {created_at_str})")
                    except (ValueError, TypeError):
                        continue
        except Exception as e:
            logger.error(f"Error during media cleanup: {e}")

