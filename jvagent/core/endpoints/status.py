"""Aggregate app status endpoint."""

from __future__ import annotations

from typing import Any, Dict

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response

from jvagent.core.agents import get_status as get_agents_status


@endpoint(
    "/status",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["App"],
    response=success_response(
        data={
            "statistics": ResponseField(
                field_type=Dict[str, Any],
                description="Comprehensive statistics about all agents",
            )
        }
    ),
)
async def get_status(sync: bool = False, include_health: bool = True) -> Dict[str, Any]:
    """Get aggregate status across all agents."""
    return await get_agents_status(sync=sync, include_health=include_health)
