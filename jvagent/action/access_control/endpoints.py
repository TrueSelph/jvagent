"""REST endpoints for per-agent access control (permissions, groups, checks).

This module exposes admin routes scoped under ``/agents/{agent_id}/access_control``.
Handlers resolve the agent's ``AccessControlAction`` and mutate or read its
permissions, user groups, and allow/deny rules.

``user_groups`` are nested by action label::

    {
        "default": {"public": [], "private": []},
        "PageIndexAction": {"reviewers": ["user_1"]},
    }

The ``default`` scope is used when evaluating group membership for actions
that lack their own entry.  Endpoints accept an ``action_label`` parameter
(default ``"default"``) to scope group operations.
"""

from typing import Any, Dict, List, Optional, Union

from fastapi import Query
from jvspatial.api import endpoint
from jvspatial.api.decorators import EndpointField
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError

from jvagent.core.agent import Agent

from .access_control_action import AccessControlAction


async def _get_access_control(agent_id: str) -> AccessControlAction:
    """Load the agent and return its configured ``AccessControlAction``.

    Args:
        agent_id: Agent node id.

    Returns:
        The access control action for that agent.

    Raises:
        ResourceNotFoundError: If the agent or its access control action is missing.
    """
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent '{agent_id}' not found",
            details={"agent_id": agent_id},
        )
    action = await agent.get_access_control_action()
    if not action:
        raise ResourceNotFoundError(
            message=f"Access control is not configured for agent '{agent_id}'",
            details={"agent_id": agent_id},
        )
    return action


