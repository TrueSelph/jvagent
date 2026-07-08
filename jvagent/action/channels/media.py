"""Channel media storage using jvspatial file interface."""

import datetime
import logging
from typing import Any, Dict, List, Optional

from jvspatial.storage.security import FileValidator, PathSanitizer

from jvagent.core.app import App

logger = logging.getLogger(__name__)


class MediaManager:
    """Stores inbound channel media using jvspatial storage and security libraries."""

    def __init__(self, storage_dir: str = "whatsapp_media"):
        self.storage_dir = PathSanitizer.sanitize_path(storage_dir)
        allowed = FileValidator.DEFAULT_ALLOWED_MIME_TYPES | {
            "application/octet-stream"
        }
        self.validator = FileValidator(
            max_size_mb=20,
            allowed_mime_types=allowed,
            strict_mime_check=False,
        )

    async def save_media(
        self,
        user_id: str,
        media_bytes: bytes,
        mime_type: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> Optional[str]:
        app = await App.get()
        if not app:
            logger.error("Could not get App instance for media storage")
            return None

        safe_user_id = PathSanitizer.sanitize_path(user_id)

        try:
            name_to_validate = filename or "media"
            if "." not in name_to_validate and mime_type:
                import mimetypes

                ext = mimetypes.guess_extension(mime_type)
                if ext:
                    name_to_validate += ext

            validation = self.validator.validate_file(
                content=media_bytes,
                filename=name_to_validate,
                expected_mime_type=mime_type,
            )
            extension = validation["extension"]
        except Exception as e:
            logger.debug("Media validation failed for user %s: %s", user_id, e)
            return None

        now_dt = await app.now()
        timestamp = now_dt.strftime("%Y%m%d_%H%M%S")
        import uuid

        unique_id = uuid.uuid4().hex[:8]
        save_filename = f"{timestamp}_{unique_id}{extension}"
        storage_path = f"{self.storage_dir}/{safe_user_id}/{save_filename}"

        metadata = {
            "user_id": user_id,
            "original_filename": filename,
            "mime_type": validation["mime_type"],
            "size": validation["size_bytes"],
            "created_at": now_dt.isoformat(),
        }

        try:
            success = await app.save_file(storage_path, media_bytes, metadata=metadata)
            if success:
                return await app.get_file_url(storage_path)
            logger.error("Failed to save media to %s", storage_path)
            return None
        except Exception as e:
            logger.error("Error saving media to %s: %s", storage_path, e)
            return None

    async def get_proxy_url(
        self, storage_path: str, expires_in: int = 3600, one_time: bool = False
    ) -> Optional[str]:
        app = await App.get()
        if not app:
            return None
        return await app.create_proxy_url(
            path=storage_path, expires_in=expires_in, one_time=one_time
        )

    async def get_by_user_id(self, user_id: str) -> List[Dict[str, Any]]:
        app = await App.get()
        if not app:
            return []

        safe_user_id = PathSanitizer.sanitize_path(user_id)
        prefix = f"{self.storage_dir}/{safe_user_id}/"

        file_interface = await app.get_file_interface()
        if not file_interface:
            return []

        try:
            files = await file_interface.list_files(prefix=prefix)
            results = []
            for f in files:
                path = f["path"]
                url = await app.get_file_url(path)
                results.append(
                    {
                        "path": path,
                        "url": url,
                        "mime_type": f.get("content_type"),
                        "size": f.get("size"),
                        "created_at": f.get("created_at"),
                    }
                )
            return results
        except Exception as e:
            logger.error("Error listing media for user %s: %s", user_id, e)
            return []

    async def delete_old_media(self, days: int = 30) -> None:
        app = await App.get()
        if not app:
            return

        file_interface = await app.get_file_interface()
        if not file_interface:
            return

        now = await app.now()
        threshold = now - datetime.timedelta(days=days)

        try:
            all_media = await file_interface.list_files(prefix=self.storage_dir)
            for item in all_media:
                created_at_str = item.get("created_at")
                if created_at_str:
                    try:
                        created_at = datetime.datetime.fromisoformat(created_at_str)
                        if created_at < threshold:
                            path = item["path"]
                            await app.delete_file(path)
                            logger.debug(
                                "Deleted old media: %s (created %s)",
                                path,
                                created_at_str,
                            )
                    except (ValueError, TypeError):
                        continue
        except Exception as e:
            logger.error("Error during media cleanup: %s", e)
