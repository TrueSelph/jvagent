"""Access Control endpoints."""

import json
import logging
from typing import Any, Dict

from jvspatial.api import endpoint
from jvspatial.api.decorators import EndpointField
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError

from .access_control_action import AccessControlAction

logger = logging.getLogger(__name__)


@endpoint(
    "/actions/{action_id}/access",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Access Control"],
    response=success_response(
        data={
            "has_access": ResponseField(
                field_type=bool,
                description="Whether the session has access to the action",
                example=True,
            ),
        }
    ),
)
async def check_action_access_endpoint(
    action_id: str,
    session_id: str,
    action_label: str = "all",
    channel: str = "default",
) -> Dict[str, bool]:
    """Check if session has access to action."""
    action = await AccessControlAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(f"AccessControlAction not found: {action_id}")

    has_access = await action.has_action_access(session_id, action_label, channel)
    return {"has_access": has_access}


@endpoint(
    "/actions/{action_id}/config/export",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Access Control"],
    response=success_response(
        data={
            "config": ResponseField(
                field_type=dict,
                description="Access control configuration",
            ),
        }
    ),
)
async def export_config_endpoint(
    action_id: str,
    format: str = "json",  # Using simple default to avoid FastAPI leading error with EndpointField in GET
) -> Dict[str, Any]:
    """Export access control configuration."""
    action = await AccessControlAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(f"AccessControlAction not found: {action_id}")

    config = action.export_config()

    if format.lower() == "yaml":
        try:
            import yaml

            config_str = yaml.dump(config, default_flow_style=False)
            return {"config": config_str, "format": "yaml"}
        except ImportError:
            logger.warning("PyYAML not available, falling back to JSON")

    return {"config": config, "format": "json"}


@endpoint(
    "/actions/{action_id}/config/import",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Access Control"],
    response=success_response(
        data={
            "message": ResponseField(
                field_type=str,
                description="Import result message",
            ),
        }
    ),
)
async def import_config_endpoint(
    action_id: str,
    config_data: Any = EndpointField(
        description="Configuration data (JSON object or YAML string)"
    ),
    purge: bool = EndpointField(
        default=False, description="Purge existing configuration before import"
    ),
) -> Dict[str, str]:
    """Import access control configuration."""
    action = await AccessControlAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(f"AccessControlAction not found: {action_id}")

    try:
        # Auto-detect format and parse
        if isinstance(config_data, str):
            # Try YAML first, then JSON
            try:
                import yaml

                config = yaml.safe_load(config_data)
            except (ImportError, yaml.YAMLError):
                try:
                    config = json.loads(config_data)
                except json.JSONDecodeError as e:
                    raise ValidationError(f"Invalid JSON/YAML format: {e}")
        elif isinstance(config_data, dict):
            config = config_data
        else:
            raise ValidationError("Config data must be a JSON object or YAML string")

        # Validate config structure
        if not isinstance(config, dict):
            raise ValidationError("Configuration must be a dictionary")

        await action.import_config(config, purge=purge)

        return {"message": "Configuration imported successfully"}

    except Exception as e:
        logger.error(f"Error importing configuration: {e}")
        raise ValidationError(f"Import failed: {str(e)}")
