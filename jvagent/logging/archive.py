"""Archive service for exporting and deleting logs."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from jvagent.logging.service import LoggingService

logger = logging.getLogger(__name__)


class ArchiveService:
    """Service for archiving logs to external storage."""

    def __init__(self, logging_service: Optional[LoggingService] = None):
        """Initialize the archive service.

        Args:
            logging_service: Optional LoggingService instance. If not provided, creates one.
        """
        if logging_service is None:
            from jvagent.logging.service import get_logging_service
            logging_service = get_logging_service()
        self.logging_service = logging_service

    async def archive_logs(
        self,
        agent_id: str,
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        export_format: str = "json",
        storage_location: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Archive logs by exporting them and deleting from database.

        Args:
            agent_id: Agent node ID (required)
            user_id: Optional user ID filter
            conversation_id: Optional conversation ID filter
            start_time: Optional start time filter
            end_time: Optional end time filter
            export_format: Export format ("json" or "csv")
            storage_location: Optional storage location (file path or S3 path).
                            If not provided, uses default location.

        Returns:
            Dictionary with archive metadata
        """
        try:
            # Get logs to archive
            logs_result = await self.logging_service.get_logs(
                agent_id=agent_id,
                user_id=user_id,
                conversation_id=conversation_id,
                start_time=start_time,
                end_time=end_time,
                page=1,
                page_size=10000,  # Get all matching logs
            )

            conversations = logs_result.get("conversations", [])
            if not conversations:
                return {
                    "archived": False,
                    "record_count": 0,
                    "message": "No logs found matching criteria",
                }

            # Count total interactions
            total_interactions = sum(
                len(conv.get("interactions", [])) for conv in conversations
            )

            # Export logs
            if export_format == "json":
                export_data = await self._export_json(conversations)
            elif export_format == "csv":
                export_data = await self._export_csv(conversations)
            else:
                raise ValueError(f"Unsupported export format: {export_format}")

            # Determine storage location
            if not storage_location:
                # Use default location
                from jvagent.logging.config import get_logging_config
                config = get_logging_config()
                archive_path = config.get("archive_default_path", "./logs_archive")
                storage_location = f"{archive_path}/archive_{agent_id}_{datetime.now().isoformat().replace(':', '-')}.{export_format}"

            # Save to storage
            if storage_location.startswith("s3://"):
                file_path = await self._save_to_s3(export_data, storage_location, export_format)
            else:
                file_path = await self._save_to_local(export_data, storage_location, export_format)

            # Delete archived logs from database
            purge_result = await self.logging_service.purge_logs(
                agent_id=agent_id,
                user_id=user_id,
                conversation_id=conversation_id,
                start_time=start_time,
                end_time=end_time,
            )

            return {
                "archived": True,
                "record_count": total_interactions,
                "file_path": file_path,
                "export_format": export_format,
                "timestamp": datetime.now().isoformat(),
                "filters": {
                    "agent_id": agent_id,
                    "user_id": user_id,
                    "conversation_id": conversation_id,
                    "start_time": start_time.isoformat() if start_time else None,
                    "end_time": end_time.isoformat() if end_time else None,
                },
                "deleted_count": purge_result.get("deleted", 0),
            }

        except Exception as e:
            logger.error(f"Failed to archive logs: {e}", exc_info=True)
            return {
                "archived": False,
                "error": str(e),
            }

    async def _export_json(self, conversations: List[Dict[str, Any]]) -> str:
        """Export conversations to JSON format.

        Args:
            conversations: List of conversation dictionaries

        Returns:
            JSON string
        """
        export_data = {
            "exported_at": datetime.now().isoformat(),
            "format_version": "1.0",
            "conversations": conversations,
        }
        return json.dumps(export_data, indent=2, default=str)

    async def _export_csv(self, conversations: List[Dict[str, Any]]) -> str:
        """Export conversations to CSV format.

        Args:
            conversations: List of conversation dictionaries

        Returns:
            CSV string
        """
        import csv
        from io import StringIO

        output = StringIO()
        writer = csv.writer(output)

        # Write header
        writer.writerow([
            "conversation_id",
            "interaction_id",
            "logged_at",
            "user_id",
            "session_id",
            "utterance",
            "response",
        ])

        # Write rows
        for conv in conversations:
            conv_id = conv.get("conversation_id", "")
            for interaction in conv.get("interactions", []):
                interaction_data = interaction.get("interaction_data", {})
                context = interaction_data.get("context", {})
                writer.writerow([
                    conv_id,
                    interaction.get("interaction_id", ""),
                    interaction.get("logged_at", ""),
                    context.get("user_id", ""),
                    context.get("session_id", ""),
                    context.get("utterance", ""),
                    context.get("response", ""),
                ])

        return output.getvalue()

    async def _save_to_local(self, data: str, file_path: str, format: str) -> str:
        """Save export data to local file system.

        Args:
            data: Export data as string
            file_path: File path to save to
            format: File format (for extension)

        Returns:
            Absolute file path
        """
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            f.write(data)

        return str(path.absolute())

    async def _save_to_s3(self, data: str, s3_path: str, format: str) -> str:
        """Save export data to S3.

        Args:
            data: Export data as string
            s3_path: S3 path (s3://bucket/key)
            format: File format (for extension)

        Returns:
            S3 path
        """
        # TODO: Implement S3 storage
        # For now, raise NotImplementedError
        raise NotImplementedError("S3 storage not yet implemented")


# Singleton instance
_archive_service: Optional[ArchiveService] = None


def get_archive_service() -> ArchiveService:
    """Get the singleton archive service instance.

    Returns:
        ArchiveService instance
    """
    global _archive_service
    if _archive_service is None:
        _archive_service = ArchiveService()
    return _archive_service

