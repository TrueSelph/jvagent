"""API endpoints for querying database logs.

Provides endpoints for cross-referenced querying of logs by agent_id and time frame.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ValidationError
from jvspatial.logging.filter_utils import validate_log_filter
from jvspatial.logging.service import get_logging_service

logger = logging.getLogger(__name__)


@endpoint(
    "/logs/agents/{agent_id}",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["App"],
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
    filter: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> Dict[str, Any]:
    """Get logs for an agent filtered by time frame and optional MongoDB-style filter.

    Retrieves error logs for a specific agent, filtered by time range and optional filter.
    Logs are returned in reverse chronological order (most recent first).

    Args:
        agent_id: Agent node ID (required)
        start_time: Optional start time filter (ISO datetime string, e.g., "2025-01-01T00:00:00Z")
        end_time: Optional end time filter (ISO datetime string, e.g., "2025-01-31T23:59:59Z")
        filter: Optional MongoDB-style filter JSON (e.g. {"context.log_data.user_id":"123"})
        page: Page number (default: 1)
        page_size: Items per page (default: 50, max recommended: 100)

    Returns:
        Dictionary containing:
        - logs: List of log entries
        - pagination: Pagination metadata (page, page_size, total, total_pages)
    """
    # Parse and validate filter if provided
    filter_query: Optional[Dict[str, Any]] = None
    if filter:
        try:
            filter_dict = json.loads(filter)
        except json.JSONDecodeError as e:
            raise ValidationError(
                message=f"Invalid filter JSON: {e}",
                details={"filter": filter},
            ) from e
        if not isinstance(filter_dict, dict):
            raise ValidationError(
                message="Filter must be a JSON object",
                details={"filter": filter},
            )
        filter_query = validate_log_filter(filter_dict)

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

    # Get logs using jvspatial's service with agent_id and optional filter
    logging_service = get_logging_service(database_name="logs")
    result = await logging_service.get_error_logs(
        agent_id=agent_id,
        start_time=start_dt,
        end_time=end_dt,
        page=page,
        page_size=page_size,
        filter_query=filter_query,
    )

    return result