@endpoint(
    "/agents/{agent_id}/access_control/config",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Access Control"],
    response=success_response(
        data={
            "config": ResponseField(
                field_type=Union[str, Dict[str, Any]],
                description="Configuration as a JSON object, or YAML text when format is yaml",
                example={
                    "permissions": {
                        "default": {
                            "any": {
                                "allow": [{"group": "all", "enabled": True}],
                                "deny": [],
                            }
                        }
                    },
                    "user_groups": {
                        "default": {"staff": ["user_1", "user_2"]},
                    },
                    "enforce": True,
                },
            ),
            "format": ResponseField(
                field_type=str,
                description="Serialization of config: json (object) or yaml (string)",
                example="json",
            ),
        }
    ),
)
async def agent_export_config_endpoint(
    agent_id: str,
    format: str = Query(
        "json",
        description="Export as structured JSON (object) or YAML (string)",
    ),
) -> Dict[str, Any]:
    """Export access control configuration for an agent.

    Args:
        agent_id: Agent id from the path.
        format: Query parameter ``json`` (default) or ``yaml``.

    Returns:
        ``config`` plus ``format`` indicating how ``config`` is encoded.

    Raises:
        ResourceNotFoundError: If the agent or access control action is missing.
        ValidationError: If ``format`` is ``yaml`` but PyYAML is not installed.
    """
    action = await _get_access_control(agent_id)
    config = action.export_config()
    fmt = format.lower()
    if fmt == "yaml":
        try:
            import yaml
        except ImportError as e:
            raise ValidationError(
                message="YAML export requires the PyYAML package",
                details={"format": "yaml"},
            ) from e
        config_str = yaml.dump(config, default_flow_style=False)
        return {"config": config_str, "format": "yaml"}
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
                description="Confirmation that permissions were replaced",
                example="Permissions replaced successfully",
            ),
        }
    ),
)
async def agent_replace_config_endpoint(
    agent_id: str,
    permissions: Dict[str, Any] = EndpointField(
        description="Permissions structure: channel -> action_label -> allow/deny rules"
    ),
) -> Dict[str, Any]:
    """Replace the permissions tree for the agent (user_groups and exceptions unchanged).

    Args:
        agent_id: Agent id from the path.
        permissions: Full permissions object from the JSON body.

    Returns:
        A short confirmation message.

    Raises:
        ResourceNotFoundError: If the agent or access control action is missing.
        ValidationError: If ``permissions`` is not a JSON object.
    """
    action = await _get_access_control(agent_id)
    if not isinstance(permissions, dict):
        raise ValidationError(
            message="permissions must be a JSON object",
            details={"field": "permissions"},
        )
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
                description="Confirmation that permissions were merged",
                example="Permissions merged successfully",
            ),
        }
    ),
)
async def agent_merge_config_endpoint(
    agent_id: str,
    permissions: Dict[str, Any] = EndpointField(
        description="Permissions structure to merge: channel -> action_label -> allow/deny rules"
    ),
) -> Dict[str, Any]:
    """Merge permissions into the existing configuration (channel-level merge).

    Args:
        agent_id: Agent id from the path.
        permissions: Partial permissions object from the JSON body.

    Returns:
        A short confirmation message.

    Raises:
        ResourceNotFoundError: If the agent or access control action is missing.
        ValidationError: If ``permissions`` is not a JSON object.
    """
    action = await _get_access_control(agent_id)
    if not isinstance(permissions, dict):
        raise ValidationError(
            message="permissions must be a JSON object",
            details={"field": "permissions"},
        )
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
                description="Whether the user is allowed the action on the channel",
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
) -> Dict[str, Any]:
    """Evaluate whether a user may run an action on a channel.

    Args:
        agent_id: Agent id from the path.
        user_id: Subject user id (JSON body).
        action_label: Action label to check (body; default ``all``).
        channel: Channel name (body; default ``default``).

    Returns:
        ``has_access`` boolean from the access control rules.

    Raises:
        ResourceNotFoundError: If the agent or access control action is missing.
    """
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
                description="Confirmation that the group was created",
                example="Group 'staff' created",
            ),
        }
    ),
)
async def agent_create_user_group_endpoint(
    agent_id: str,
    name: str,
    user_ids: List[str] = EndpointField(default_factory=list),
    action_label: str = EndpointField(
        default="default",
        description="Action label scope for the group (default: 'default')",
    ),
) -> Dict[str, Any]:
    """Create a named user group (optional initial member ids).

    Args:
        agent_id: Agent id from the path.
        name: Group name (JSON body).
        user_ids: Initial member ids (body; may be empty).
        action_label: Action label scope (body; default ``default``).

    Returns:
        A short confirmation message.

    Raises:
        ResourceNotFoundError: If the agent or access control action is missing.
    """
    action = await _get_access_control(agent_id)
    await action.add_user_group(name, user_ids or None, action_label=action_label)
    return {"message": f"Group '{name}' created under '{action_label}'"}


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
                description="Confirmation including how many users were added",
                example="Added 2 user(s) to group 'staff'",
            ),
        }
    ),
)
async def agent_add_users_to_group_endpoint(
    agent_id: str,
    group: str,
    user_ids: List[str] = EndpointField(),
    action_label: str = EndpointField(
        default="default",
        description="Action label scope for the group (default: 'default')",
    ),
) -> Dict[str, Any]:
    """Add users to an existing group.

    Args:
        agent_id: Agent id from the path.
        group: Group name from the path.
        user_ids: User ids to add (JSON body).
        action_label: Action label scope (body; default ``default``).

    Returns:
        A short confirmation message.

    Raises:
        ResourceNotFoundError: If the agent or access control action is missing.
    """
    action = await _get_access_control(agent_id)
    await action.add_users_to_group(group, user_ids, action_label=action_label)
    return {"message": f"Added {len(user_ids)} user(s) to group '{group}' under '{action_label}'"}


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
                description="Confirmation that users were removed from the group",
                example="Removed user(s) from group 'staff'",
            ),
        }
    ),
)
async def agent_remove_users_from_group_endpoint(
    agent_id: str,
    group: str,
    user_ids: List[str] = EndpointField(),
    action_label: str = EndpointField(
        default="default",
        description="Action label scope for the group (default: 'default')",
    ),
) -> Dict[str, Any]:
    """Remove users from a group (one removal per id in ``user_ids``).

    Args:
        agent_id: Agent id from the path.
        group: Group name from the path.
        user_ids: User ids to remove (JSON body).
        action_label: Action label scope (body; default ``default``).

    Returns:
        A short confirmation message.

    Raises:
        ResourceNotFoundError: If the agent or access control action is missing.
    """
    action = await _get_access_control(agent_id)
    for uid in user_ids:
        await action.remove_user_from_group(group, uid, action_label=action_label)
    return {"message": f"Removed user(s) from group '{group}' under '{action_label}'"}


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
                description="Confirmation that the group was deleted",
                example="Group 'staff' removed",
            ),
        }
    ),
)
async def agent_remove_user_group_endpoint(
    agent_id: str,
    group: str,
    action_label: str = EndpointField(
        default="default",
        description="Action label scope for the group (default: 'default')",
    ),
) -> Dict[str, Any]:
    """Delete a user group.

    Args:
        agent_id: Agent id from the path.
        group: Group name from the path.
        action_label: Action label scope (body; default ``default``).

    Returns:
        A short confirmation message.

    Raises:
        ResourceNotFoundError: If the agent or access control action is missing.
    """
    action = await _get_access_control(agent_id)
    await action.remove_user_group(group, action_label=action_label)
    return {"message": f"Group '{group}' removed from '{action_label}'"}


