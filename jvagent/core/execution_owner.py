"""Select the declared execution owner without creating another runtime."""

import os
from typing import Any


async def uses_native_harness(agent: Any) -> bool:
    """Resolve enabled graph Actions; never fall back after a lookup failure."""
    from jvagent.action.native_harness import NativeHarnessAction

    actions = await agent.get_actions(enabled_only=True)
    native = [action for action in actions if isinstance(action, NativeHarnessAction)]
    if not native:
        return False
    if len(native) != 1 or any(
        action is not native[0] and action.get_manifest().pattern_orchestrator
        for action in actions
    ):
        raise RuntimeError("Configure only one harness execution Action")
    return True


def require_native_local_ownership() -> None:
    """Refuse declared distributed ownership until native fencing is qualified.

    Existing conversation locks remain in force for local admission. Their leases
    alone do not qualify SDK checkpoint, spend, or effect ownership across workers.
    Hosts must not use this development entrypoint in an undeclared worker pool.
    """
    if any(
        os.getenv(name, "").strip()
        for name in (
            "JVAGENT_CONVERSATION_LOCK_REDIS_URL",
            "JVAGENT_CONVERSATION_LOCK_DYNAMODB_TABLE",
        )
    ) or os.getenv("JVAGENT_REQUIRE_DISTRIBUTED_CONVERSATION_LOCK", "").lower() in (
        "true",
        "1",
        "yes",
    ):
        raise RuntimeError("Native distributed execution ownership is not qualified")
    for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS"):
        value = os.getenv(name, "1")
        if not value.isdecimal() or int(value) != 1:
            raise RuntimeError("Native execution requires one declared worker")
