"""Host Action authorization shared by native and transitional execution.

Tool dispatch is gated on the existing AC taxonomy; IA-as-tools use the stable
``tool:delegate:{action_name}`` label. Fail-open when no enforcing
``AccessControlAction`` is attached; fail-closed when AC raises.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("jvagent.action.orchestrator.access")


def delegate_resource_label(action_name: str) -> str:
    return f"tool:delegate:{action_name}"


async def _resolve_access_control(agent: Any) -> Optional[Any]:
    if agent is None:
        return None
    # A missing policy is an intentional open deployment. A configured policy
    # that cannot be loaded is an operational/security failure and must not be
    # confused with absence.
    ac = await agent.get_access_control_action()
    if ac is None:
        return None
    if not ac.policy_applies():
        return None
    return ac


def _policy_failure(
    exc: Exception, *, label: str, user_id: Optional[str], channel: str
) -> None:
    logger.error(
        "orchestrator_access_policy_failure",
        extra={
            "event": "orchestrator_access_policy_failure",
            "event_code": "orchestrator_access_policy_failure",
            "action_label": label,
            "channel": channel,
            "actor_present": bool(user_id),
            "stage": "orchestrator",
            "reason": "policy_resolution_or_evaluation_error",
            "exception_type": type(exc).__name__,
        },
    )


async def is_tool_allowed(
    agent: Any,
    *,
    label: str,
    user_id: Optional[str],
    channel: str,
    tool_name: Optional[str] = None,
    requires_tool_permission: bool = False,
) -> bool:
    """True if the labelled tool may be dispatched (fail-open / fail-closed)."""
    try:
        ac = await _resolve_access_control(agent)
        if ac is None:
            return not requires_tool_permission
        if tool_name is not None:
            names = getattr(ac, "tool_permission_names", None)
            named_rule = callable(names) and tool_name in set(names(channel) or ())
            if requires_tool_permission and not named_rule:
                return False
            if requires_tool_permission or named_rule:
                check = getattr(ac, "has_tool_access", None)
                if not callable(check) or not await check(
                    user_id or "", tool_name, channel
                ):
                    return False
        allowed = bool(
            await ac.has_action_access(
                user_id=user_id or "",
                action_label=label,
                channel=channel,
            )
        )
        if not allowed:
            logger.info(
                "orchestrator_access_denied",
                extra={
                    "event": "orchestrator_access_denied",
                    "event_code": "orchestrator_access_denied",
                    "action_label": label,
                    "channel": channel,
                    "actor_present": bool(user_id),
                    "stage": "orchestrator",
                    "reason": "policy_denied",
                },
            )
        return allowed
    except Exception as exc:
        _policy_failure(exc, label=label, user_id=user_id, channel=channel)
        return False


async def drop_unpermitted_tools(
    ac: Any,
    *,
    user_id: Optional[str],
    channel: str,
    tools: Dict[str, Any],
    visible: set,
    longtail: Optional[set] = None,
) -> None:
    """Remove tools listed in ``permissions[channel].tools`` the sender cannot call.

    A pin cannot keep them. They leave the callable map, so discovery cannot
    load them either. Names that are not in that map stay.
    """
    names = ac.tool_permission_names(channel)
    for name in names:
        if name not in tools:
            continue
        try:
            allowed = bool(await ac.has_tool_access(user_id or "", name, channel))
        except Exception as exc:
            _policy_failure(exc, label=name, user_id=user_id, channel=channel)
            allowed = False
        if allowed:
            continue
        tools.pop(name, None)
        visible.discard(name)
        if longtail is not None:
            longtail.discard(name)


async def named_tool_rule_applies(agent: Any, *, tool_name: str, channel: str) -> bool:
    """Require named authorization on policy failure as well as explicit rules."""
    try:
        ac = await _resolve_access_control(agent)
        if ac is None:
            return False
        names = getattr(ac, "tool_permission_names", None)
        return callable(names) and str(tool_name or "") in set(names(channel) or ())
    except Exception as exc:
        _policy_failure(exc, label=tool_name, user_id=None, channel=channel)
        return True


async def denied_named_tool_names(
    agent: Any, *, user_id: Optional[str], channel: str
) -> set:
    """Configured ``permissions[channel].tools`` names this sender cannot call."""
    ac = await _resolve_access_control(agent)
    if ac is None:
        return set()
    names_fn = getattr(ac, "tool_permission_names", None)
    check = getattr(ac, "has_tool_access", None)
    if not callable(names_fn) or not callable(check):
        return set()
    try:
        names = set(names_fn(channel) or ())
    except Exception:
        raise RuntimeError("Named tool permission discovery failed") from None
    denied: set = set()
    for name in names:
        try:
            allowed = bool(await check(user_id or "", name, channel))
        except Exception as exc:
            _policy_failure(exc, label=name, user_id=user_id, channel=channel)
            allowed = False
        if not allowed:
            denied.add(str(name))
    return denied


async def is_named_tool_allowed(
    agent: Any, *, tool_name: str, user_id: Optional[str], channel: str
) -> bool:
    """True if ``permissions[channel].tools[tool_name]`` allows this sender.

    Opted-in tools fail closed: no enforcing AccessControl, a missing ``tools``
    entry, or no matching allow rule denies the call. This does not use the
    action-label gate.
    """
    try:
        ac = await _resolve_access_control(agent)
        if ac is None:
            return False
        names = getattr(ac, "tool_permission_names", None)
        if not callable(names) or tool_name not in set(names(channel) or ()):
            return False
        check = getattr(ac, "has_tool_access", None)
        return callable(check) and bool(await check(user_id or "", tool_name, channel))
    except Exception as exc:
        _policy_failure(exc, label=tool_name, user_id=user_id, channel=channel)
        return False


__all__ = [
    "delegate_resource_label",
    "denied_named_tool_names",
    "drop_unpermitted_tools",
    "is_named_tool_allowed",
    "is_tool_allowed",
    "named_tool_rule_applies",
]
