"""Public harness contract types and runtime (ADR-0054)."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

from jvagent.harness.contracts import (
    CONTRACT_VERSION,
    EventEnvelope,
    HarnessContractError,
    HostCapabilityProvider,
    IdempotencyClass,
    InvocationRecord,
    NativeCaller,
    SkillMaterialization,
    SnapshotSelector,
    ToolResult,
    ToolSurfaceSnapshot,
    TurnRunState,
    assert_turn_run_transition,
    host_work_execution_context,
    native_caller_from_mapping,
    reject_host_domain_fields,
    reject_model_authority_fields,
)

if TYPE_CHECKING:
    from jvagent.harness.runtime import (
        HarnessRuntime,
        HarnessStore,
        get_runtime,
        reset_runtime,
    )


def __getattr__(name: str) -> Any:
    """Resolve transitional public runtime exports only when explicitly requested."""
    if name in {"HarnessRuntime", "HarnessStore", "get_runtime", "reset_runtime"}:
        value = getattr(import_module("jvagent.harness.runtime"), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CONTRACT_VERSION",
    "EventEnvelope",
    "HarnessContractError",
    "HarnessRuntime",
    "HarnessStore",
    "HostCapabilityProvider",
    "IdempotencyClass",
    "InvocationRecord",
    "NativeCaller",
    "SkillMaterialization",
    "SnapshotSelector",
    "ToolResult",
    "ToolSurfaceSnapshot",
    "TurnRunState",
    "assert_turn_run_transition",
    "get_runtime",
    "host_work_execution_context",
    "native_caller_from_mapping",
    "reject_host_domain_fields",
    "reject_model_authority_fields",
    "reset_runtime",
]
