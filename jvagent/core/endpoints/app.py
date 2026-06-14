"""App-level admin endpoints (e.g. update_mode)."""

from __future__ import annotations

from typing import Any, Dict

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError

from jvagent.core.app import App, set_app_update_mode
from jvagent.core.bootstrap_update_mode import validate_admin_update_mode


@endpoint(
    "/app/update_mode",
    methods=["PUT"],
    auth=True,
    roles=["admin"],
    tags=["App"],
    response=success_response(
        data={
            "update_mode": ResponseField(
                field_type=str,
                description="Persisted next-start mode: run, merge, or source",
                example="merge",
            ),
            "message": ResponseField(
                field_type=str,
                description="Confirmation",
                example="update_mode set to merge",
            ),
        }
    ),
)
async def put_app_update_mode(update_mode: str) -> Dict[str, Any]:
    """Set ``App.update_mode`` for the next process start (admin only).

    Use ``merge`` or ``source`` to trigger YAML sync on the next ``jvagent run``
    / ``jvagent bootstrap`` when the CLI does not pass ``--update``. After a
    successful startup the value is reset to ``run`` automatically.
    """
    try:
        validated = validate_admin_update_mode(update_mode)
    except ValueError as e:
        raise ValidationError(message=str(e)) from e

    app = await App.get()
    if not app:
        raise ResourceNotFoundError(message="Application not found")

    await set_app_update_mode(app, validated)
    return {
        "update_mode": validated,
        "message": f"update_mode set to {validated}",
    }
