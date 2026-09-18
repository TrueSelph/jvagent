"""Host-neutral harness contracts (ADR-0054).

Types and validators only. No Orchestrator I/O, persistence, or host imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Protocol, Tuple

CONTRACT_VERSION = "1.0.0"

FORBIDDEN_HOST_DOMAIN_KEYS = frozenset(
    {
        "workspace_id",
        "organization",
        "organization_id",
        "org_id",
        "content_profile_id",
    }
)

FORBIDDEN_AUTHORITY_KEYS = frozenset(
    {
        "authority",
        "trust_tier",
        "capability_token",
        "snapshot_secret",
        "isolation_backend",
    }
)

_NATIVE_CALLER_KEYS = frozenset({"agent_id", "user_id", "session_id"})


class HarnessContractError(ValueError):
    """Invalid harness identity, transition, snapshot, or payload."""


class TurnRunState(str, Enum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    WAITING_TOOL = "waiting_tool"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RECOVERY_REQUIRED = "recovery_required"


class IdempotencyClass(str, Enum):
    IDEMPOTENT = "idempotent"
    COMPENSATABLE = "compensatable"
    NON_RETRYABLE = "non_retryable"


TURN_RUN_TERMINAL = frozenset(
    {
        TurnRunState.COMPLETED,
        TurnRunState.FAILED,
        TurnRunState.CANCELLED,
        TurnRunState.RECOVERY_REQUIRED,
    }
)

LEGAL_TURN_RUN_TRANSITIONS: Mapping[TurnRunState, frozenset[TurnRunState]] = {
    TurnRunState.ACCEPTED: frozenset(
        {
            TurnRunState.RUNNING,
            TurnRunState.CANCELLED,
            TurnRunState.FAILED,
        }
    ),
    TurnRunState.RUNNING: frozenset(
        {
            TurnRunState.WAITING_TOOL,
            TurnRunState.WAITING_APPROVAL,
            TurnRunState.COMPLETED,
            TurnRunState.FAILED,
            TurnRunState.CANCELLED,
            TurnRunState.RECOVERY_REQUIRED,
        }
    ),
    TurnRunState.WAITING_TOOL: frozenset(
        {
            TurnRunState.RUNNING,
            TurnRunState.FAILED,
            TurnRunState.CANCELLED,
            TurnRunState.RECOVERY_REQUIRED,
        }
    ),
    TurnRunState.WAITING_APPROVAL: frozenset(
        {
            TurnRunState.RUNNING,
            TurnRunState.FAILED,
            TurnRunState.CANCELLED,
            TurnRunState.RECOVERY_REQUIRED,
        }
    ),
}


def reject_host_domain_fields(data: Mapping[str, Any]) -> None:
    leaked = FORBIDDEN_HOST_DOMAIN_KEYS.intersection(data)
    if leaked:
        raise HarnessContractError(
            f"host-domain field(s) forbidden on harness types: {sorted(leaked)}"
        )


def reject_model_authority_fields(payload: Mapping[str, Any]) -> None:
    leaked = FORBIDDEN_AUTHORITY_KEYS.intersection(payload)
    if leaked:
        raise HarnessContractError(
            f"authority field(s) forbidden on model-generated payloads: "
            f"{sorted(leaked)}"
        )


def assert_turn_run_transition(src: TurnRunState, dst: TurnRunState) -> None:
    allowed = LEGAL_TURN_RUN_TRANSITIONS.get(src, frozenset())
    if dst not in allowed:
        raise HarnessContractError(
            f"illegal TurnRun transition {src.value} -> {dst.value}"
        )


@dataclass(frozen=True)
class NativeCaller:
    """Admission identity. Host scopes map to session_id outside jvagent."""

    agent_id: str
    user_id: str
    session_id: str

    def as_tuple(self) -> Tuple[str, str, str]:
        return (self.agent_id, self.user_id, self.session_id)

    def to_mapping(self) -> dict[str, str]:
        return {
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
        }


def native_caller_from_mapping(data: Mapping[str, Any]) -> NativeCaller:
    reject_host_domain_fields(data)
    unexpected = set(data) - _NATIVE_CALLER_KEYS
    if unexpected:
        raise HarnessContractError(
            f"unexpected NativeCaller field(s): {sorted(unexpected)}"
        )
    missing = _NATIVE_CALLER_KEYS - set(data)
    if missing:
        raise HarnessContractError(f"missing NativeCaller field(s): {sorted(missing)}")
    return NativeCaller(
        agent_id=str(data["agent_id"]),
        user_id=str(data["user_id"]),
        session_id=str(data["session_id"]),
    )


@dataclass(frozen=True)
class ToolSurfaceSnapshot:
    snapshot_id: str
    caller: NativeCaller
    native_tool_names: Tuple[str, ...]
    native_skill_keys: Tuple[str, ...]
    host_tool_names: Tuple[str, ...]
    host_skill_keys: Tuple[str, ...]
    created_at: str
    expires_at: str
    revoked: bool = False

    def cache_key(self) -> Tuple[str, str, str, str]:
        return (self.snapshot_id, *self.caller.as_tuple())

    def assert_usable(self, now: Optional[datetime] = None) -> None:
        if self.revoked:
            raise HarnessContractError("snapshot is revoked")
        current = now or datetime.now(timezone.utc)
        expiry = datetime.fromisoformat(self.expires_at)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if current >= expiry:
            raise HarnessContractError("snapshot is expired")


@dataclass(frozen=True)
class InvocationRecord:
    invocation_id: str
    snapshot_id: str
    tool_name: str
    input_digest: str
    idempotency_class: Optional[IdempotencyClass] = None
    attempt: int = 1
    outcome: Optional[str] = None


@dataclass(frozen=True)
class EventEnvelope:
    session_id: str
    sequence: int
    cursor: str
    message_id: str
    correlation_id: str
    snapshot_id: str
    kind: str
    # The original transport frame. Metadata alone cannot reconstruct an
    # assistant reply after the process-local ResponseBus has disappeared.
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise HarnessContractError("event sequence must be >= 1")


@dataclass(frozen=True)
class ToolResult:
    invocation_id: str
    ok: bool
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class SkillMaterialization:
    skill_key: str
    digest: str
    spec: str
    body: str


@dataclass(frozen=True)
class SnapshotSelector:
    snapshot_id: Optional[str] = None
    caller: Optional[NativeCaller] = None


class HostCapabilityProvider(Protocol):
    """Host-neutral capability surface. Implementations live outside orchestrator."""

    async def resolve_snapshot(self, caller: NativeCaller) -> ToolSurfaceSnapshot: ...

    async def invoke(
        self,
        snapshot_id: str,
        invocation_id: str,
        tool_name: str,
        payload: Mapping[str, Any],
    ) -> ToolResult: ...

    async def load_skill(
        self, snapshot_id: str, skill_key: str
    ) -> SkillMaterialization: ...

    async def invalidate(self, selector: SnapshotSelector) -> None: ...


__all__ = [
    "CONTRACT_VERSION",
    "FORBIDDEN_AUTHORITY_KEYS",
    "FORBIDDEN_HOST_DOMAIN_KEYS",
    "EventEnvelope",
    "HarnessContractError",
    "HostCapabilityProvider",
    "IdempotencyClass",
    "InvocationRecord",
    "LEGAL_TURN_RUN_TRANSITIONS",
    "NativeCaller",
    "SkillMaterialization",
    "SnapshotSelector",
    "TURN_RUN_TERMINAL",
    "ToolResult",
    "ToolSurfaceSnapshot",
    "TurnRunState",
    "assert_turn_run_transition",
    "native_caller_from_mapping",
    "reject_host_domain_fields",
    "reject_model_authority_fields",
]
