"""API endpoints for querying database logs.

Provides endpoints for cross-referenced querying of logs by agent_id and time frame.
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ValidationError
from jvspatial.logging.service import get_logging_service

logger = logging.getLogger(__name__)


@endpoint(
    "/api/logs/agents/{agent_id}",
    methods=["GET"],
    auth=True,
    tags=["Logging"],
    response=success_response(
        data={
            "errors": ResponseField(
                field_type=list,
                description="Error log entries (reverse chronological)",
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
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> Dict[str, Any]:
    """Get logs for an agent filtered by time frame.

    Retrieves error logs for a specific agent, filtered by time range.
    Logs are returned in reverse chronological order (most recent first).

    Args:
        agent_id: Agent node ID (required)
        start_time: Optional start time filter (ISO datetime string, e.g., "2025-01-01T00:00:00Z")
        end_time: Optional end time filter (ISO datetime string, e.g., "2025-01-31T23:59:59Z")
        page: Page number (default: 1)
        page_size: Items per page (default: 50, max recommended: 100)

    Returns:
        Dictionary containing:
        - logs: List of log entries
        - pagination: Pagination metadata (page, page_size, total, total_pages)
    """
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

    # Get logs using jvspatial's service with agent_id filter
    logging_service = get_logging_service(database_name="logs")
    result = await logging_service.get_error_logs(
        agent_id=agent_id,
        start_time=start_dt,
        end_time=end_dt,
        page=page,
        page_size=page_size,
    )

    return result