@endpoint(
    "/agents/{agent_id}/access_control/user_groups",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Access Control"],
    response=success_response(
        data={
            "user_groups": ResponseField(
                field_type=Union[str, Dict],
                description=(
                    "When action_label is provided: group name to member user ids "
                    "for that scope. Otherwise: full nested structure keyed by "
                    "action label."
                ),
                example={
                    "default": {"staff": ["user_7b2", "user_9aa"], "beta": ["user_1"]},
                },
            ),
        }
    ),
)
async def agent_list_user_groups_endpoint(
    agent_id: str,
    action_label: Optional[str] = Query(
        default=None,
        description=(
            "Optional action label scope. When provided, returns the merged "
            "groups for that scope (action-specific + default). Omit for the "
            "full nested structure."
        ),
    ),
) -> Dict[str, Any]:
    """List user groups and their members.

    Args:
        agent_id: Agent id from the path.
        action_label: Optional scope filter (query param).

    Returns:
        ``user_groups`` — flat dict when action_label given, nested otherwise.

    Raises:
        ResourceNotFoundError: If the agent or access control action is missing.
    """
    action = await _get_access_control(agent_id)
    return {"user_groups": action.get_user_groups(action_label=action_label)}


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
                description="Confirmation that an allow/deny rule was added",
                example="Permission added",
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
) -> Dict[str, Any]:
    """Add a user or group to allow or deny for a channel and action label.

    Args:
        agent_id: Agent id from the path.
        channel: Channel (body; default ``default``).
        action_label: Action label (body; default ``all``).
        user_id: User id when targeting a user (body).
        group: Group name when targeting a group (body).
        allow: If True, add to allow list; otherwise to deny (body).

    Returns:
        A short confirmation message.

    Raises:
        ResourceNotFoundError: If the agent or access control action is missing.
        ValidationError: If neither ``user_id`` nor ``group`` is set.
    """
    if not user_id and not group:
        raise ValidationError(
            message="Either user_id or group must be provided",
            details={"user_id": user_id, "group": group},
        )
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
                description="Confirmation that a rule was removed from allow or deny",
                example="Permission removed",
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
) -> Dict[str, Any]:
    """Remove a user or group from allow or deny for a channel and action label.

    Args:
        agent_id: Agent id from the path.
        channel: Channel (body; default ``default``).
        action_label: Action label (body; default ``all``).
        user_id: User id when targeting a user (body).
        group: Group name when targeting a group (body).
        from_allow: If True, remove from allow list; otherwise from deny (body).

    Returns:
        A short confirmation message.

    Raises:
        ResourceNotFoundError: If the agent or access control action is missing.
        ValidationError: If neither ``user_id`` nor ``group`` is set.
    """
    if not user_id and not group:
        raise ValidationError(
            message="Either user_id or group must be provided",
            details={"user_id": user_id, "group": group},
        )
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
