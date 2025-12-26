"""API endpoints for logging system."""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError

from jvagent.core.app import App
from jvagent.logging.service import get_logging_service
from jvagent.logging.archive import get_archive_service
from jvagent.logging.retention import get_retention_task

logger = logging.getLogger(__name__)


@endpoint(
    "/logs/agents/{agent_id}/logs",
    methods=["GET"],
    auth=True,
    tags=["Logging"],
    response=success_response(
        data={
            "conversations": ResponseField(
                field_type=list,
                description="Logs grouped by conversation (reverse chronological)",
            ),
            "pagination": ResponseField(
                field_type=Dict[str, Any],
                description="Pagination metadata",
            ),
        }
    ),
)
async def get_logs_by_agent(
    agent_id: str,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> Dict[str, Any]:
    """Get logs for an agent with filtering and pagination.

    Retrieves interaction logs for a specific agent, grouped by conversation.
    Logs are returned in reverse chronological order (most recent first),
    with interactions within each conversation in chronological order.

    Args:
        agent_id: Agent node ID (required)
        user_id: Optional user ID filter
        conversation_id: Optional conversation ID filter
        session_id: Optional session ID filter
        start_time: Optional start time filter (ISO datetime string, e.g., "2025-01-01T00:00:00Z")
        end_time: Optional end time filter (ISO datetime string, e.g., "2025-01-31T23:59:59Z")
        page: Page number (default: 1)
        page_size: Items per page (default: 50, max recommended: 100)

    Returns:
        Dictionary containing:
        - conversations: List of conversation objects, each containing:
          - conversation_id: Conversation identifier
          - interactions: List of interaction log entries
        - pagination: Pagination metadata (page, page_size, total, total_pages)
    """
    # Check if logging is enabled (global and app-level)
    from jvagent.logging.config import get_logging_config
    config = get_logging_config()
    if not config.get("enabled", True):
        raise ResourceNotFoundError(
            message="Logging is disabled",
            details={"reason": "Global logging is disabled"},
        )
    
    # Check app-level logging setting
    app = await App.get()
    if app and not app.logging_enabled:
        raise ResourceNotFoundError(
            message="Logging is disabled for this application",
            details={"reason": "App-level logging is disabled", "app_id": app.id},
        )
    
    # Validate agent exists
    from jvagent.core.agent import Agent
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
        )

    # Parse datetime strings
    start_dt = None
    end_dt = None
    if start_time:
        try:
            start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        except ValueError:
            raise ValidationError(
                message=f"Invalid start_time format: {start_time}",
                details={"start_time": start_time},
            )
    if end_time:
        try:
            end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        except ValueError:
            raise ValidationError(
                message=f"Invalid end_time format: {end_time}",
                details={"end_time": end_time},
            )

    # Get logs
    logging_service = get_logging_service()
    result = await logging_service.get_logs(
        agent_id=agent_id,
        user_id=user_id,
        conversation_id=conversation_id,
        session_id=session_id,
        start_time=start_dt,
        end_time=end_dt,
        page=page,
        page_size=page_size,
    )

    return result


@endpoint(
    "/logs/agents/{agent_id}/archive",
    methods=["POST"],
    auth=True,
    tags=["Logging"],
    response=success_response(
        data={
            "archived": ResponseField(field_type=bool, description="Whether archive succeeded"),
            "record_count": ResponseField(field_type=int, description="Number of records archived"),
            "file_path": ResponseField(field_type=str, description="Path to archive file"),
        }
    ),
)
async def archive_logs_by_agent(
    agent_id: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    export_format: str = "json",
    storage_location: Optional[str] = None,
) -> Dict[str, Any]:
    """Archive logs for an agent by exporting and deleting from database.

    Exports matching logs to a file (JSON or CSV format) and then deletes
    them from the logging database. This is useful for long-term storage
    and compliance requirements.

    Args:
        agent_id: Agent node ID (required)
        start_time: Optional start time filter (ISO datetime string, e.g., "2025-01-01T00:00:00Z")
        end_time: Optional end time filter (ISO datetime string, e.g., "2025-01-31T23:59:59Z")
        user_id: Optional user ID filter
        conversation_id: Optional conversation ID filter
        export_format: Export format - "json" or "csv" (default: "json")
        storage_location: Optional storage location. If not provided, uses default
            archive path from configuration. Supports local file paths or S3 paths.

    Returns:
        Dictionary containing:
        - archived: Boolean indicating success
        - record_count: Number of records archived
        - file_path: Path to the archive file
        - export_format: Format used for export
        - timestamp: ISO timestamp of archive operation
        - filters: Dictionary of filters applied
        - deleted_count: Number of records deleted from database
    """
    # Check if logging is enabled (global and app-level)
    from jvagent.logging.config import get_logging_config
    config = get_logging_config()
    if not config.get("enabled", True):
        raise ResourceNotFoundError(
            message="Logging is disabled",
            details={"reason": "Global logging is disabled"},
        )
    
    # Check app-level logging setting
    app = await App.get()
    if app and not app.logging_enabled:
        raise ResourceNotFoundError(
            message="Logging is disabled for this application",
            details={"reason": "App-level logging is disabled", "app_id": app.id},
        )
    
    # Validate agent exists
    from jvagent.core.agent import Agent
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
        )

    # Validate export format
    if export_format not in ["json", "csv"]:
        raise ValidationError(
            message=f"Invalid export_format: {export_format}. Must be 'json' or 'csv'",
            details={"export_format": export_format},
        )

    # Parse datetime strings
    start_dt = None
    end_dt = None
    if start_time:
        try:
            start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        except ValueError:
            raise ValidationError(
                message=f"Invalid start_time format: {start_time}",
                details={"start_time": start_time},
            )
    if end_time:
        try:
            end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        except ValueError:
            raise ValidationError(
                message=f"Invalid end_time format: {end_time}",
                details={"end_time": end_time},
            )

    # Archive logs
    archive_service = get_archive_service()
    result = await archive_service.archive_logs(
        agent_id=agent_id,
        user_id=user_id,
        conversation_id=conversation_id,
        start_time=start_dt,
        end_time=end_dt,
        export_format=export_format,
        storage_location=storage_location,
    )

    return result


