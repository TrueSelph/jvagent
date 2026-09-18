"""Public harness contract types and runtime (ADR-0054)."""

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
    native_caller_from_mapping,
    reject_host_domain_fields,
    reject_model_authority_fields,
)
from jvagent.harness.runtime import (
    HarnessRuntime,
    HarnessStore,
    get_runtime,
    reset_runtime,
)

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
    "native_caller_from_mapping",
    "reject_host_domain_fields",
    "reject_model_authority_fields",
    "reset_runtime",
]
