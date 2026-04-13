"""Access Control endpoints."""

import logging
from typing import Any, Dict, List

from jvspatial.api import endpoint
from jvspatial.api.decorators import EndpointField
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError

from jvagent.core.agent import Agent

from .access_control_action import AccessControlAction

logger = logging.getLogger(__name__)


async def _get_access_control(agent_id: str) -> AccessControlAction:
    """Resolve AccessControlAction for agent. Raises if not configured."""
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(f"Agent not found: {agent_id}")
    action = await agent.get_access_control_action()
    if not action:
        raise ResourceNotFoundError(
            f"AccessControlAction not configured for agent: {agent_id}"
        )
    return action


# All endpoints are agent-scoped (AccessControlAction is singleton per agent)


@endpoint(
    "/agents/{agent_id}/access_control/config",
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
async def agent_export_config_endpoint(
    agent_id: str,
    format: str = "json",
) -> Dict[str, Any]:
    """Export access control configuration for agent."""
    action = await _get_access_control(agent_id)
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
    "/agents/{agent_id}/access_control/config",
    methods=["PUT"],
    auth=True,
    roles=["admin"],
    tags=["Access Control"],
    response=success_response(
        data={
            "message": ResponseField(
                field_type=str,
                description="Result message",
            ),
        }
    ),
)
async def agent_replace_config_endpoint(
    agent_id: str,
    permissions: Dict[str, Any] = EndpointField(
        description="Permissions structure: channel -> action_label -> allow/deny rules"
    ),
) -> Dict[str, str]:
    """Replace access control permissions for agent.

    Resolves AccessControlAction from agent_id (no start_node). Replaces the
    permissions dictionary entirely. user_groups and exceptions are preserved.
    """
    action = await _get_access_control(agent_id)
    if not isinstance(permissions, dict):
        raise ValidationError("permissions must be a JSON object")
    action.permissions = permissions
    await action.save()
    return {"message": "Permissions replaced successfully"}


@endpoint(
    "/agents/{agent_id}/access_control/config",
    methods=["PATCH"],
    auth=True,
    roles=["admin"],
    tags=["Access Control"],
    response=success_response(
        data={
            "message": ResponseField(
                field_type=str,
                description="Result message",
            ),
        }
    ),
)
async def agent_merge_config_endpoint(
    agent_id: str,
    permissions: Dict[str, Any] = EndpointField(
        description="Permissions structure to merge: channel -> action_label -> allow/deny rules"
    ),
) -> Dict[str, str]:
    """Merge access control permissions for agent.

    Resolves AccessControlAction from agent_id (no start_node). Merges the
    provided permissions into existing (channel-level merge).
    """
    action = await _get_access_control(agent_id)
    if not isinstance(permissions, dict):
        raise ValidationError("permissions must be a JSON object")
    await action.import_config({"permissions": permissions}, purge=False)
    return {"message": "Permissions merged successfully"}


@endpoint(
    "/agents/{agent_id}/access_control/check",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Access Control"],
    response=success_response(
        data={
            "has_access": ResponseField(
                field_type=bool,
                description="Whether the user has access",
                example=True,
            ),
        }
    ),
)
async def agent_check_access_endpoint(
    agent_id: str,
    user_id: str,
    action_label: str = "all",
    channel: str = "default",
) -> Dict[str, bool]:
    """Check if user has access to action for agent."""
    action = await _get_access_control(agent_id)
    has_access = await action.has_action_access(user_id, action_label, channel)
    return {"has_access": has_access}


@endpoint(
    "/agents/{agent_id}/access_control/user_groups",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Access Control"],
    response=success_response(
        data={
            "message": ResponseField(
                field_type=str,
                description="Result message",
            ),
        }
    ),
)
async def agent_create_user_group_endpoint(
    agent_id: str,
    name: str,
    user_ids: List[str] = EndpointField(default_factory=list),
) -> Dict[str, str]:
    """Create user group for agent."""
    action = await _get_access_control(agent_id)
    await action.add_user_group(name, user_ids or None)
    return {"message": f"Group '{name}' created"}