@endpoint(
    "/logs/agents/{agent_id}/purge",
    methods=["POST"],
    auth=True,
    tags=["Logging"],
    response=success_response(
        data={
            "deleted": ResponseField(field_type=int, description="Number of records deleted"),
        }
    ),
)
async def purge_logs_by_agent(
    agent_id: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    confirm: bool = False,
) -> Dict[str, Any]:
    """Purge logs matching criteria for an agent.

    Permanently deletes logs from the database matching the specified criteria.
    This operation cannot be undone. Use with caution.

    Args:
        agent_id: Agent node ID (required)
        start_time: Optional start time filter (ISO datetime string, e.g., "2025-01-01T00:00:00Z")
        end_time: Optional end time filter (ISO datetime string, e.g., "2025-01-31T23:59:59Z")
        user_id: Optional user ID filter
        conversation_id: Optional conversation ID filter
        confirm: Safety flag - must be set to True to proceed with deletion.
            This prevents accidental data loss.

    Returns:
        Dictionary containing:
        - deleted: Number of records deleted
        - error: Error message if deletion failed (optional)
    """
    # Check if logging is enabled (global and app-level)
    from jvagent.logging.config import get_logging_config
    config = get_logging_config()
    if not config.get("enabled", True):
        raise ResourceNotFoundError(
            message="Logging is disabled",
            details={"reason": "Global logging is disabled"},
        )
    
    # Check app-level logging setting
    app = await App.get()
    if app and not app.logging_enabled:
        raise ResourceNotFoundError(
            message="Logging is disabled for this application",
            details={"reason": "App-level logging is disabled", "app_id": app.id},
        )
    
    if not confirm:
        raise ValidationError(
            message="confirm parameter must be True to proceed with purge",
            details={"confirm": confirm},
        )

    # Validate agent exists
    from jvagent.core.agent import Agent
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
        )

    # Parse datetime strings
    start_dt = None
    end_dt = None
    if start_time:
        try:
            start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        except ValueError:
            raise ValidationError(
                message=f"Invalid start_time format: {start_time}",
                details={"start_time": start_time},
            )
    if end_time:
        try:
            end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        except ValueError:
            raise ValidationError(
                message=f"Invalid end_time format: {end_time}",
                details={"end_time": end_time},
            )

    # Purge logs
    logging_service = get_logging_service()
    result = await logging_service.purge_logs(
        agent_id=agent_id,
        user_id=user_id,
        conversation_id=conversation_id,
        start_time=start_dt,
        end_time=end_dt,
    )

    return result


@endpoint(
    "/logs/applications/{app_id}/retention",
    methods=["GET"],
    auth=True,
    tags=["Logging"],
    response=success_response(
        data={
            "retention_days": ResponseField(field_type=int, description="Retention period in days"),
        }
    ),
)
async def get_retention(app_id: str) -> Dict[str, Any]:
    """Get retention configuration for an application.

    Retrieves the current log retention policy for the specified application.
    The retention period determines how long logs are kept before automatic
    cleanup via the retention task.

    Args:
        app_id: Application node ID (required)

    Returns:
        Dictionary containing:
        - retention_days: Number of days logs are retained (0 means indefinite)
    """
    # Validate app exists
    app = await App.get()
    if not app or app.id != app_id:
        raise ResourceNotFoundError(
            message=f"Application with ID '{app_id}' not found",
            details={"app_id": app_id},
        )

    return {"retention_days": app.log_retention_days}


@endpoint(
    "/logs/applications/{app_id}/retention",
    methods=["PUT"],
    auth=True,
    tags=["Logging"],
    response=success_response(
        data={
            "retention_days": ResponseField(field_type=int, description="Updated retention period in days"),
            "message": ResponseField(field_type=str, description="Success message"),
        }
    ),
)
async def set_retention(app_id: str, retention_days: int) -> Dict[str, Any]:
    """Set retention configuration for an application.

    Updates the log retention policy for the specified application.
    The retention task will automatically purge logs older than the
    specified number of days.

    Args:
        app_id: Application node ID (required)
        retention_days: Retention period in days (minimum: 1).
            Set to 0 for indefinite retention (not recommended for production).

    Returns:
        Dictionary containing:
        - retention_days: Updated retention period in days
        - message: Success confirmation message
    """
    if retention_days < 1:
        raise ValidationError(
            message="retention_days must be at least 1",
            details={"retention_days": retention_days},
        )

    # Validate app exists
    app = await App.get()
    if not app or app.id != app_id:
        raise ResourceNotFoundError(
            message=f"Application with ID '{app_id}' not found",
            details={"app_id": app_id},
        )

    # Update retention
    app.log_retention_days = retention_days
    await app.save()

    return {
        "retention_days": app.log_retention_days,
        "message": "Retention configuration updated successfully",
    }