@endpoint(
    "/agents/{agent_id}/access_control/user_groups/{group}/users",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Access Control"],
    response=success_response(
        data={
            "message": ResponseField(
                field_type=str,
                description="Result message",
            ),
        }
    ),
)
async def agent_add_users_to_group_endpoint(
    agent_id: str,
    group: str,
    user_ids: List[str] = EndpointField(),
) -> Dict[str, str]:
    """Add user(s) to group."""
    action = await _get_access_control(agent_id)
    await action.add_users_to_group(group, user_ids)
    return {"message": f"Added {len(user_ids)} user(s) to group '{group}'"}


@endpoint(
    "/agents/{agent_id}/access_control/user_groups/{group}/users",
    methods=["DELETE"],
    auth=True,
    roles=["admin"],
    tags=["Access Control"],
    response=success_response(
        data={
            "message": ResponseField(
                field_type=str,
                description="Result message",
            ),
        }
    ),
)
async def agent_remove_users_from_group_endpoint(
    agent_id: str,
    group: str,
    user_ids: List[str] = EndpointField(),
) -> Dict[str, str]:
    """Remove user(s) from group."""
    action = await _get_access_control(agent_id)
    for uid in user_ids:
        await action.remove_user_from_group(group, uid)
    return {"message": f"Removed user(s) from group '{group}'"}


@endpoint(
    "/agents/{agent_id}/access_control/user_groups/{group}",
    methods=["DELETE"],
    auth=True,
    roles=["admin"],
    tags=["Access Control"],
    response=success_response(
        data={
            "message": ResponseField(
                field_type=str,
                description="Result message",
            ),
        }
    ),
)
async def agent_remove_user_group_endpoint(
    agent_id: str,
    group: str,
) -> Dict[str, str]:
    """Remove user group."""
    action = await _get_access_control(agent_id)
    await action.remove_user_group(group)
    return {"message": f"Group '{group}' removed"}


@endpoint(
    "/agents/{agent_id}/access_control/user_groups",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Access Control"],
    response=success_response(
        data={
            "user_groups": ResponseField(
                field_type=dict,
                description="Group name to user IDs mapping",
            ),
        }
    ),
)
async def agent_list_user_groups_endpoint(agent_id: str) -> Dict[str, Any]:
    """List user groups and members."""
    action = await _get_access_control(agent_id)
    return {"user_groups": action.get_user_groups()}


@endpoint(
    "/agents/{agent_id}/access_control/permissions",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Access Control"],
    response=success_response(
        data={
            "message": ResponseField(
                field_type=str,
                description="Result message",
            ),
        }
    ),
)
async def agent_add_permission_endpoint(
    agent_id: str,
    channel: str = "default",
    action_label: str = "all",
    user_id: str = EndpointField(default=""),
    group: str = EndpointField(default=""),
    allow: bool = True,
) -> Dict[str, str]:
    """Add user or group to allow/deny for channel+action."""
    if not user_id and not group:
        raise ValidationError("Either user_id or group must be provided")
    action = await _get_access_control(agent_id)
    if user_id:
        if allow:
            await action.add_user_to_allow(channel, action_label, user_id)
        else:
            await action.add_user_to_deny(channel, action_label, user_id)
    else:
        if allow:
            await action.add_group_to_allow(channel, action_label, group)
        else:
            await action.add_group_to_deny(channel, action_label, group)
    return {"message": "Permission added"}


@endpoint(
    "/agents/{agent_id}/access_control/permissions",
    methods=["DELETE"],
    auth=True,
    roles=["admin"],
    tags=["Access Control"],
    response=success_response(
        data={
            "message": ResponseField(
                field_type=str,
                description="Result message",
            ),
        }
    ),
)
async def agent_remove_permission_endpoint(
    agent_id: str,
    channel: str = "default",
    action_label: str = "all",
    user_id: str = EndpointField(default=""),
    group: str = EndpointField(default=""),
    from_allow: bool = True,
) -> Dict[str, str]:
    """Remove user or group from allow/deny for channel+action."""
    if not user_id and not group:
        raise ValidationError("Either user_id or group must be provided")
    action = await _get_access_control(agent_id)
    if user_id:
        await action.remove_user_from_permission(
            channel, action_label, user_id, from_allow
        )
    else:
        await action.remove_group_from_permission(
            channel, action_label, group, from_allow
        )
    return {"message": "Permission removed"}
