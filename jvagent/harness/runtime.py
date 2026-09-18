"""Store-backed harness runtime (HP-02 … HP-12).

Process-local default. Inject a shared :class:`HarnessStore` for two-worker
tests. TurnRun is a journal Object (I-GRAPH-02), not a conversation Node.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

from jvagent.harness.contracts import (
    CONTRACT_VERSION,
    TURN_RUN_TERMINAL,
    EventEnvelope,
    HarnessContractError,
    IdempotencyClass,
    InvocationRecord,
    NativeCaller,
    SkillMaterialization,
    SnapshotSelector,
    ToolSurfaceSnapshot,
    TurnRunState,
    assert_turn_run_transition,
    native_caller_from_mapping,
    reject_host_domain_fields,
    reject_model_authority_fields,
)

SAME_SESSION_POLICY = "lease"
SNAPSHOT_TTL = timedelta(hours=1)
DEFAULT_LEASE_TTL_S = 30.0
MAX_EVENTS_PER_SESSION = 10_000
MAX_OBSERVATION_CHARS = 8_000
MAX_TRACE_SPANS = 10_000
APPROVED_ISOLATION_BACKENDS = frozenset({"gvisor", "firecracker", "nsjail"})
CHECKPOINT_KIND = "harness.turn_run"
TRACE_KIND = "harness.trace"

_log = logging.getLogger("jvagent.harness")

_runtime_guard = threading.Lock()
_runtime: Optional["HarnessRuntime"] = None


class AdmissionRefused(HarnessContractError):
    """Turn or snapshot admission rejected (drain, lease, identity)."""


class SessionBusy(AdmissionRefused):
    """Same-session policy is lease; another worker holds the session."""


class SkillIsolationRefused(HarnessContractError):
    """Untrusted skill has no approved isolation backend."""


@dataclass
class TurnRunJournal:
    """Log-shaped TurnRun. Not a graph Node."""

    correlation_id: str
    caller: NativeCaller
    state: TurnRunState
    snapshot_id: str
    interaction_id: str = ""
    seq: int = 0
    worker_id: str = ""
    entries: List[Dict[str, Any]] = field(default_factory=list)
    completed_invocation_ids: List[str] = field(default_factory=list)
    observation_refs: List[str] = field(default_factory=list)
    plan_phase: str = ""
    reason: str = ""


@dataclass
class SkillManifest:
    skill_key: str
    source: str
    digest: str
    declared_tools: Tuple[str, ...]
    capabilities: Tuple[str, ...]
    trust_tier: str
    spec: str = "jv"
    signature: str = ""
    body: str = ""


@dataclass
class StageRecord:
    caller: NativeCaller
    snapshot_id: str
    digest: str
    path: str
    active: bool = True


@dataclass
class HarnessStore:
    """Shared backend. One store per process by default; share for multi-worker tests."""

    identities: Dict[Tuple[str, str], str] = field(default_factory=dict)
    conversations: Dict[Tuple[str, str], str] = field(default_factory=dict)
    snapshots: Dict[str, ToolSurfaceSnapshot] = field(default_factory=dict)
    current_snapshot: Dict[Tuple[str, str, str], str] = field(default_factory=dict)
    generation: Dict[Tuple[str, str, str], int] = field(default_factory=dict)
    runs: Dict[str, TurnRunJournal] = field(default_factory=dict)
    runs_by_interaction: Dict[str, str] = field(default_factory=dict)
    invocations: Dict[str, InvocationRecord] = field(default_factory=dict)
    invocation_results: Dict[str, str] = field(default_factory=dict)
    outbox: Dict[str, List[EventEnvelope]] = field(default_factory=dict)
    leases: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    traces: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    skill_manifests: Dict[str, SkillManifest] = field(default_factory=dict)
    stages: Dict[str, StageRecord] = field(default_factory=dict)
    host_tools: Dict[str, List[str]] = field(default_factory=dict)
    host_skills: Dict[str, List[str]] = field(default_factory=dict)
    host_skill_materializations: Dict[Tuple[str, str], SkillMaterialization] = field(
        default_factory=dict
    )
    revoked_host_tools: Dict[str, set] = field(default_factory=dict)
    revoked_manifests: set = field(default_factory=set)
    breaker_states: Dict[str, Any] = field(default_factory=dict)
    draining: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


class HarnessRuntime:
    """Admission, snapshots, journal, ledger, outbox, leases, traces, skills."""

    def __init__(
        self,
        store: Optional[HarnessStore] = None,
        *,
        worker_id: str = "",
        isolation_backend: str = "",
        skill_signing_key: str = "",
        lease_backend: Any = None,
        max_trace_spans: int = MAX_TRACE_SPANS,
    ) -> None:
        self.store = store or HarnessStore()
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.isolation_backend = isolation_backend
        self.skill_signing_key = skill_signing_key
        self.lease_backend = lease_backend
        self.max_trace_spans = max_trace_spans
        self.contract_version = CONTRACT_VERSION
        self._host_runners: Dict[Tuple[str, str], Any] = {}
        self._compensators: Dict[str, Any] = {}

    # -- identity (HP-02) -------------------------------------------------

    def upsert_user(self, memory_id: str, user_id: str) -> str:
        key = (memory_id, user_id)
        with self.store.lock:
            node = self.store.identities.get(key)
            if node is None:
                node = f"user:{memory_id}:{user_id}"
                self.store.identities[key] = node
            return node

    def upsert_conversation(self, memory_id: str, session_id: str) -> str:
        key = (memory_id, session_id)
        with self.store.lock:
            node = self.store.conversations.get(key)
            if node is None:
                node = f"conv:{memory_id}:{session_id}"
                self.store.conversations[key] = node
            return node

    def admit_payload(self, data: Optional[Mapping[str, Any]]) -> None:
        if data:
            reject_host_domain_fields(data)

    def new_correlation(self) -> str:
        return f"corr-{uuid.uuid4().hex}"

    # -- snapshots (HP-03) ------------------------------------------------

    def admit_snapshot(
        self,
        caller: NativeCaller,
        *,
        native_tool_names: Tuple[str, ...] = (),
        native_skill_keys: Tuple[str, ...] = (),
        force_new: bool = False,
    ) -> ToolSurfaceSnapshot:
        if self.store.draining:
            raise AdmissionRefused("admissions stopped: worker draining")
        key = caller.as_tuple()
        now = datetime.now(timezone.utc)
        with self.store.lock:
            current_id = self.store.current_snapshot.get(key)
            if current_id and not force_new:
                snap = self.store.snapshots.get(current_id)
                if snap is not None:
                    try:
                        snap.assert_usable(now)
                        return snap
                    except HarnessContractError:
                        pass
            host_tools = tuple(
                t
                for t in self.store.host_tools.get(caller.session_id, [])
                if t not in self.store.revoked_host_tools.get(caller.session_id, set())
            )
            host_skills = tuple(self.store.host_skills.get(caller.session_id, []))
            snap = ToolSurfaceSnapshot(
                snapshot_id=f"snap-{uuid.uuid4().hex}",
                caller=caller,
                native_tool_names=tuple(native_tool_names),
                native_skill_keys=tuple(native_skill_keys),
                host_tool_names=host_tools,
                host_skill_keys=host_skills,
                created_at=now.isoformat(),
                expires_at=(now + SNAPSHOT_TTL).isoformat(),
                revoked=False,
            )
            self.store.snapshots[snap.snapshot_id] = snap
            self.store.current_snapshot[key] = snap.snapshot_id
            self.store.generation[key] = self.store.generation.get(key, 0) + 1
            return snap

    def get_snapshot(self, snapshot_id: str) -> Optional[ToolSurfaceSnapshot]:
        return self.store.snapshots.get(snapshot_id)

    def invalidate(self, selector: SnapshotSelector) -> None:
        with self.store.lock:
            ids: List[str] = []
            if selector.snapshot_id:
                ids.append(selector.snapshot_id)
            if selector.caller is not None:
                current = self.store.current_snapshot.get(selector.caller.as_tuple())
                if current:
                    ids.append(current)
            for sid in ids:
                snap = self.store.snapshots.get(sid)
                if snap is None:
                    continue
                self.store.snapshots[sid] = replace(snap, revoked=True)
                key = snap.caller.as_tuple()
                if self.store.current_snapshot.get(key) == sid:
                    self.store.current_snapshot.pop(key, None)

    def update_snapshot_descriptors(
        self,
        snapshot_id: str,
        *,
        native_tool_names: Tuple[str, ...] = (),
        native_skill_keys: Tuple[str, ...] = (),
    ) -> ToolSurfaceSnapshot:
        snap = self.store.snapshots.get(snapshot_id)
        if snap is None:
            raise HarnessContractError(f"unknown snapshot {snapshot_id}")
        snap.assert_usable()
        updated = replace(
            snap,
            native_tool_names=tuple(native_tool_names) or snap.native_tool_names,
            native_skill_keys=tuple(native_skill_keys) or snap.native_skill_keys,
        )
        with self.store.lock:
            self.store.snapshots[snapshot_id] = updated
        return updated

    def require_usable(self, snapshot_id: str) -> ToolSurfaceSnapshot:
        snap = self.store.snapshots.get(snapshot_id)
        if snap is None:
            raise HarnessContractError(f"unknown snapshot {snapshot_id}")
        snap.assert_usable()
        return snap

    # -- TurnRun journal (HP-04) ------------------------------------------

    def start_turn(
        self,
        correlation_id: str,
        caller: NativeCaller,
        snapshot: ToolSurfaceSnapshot,
        *,
        interaction_id: str = "",
        plan_phase: str = "",
    ) -> TurnRunJournal:
        if self.store.draining:
            raise AdmissionRefused("admissions stopped: worker draining")
        journal = TurnRunJournal(
            correlation_id=correlation_id,
            caller=caller,
            state=TurnRunState.ACCEPTED,
            snapshot_id=snapshot.snapshot_id,
            interaction_id=interaction_id,
            worker_id=self.worker_id,
            plan_phase=plan_phase,
        )
        self._append_journal(journal, TurnRunState.RUNNING, "admitted")
        with self.store.lock:
            self.store.runs[correlation_id] = journal
            if interaction_id:
                self.store.runs_by_interaction[interaction_id] = correlation_id
        self.record_span(correlation_id, "admission", caller=caller)
        return journal

    def get_run(self, correlation_id: str) -> Optional[TurnRunJournal]:
        return self.store.runs.get(correlation_id)

    def transition(
        self,
        correlation_id: str,
        dst: TurnRunState,
        *,
        reason: str = "",
    ) -> TurnRunJournal:
        journal = self._require_run(correlation_id)
        assert_turn_run_transition(journal.state, dst)
        self._append_journal(journal, dst, reason)
        return journal

    def complete_turn(self, correlation_id: str, *, reason: str = "completed") -> None:
        journal = self._require_run(correlation_id)
        if journal.state in TURN_RUN_TERMINAL:
            return
        if journal.state is TurnRunState.WAITING_TOOL:
            self.transition(correlation_id, TurnRunState.RUNNING, reason="flush")
            journal = self._require_run(correlation_id)
        assert_turn_run_transition(journal.state, TurnRunState.COMPLETED)
        self._append_journal(journal, TurnRunState.COMPLETED, reason)

    def fail_turn(self, correlation_id: str, *, reason: str = "failed") -> None:
        journal = self._require_run(correlation_id)
        if journal.state in TURN_RUN_TERMINAL:
            return
        assert_turn_run_transition(journal.state, TurnRunState.FAILED)
        self._append_journal(journal, TurnRunState.FAILED, reason)

    def mark_recovery(self, correlation_id: str, *, reason: str) -> TurnRunJournal:
        journal = self._require_run(correlation_id)
        if journal.state not in TURN_RUN_TERMINAL:
            assert_turn_run_transition(journal.state, TurnRunState.RECOVERY_REQUIRED)
            self._append_journal(journal, TurnRunState.RECOVERY_REQUIRED, reason)
        return journal

    def resume_turn(self, correlation_id: str) -> TurnRunJournal:
        journal = self._require_run(correlation_id)
        if journal.state in TURN_RUN_TERMINAL:
            raise HarnessContractError(
                f"cannot resume terminal run {journal.state.value}"
            )
        return journal

    def list_journal(
        self, correlation_id: str, *, offset: int = 0, limit: int = 100
    ) -> List[Dict[str, Any]]:
        journal = self._require_run(correlation_id)
        return journal.entries[offset : offset + limit]

    def peek_completed_result(
        self,
        *,
        correlation_id: str,
        tool_name: str,
        payload: Mapping[str, Any],
    ) -> Optional[str]:
        """Cached IDEMPOTENT result for this (tool, args). Does not bump attempt."""
        digest = _input_digest(tool_name, payload)
        ledger_key = f"{correlation_id}:{tool_name}:{digest}"
        with self.store.lock:
            existing = self.store.invocations.get(ledger_key)
            if existing is None:
                return None
            if existing.idempotency_class is not IdempotencyClass.IDEMPOTENT:
                return None
            return self.store.invocation_results.get(existing.invocation_id)

    def correlation_for_session(self, session_id: str) -> Optional[str]:
        if not session_id:
            return None
        if self.lease_backend is not None:
            held = self.lease_backend.get(session_id)
        else:
            held = self.store.leases.get(session_id)
        if held:
            corr = str(held.get("correlation_id") or "")
            if corr:
                return corr
        with self.store.lock:
            for corr, journal in self.store.runs.items():
                if journal.caller.session_id == session_id and (
                    journal.state not in TURN_RUN_TERMINAL
                ):
                    return corr
        return None

    def export_checkpoint(self, correlation_id: str) -> Dict[str, Any]:
        journal = self._require_run(correlation_id)
        snap = self.store.snapshots.get(journal.snapshot_id)
        with self.store.lock:
            prefix = f"{correlation_id}:"
            invocations: List[Dict[str, Any]] = []
            for key, rec in self.store.invocations.items():
                if not key.startswith(prefix):
                    continue
                rec_map = asdict(rec)
                klass = rec.idempotency_class
                rec_map["idempotency_class"] = klass.value if klass else None
                invocations.append(
                    {
                        "ledger_key": key,
                        "record": rec_map,
                        "result": self.store.invocation_results.get(rec.invocation_id),
                    }
                )
            outbox = [
                asdict(e) for e in self.store.outbox.get(journal.caller.session_id, [])
            ]
        snap_map: Optional[Dict[str, Any]] = None
        if snap is not None:
            snap_map = asdict(snap)
            snap_map["caller"] = snap.caller.to_mapping()
        return {
            "correlation_id": journal.correlation_id,
            "state": journal.state.value,
            "snapshot_id": journal.snapshot_id,
            "interaction_id": journal.interaction_id,
            "seq": journal.seq,
            "completed_invocation_ids": list(journal.completed_invocation_ids),
            "observation_refs": list(journal.observation_refs),
            "plan_phase": journal.plan_phase,
            "reason": journal.reason,
            "entries": list(journal.entries),
            "invocations": invocations,
            "outbox": outbox,
            "caller": journal.caller.to_mapping(),
            "snapshot": snap_map,
            "worker_id": journal.worker_id,
        }

    def import_checkpoint(self, payload: Mapping[str, Any]) -> TurnRunJournal:
        caller = native_caller_from_mapping(payload["caller"])
        journal = TurnRunJournal(
            correlation_id=str(payload["correlation_id"]),
            caller=caller,
            state=TurnRunState(str(payload["state"])),
            snapshot_id=str(payload.get("snapshot_id") or ""),
            interaction_id=str(payload.get("interaction_id") or ""),
            seq=int(payload.get("seq") or 0),
            worker_id=str(payload.get("worker_id") or self.worker_id),
            entries=list(payload.get("entries") or []),
            completed_invocation_ids=list(
                payload.get("completed_invocation_ids") or []
            ),
            observation_refs=list(payload.get("observation_refs") or []),
            plan_phase=str(payload.get("plan_phase") or ""),
            reason=str(payload.get("reason") or ""),
        )
        snap_raw = payload.get("snapshot")
        snap: Optional[ToolSurfaceSnapshot] = None
        if isinstance(snap_raw, dict) and snap_raw.get("snapshot_id"):
            snap = ToolSurfaceSnapshot(
                snapshot_id=str(snap_raw["snapshot_id"]),
                caller=native_caller_from_mapping(snap_raw["caller"]),
                native_tool_names=tuple(snap_raw.get("native_tool_names") or ()),
                native_skill_keys=tuple(snap_raw.get("native_skill_keys") or ()),
                host_tool_names=tuple(snap_raw.get("host_tool_names") or ()),
                host_skill_keys=tuple(snap_raw.get("host_skill_keys") or ()),
                created_at=str(snap_raw.get("created_at") or ""),
                expires_at=str(snap_raw.get("expires_at") or ""),
                revoked=bool(snap_raw.get("revoked")),
            )
        with self.store.lock:
            self.store.runs[journal.correlation_id] = journal
            if journal.interaction_id:
                self.store.runs_by_interaction[journal.interaction_id] = (
                    journal.correlation_id
                )
            if snap is not None:
                self.store.snapshots[snap.snapshot_id] = snap
            for item in payload.get("invocations") or []:
                rec_map = dict(item.get("record") or {})
                klass_raw = rec_map.get("idempotency_class")
                rec = InvocationRecord(
                    invocation_id=str(rec_map["invocation_id"]),
                    snapshot_id=str(rec_map.get("snapshot_id") or ""),
                    tool_name=str(rec_map.get("tool_name") or ""),
                    input_digest=str(rec_map.get("input_digest") or ""),
                    idempotency_class=(
                        IdempotencyClass(klass_raw) if klass_raw else None
                    ),
                    attempt=int(rec_map.get("attempt") or 1),
                    outcome=rec_map.get("outcome"),
                )
                key = str(item.get("ledger_key") or "")
                if key:
                    self.store.invocations[key] = rec
                result = item.get("result")
                if result is not None:
                    self.store.invocation_results[rec.invocation_id] = str(result)
            envelopes = [EventEnvelope(**raw) for raw in (payload.get("outbox") or [])]
            if envelopes:
                self.store.outbox[caller.session_id] = envelopes
        return journal

    def persist_to_interaction(self, interaction: Any, correlation_id: str) -> None:
        if interaction is None or not correlation_id:
            return
        if self.get_run(correlation_id) is None:
            return
        payload = self.export_checkpoint(correlation_id)
        metrics = [
            m
            for m in list(getattr(interaction, "observability_metrics", None) or [])
            if not (isinstance(m, dict) and m.get("kind") == CHECKPOINT_KIND)
        ]
        metrics.append({"kind": CHECKPOINT_KIND, "payload": payload})
        spans = self.traces_for(correlation_id)
        metrics = [
            m
            for m in metrics
            if not (isinstance(m, dict) and m.get("kind") == TRACE_KIND)
        ]
        metrics.append({"kind": TRACE_KIND, "spans": spans})
        interaction.observability_metrics = metrics

    def checkpoint_from_interaction(self, interaction: Any) -> Optional[Dict[str, Any]]:
        if interaction is None:
            return None
        for metric in getattr(interaction, "observability_metrics", None) or []:
            if isinstance(metric, dict) and metric.get("kind") == CHECKPOINT_KIND:
                payload = metric.get("payload")
                if isinstance(payload, dict):
                    return payload
        return None

    # -- invocation ledger (HP-05) ----------------------------------------

    def begin_invocation(
        self,
        *,
        correlation_id: str,
        snapshot_id: str,
        tool_name: str,
        payload: Mapping[str, Any],
        idempotency_class: Optional[IdempotencyClass] = None,
    ) -> Tuple[InvocationRecord, Optional[str]]:
        reject_model_authority_fields(payload)
        self.require_usable(snapshot_id)
        digest = _input_digest(tool_name, payload)
        ledger_key = f"{correlation_id}:{tool_name}:{digest}"
        with self.store.lock:
            existing = self.store.invocations.get(ledger_key)
            if existing is not None:
                if existing.idempotency_class is IdempotencyClass.NON_RETRYABLE:
                    self.mark_recovery(
                        correlation_id, reason=f"non_retryable:{tool_name}"
                    )
                    raise HarnessContractError(
                        f"non-retryable tool {tool_name} cannot be replayed"
                    )
                cached = self.store.invocation_results.get(existing.invocation_id)
                retried = replace(existing, attempt=existing.attempt + 1)
                self.store.invocations[ledger_key] = retried
                reuse = (
                    existing.idempotency_class is IdempotencyClass.IDEMPOTENT
                    and cached is not None
                )
                return retried, cached if reuse else None
            record = InvocationRecord(
                invocation_id=f"inv-{uuid.uuid4().hex}",
                snapshot_id=snapshot_id,
                tool_name=tool_name,
                input_digest=digest,
                idempotency_class=idempotency_class,
                attempt=1,
            )
            self.store.invocations[ledger_key] = record
        journal = self.get_run(correlation_id)
        if journal is not None and journal.state is TurnRunState.RUNNING:
            self.transition(correlation_id, TurnRunState.WAITING_TOOL, reason=tool_name)
        self.record_span(
            correlation_id,
            "tool_invoke",
            invocation_id=record.invocation_id,
            tool_name=tool_name,
        )
        return record, None

    def finish_invocation(
        self,
        *,
        correlation_id: str,
        record: InvocationRecord,
        result: str,
        ok: bool = True,
    ) -> None:
        clipped = (
            result
            if len(result) <= MAX_OBSERVATION_CHARS
            else result[:MAX_OBSERVATION_CHARS]
        )
        with self.store.lock:
            if ok:
                self.store.invocation_results[record.invocation_id] = clipped
                journal = self.store.runs.get(correlation_id)
                if journal is not None:
                    journal.completed_invocation_ids.append(record.invocation_id)
                    journal.observation_refs.append(f"inv:{record.invocation_id}")
        if not ok and record.idempotency_class is IdempotencyClass.COMPENSATABLE:
            if record.tool_name in self._compensators:
                self.compensate(record.invocation_id)
        if not ok and record.idempotency_class is IdempotencyClass.NON_RETRYABLE:
            self.mark_recovery(correlation_id, reason=f"failed:{record.tool_name}")
            return
        journal = self.get_run(correlation_id)
        if journal is not None and journal.state is TurnRunState.WAITING_TOOL:
            self.transition(
                correlation_id,
                TurnRunState.RUNNING,
                reason="tool_ok" if ok else "tool_error",
            )

    def compensate(self, invocation_id: str) -> str:
        record = None
        with self.store.lock:
            for rec in self.store.invocations.values():
                if rec.invocation_id == invocation_id:
                    record = rec
                    break
        if record is None:
            raise HarnessContractError(f"unknown invocation {invocation_id}")
        fn = self._compensators.get(record.tool_name)
        if fn is None:
            raise HarnessContractError(
                f"no compensator registered for {record.tool_name}"
            )
        result = fn(record)
        return str(result)

    def register_compensator(self, tool_name: str, fn: Any) -> None:
        self._compensators[tool_name] = fn

    # -- outbox (HP-06) ---------------------------------------------------

    def append_event(
        self,
        *,
        session_id: str,
        kind: str,
        message_id: str,
        correlation_id: str,
        snapshot_id: str,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> EventEnvelope:
        with self.store.lock:
            stream = self.store.outbox.setdefault(session_id, [])
            if len(stream) >= MAX_EVENTS_PER_SESSION:
                stream.pop(0)
            seq = (stream[-1].sequence + 1) if stream else 1
            env = EventEnvelope(
                session_id=session_id,
                sequence=seq,
                cursor=f"{session_id}:{seq}",
                message_id=message_id,
                correlation_id=correlation_id,
                snapshot_id=snapshot_id,
                kind=kind,
                payload=dict(payload or {}),
            )
            stream.append(env)
        self.record_span(
            correlation_id,
            "event_append",
            session_id=session_id,
            sequence=env.sequence,
        )
        return env

    def replay_from(
        self, session_id: str, cursor: Optional[str] = None, *, limit: int = 500
    ) -> List[EventEnvelope]:
        stream = list(self.store.outbox.get(session_id, []))
        after = 0
        if cursor:
            try:
                after = int(str(cursor).rsplit(":", 1)[-1])
            except ValueError:
                after = 0
        out = [e for e in stream if e.sequence > after]
        return out[:limit]

    def journal_entries(
        self, correlation_id: str, *, after_seq: int = 0, limit: int = 100
    ) -> List[Dict[str, Any]]:
        journal = self.get_run(correlation_id)
        if journal is None:
            return []
        rows = [e for e in journal.entries if int(e.get("seq") or 0) > after_seq]
        return rows[: max(limit, 0)]

    # -- leases (HP-07) ---------------------------------------------------

    def acquire_session_lease(
        self, session_id: str, *, ttl_s: float = DEFAULT_LEASE_TTL_S
    ) -> None:
        if self.lease_backend is not None:
            rec = self.lease_backend.acquire(session_id, self.worker_id, ttl_s=ttl_s)
            expired = rec.get("expired_correlation_id") or ""
            if expired and expired in self.store.runs:
                run = self.store.runs[expired]
                if run.state not in TURN_RUN_TERMINAL:
                    self.mark_recovery(expired, reason="lease_expired")
            return
        now = time.monotonic()
        with self.store.lock:
            held = self.store.leases.get(session_id)
            if (
                held
                and held["worker_id"] != self.worker_id
                and held["expires_at"] > now
            ):
                raise SessionBusy(f"session {session_id} leased by {held['worker_id']}")
            if (
                held
                and held["expires_at"] <= now
                and held["worker_id"] != self.worker_id
            ):
                corr = held.get("correlation_id")
                if corr and corr in self.store.runs:
                    run = self.store.runs[corr]
                    if run.state not in TURN_RUN_TERMINAL:
                        run.state = TurnRunState.RECOVERY_REQUIRED
                        run.reason = "lease_expired"
                        run.entries.append(
                            {
                                "seq": run.seq + 1,
                                "state": TurnRunState.RECOVERY_REQUIRED.value,
                                "reason": "lease_expired",
                                "ts": datetime.now(timezone.utc).isoformat(),
                            }
                        )
                        run.seq += 1
            self.store.leases[session_id] = {
                "worker_id": self.worker_id,
                "expires_at": now + ttl_s,
                "correlation_id": "",
            }

    def bind_lease(self, session_id: str, correlation_id: str) -> None:
        if self.lease_backend is not None:
            self.lease_backend.bind(session_id, self.worker_id, correlation_id)
            return
        with self.store.lock:
            held = self.store.leases.get(session_id)
            if held and held["worker_id"] == self.worker_id:
                held["correlation_id"] = correlation_id

    def renew_session_lease(
        self, session_id: str, *, ttl_s: float = DEFAULT_LEASE_TTL_S
    ) -> None:
        if self.lease_backend is not None:
            self.lease_backend.renew(session_id, self.worker_id, ttl_s=ttl_s)
            return
        now = time.monotonic()
        with self.store.lock:
            held = self.store.leases.get(session_id)
            if not held or held["worker_id"] != self.worker_id:
                raise SessionBusy(f"session {session_id} not held by {self.worker_id}")
            held["expires_at"] = now + ttl_s

    def release_session_lease(self, session_id: str) -> None:
        if self.lease_backend is not None:
            self.lease_backend.release(session_id, self.worker_id)
            return
        with self.store.lock:
            held = self.store.leases.get(session_id)
            if held and held["worker_id"] == self.worker_id:
                self.store.leases.pop(session_id, None)

    def drain(self) -> None:
        self.store.draining = True

    def worker_lost(self, worker_id: str) -> None:
        with self.store.lock:
            drop = [
                sid
                for sid, held in self.store.leases.items()
                if held["worker_id"] == worker_id
            ]
            for sid in drop:
                held = self.store.leases.pop(sid)
                corr = held.get("correlation_id")
                if corr and corr in self.store.runs:
                    run = self.store.runs[corr]
                    if run.state not in TURN_RUN_TERMINAL:
                        run.state = TurnRunState.RECOVERY_REQUIRED
                        run.reason = "worker_lost"
                        run.entries.append(
                            {
                                "seq": run.seq + 1,
                                "state": TurnRunState.RECOVERY_REQUIRED.value,
                                "reason": "worker_lost",
                                "ts": datetime.now(timezone.utc).isoformat(),
                            }
                        )
                        run.seq += 1

    @property
    def is_draining(self) -> bool:
        return self.store.draining

    # -- host tools (HP-08) -----------------------------------------------

    def put_host_tools(self, session_id: str, names: List[str]) -> None:
        with self.store.lock:
            self.store.host_tools[session_id] = list(names)

    def put_host_skills(self, session_id: str, keys: List[str]) -> None:
        with self.store.lock:
            self.store.host_skills[session_id] = list(keys)

    def register_host_skill(
        self,
        session_id: str,
        skill_key: str,
        *,
        digest: str,
        spec: str,
        body: str,
    ) -> None:
        """Register the immutable materialization a host exposes for one session."""
        if spec not in ("jv", "claude"):
            raise HarnessContractError(f"unsupported host skill spec {spec!r}")
        materialization = SkillMaterialization(
            skill_key=skill_key,
            digest=digest,
            spec=spec,
            body=body,
        )
        with self.store.lock:
            keys = self.store.host_skills.setdefault(session_id, [])
            if skill_key not in keys:
                keys.append(skill_key)
            self.store.host_skill_materializations[(session_id, skill_key)] = (
                materialization
            )

    def host_skill_materialization(
        self, session_id: str, skill_key: str
    ) -> Optional[SkillMaterialization]:
        return self.store.host_skill_materializations.get((session_id, skill_key))

    def revoke_host_tool(self, session_id: str, name: str) -> None:
        with self.store.lock:
            self.store.revoked_host_tools.setdefault(session_id, set()).add(name)

    def register_host_runner(self, session_id: str, name: str, fn: Any) -> None:
        self._host_runners[(session_id, name)] = fn

    def host_runner(self, session_id: str, name: str) -> Any:
        return self._host_runners.get((session_id, name))

    # -- skills (HP-09) ---------------------------------------------------

    def sign_digest(self, digest: str) -> str:
        if not self.skill_signing_key:
            return ""
        return hmac.new(
            self.skill_signing_key.encode(), digest.encode(), hashlib.sha256
        ).hexdigest()

    def register_manifest(self, manifest: SkillManifest) -> None:
        if manifest.spec not in ("jv", "claude"):
            raise HarnessContractError(
                f"unsupported skill spec {manifest.spec!r}; only jv and claude"
            )
        if manifest.digest in self.store.revoked_manifests:
            raise HarnessContractError(f"revoked skill digest {manifest.digest}")
        if self.skill_signing_key:
            expected = self.sign_digest(manifest.digest)
            if not hmac.compare_digest(manifest.signature or "", expected):
                raise HarnessContractError("invalid skill signature")
        with self.store.lock:
            self.store.skill_manifests[manifest.digest] = manifest

    def publish_manifest(self, manifest: SkillManifest) -> None:
        self.register_manifest(manifest)

    def revoke_manifest(self, digest: str) -> None:
        with self.store.lock:
            self.store.revoked_manifests.add(digest)
            self.store.skill_manifests.pop(digest, None)

    def activate_skill(
        self,
        caller: NativeCaller,
        snapshot_id: str,
        digest: str,
        *,
        trust_tier: str = "trusted",
    ) -> StageRecord:
        snap = self.require_usable(snapshot_id)
        if digest in self.store.revoked_manifests:
            raise HarnessContractError(f"revoked skill digest {digest}")
        manifest = self.store.skill_manifests.get(digest)
        if manifest is None:
            raise HarnessContractError(f"unknown skill digest {digest}")
        if trust_tier == "untrusted" and (
            self.isolation_backend not in APPROVED_ISOLATION_BACKENDS
        ):
            raise SkillIsolationRefused(
                "untrusted skill requires an approved isolation backend "
                f"(got {self.isolation_backend!r}; subprocess is not a sandbox)"
            )
        path = f"stage/{caller.session_id}/{snapshot_id}/{digest}"
        rec = StageRecord(
            caller=caller, snapshot_id=snapshot_id, digest=digest, path=path
        )
        with self.store.lock:
            self.store.stages[path] = rec
        self.record_span(
            snap.snapshot_id,
            "skill_activate",
            digest=digest,
            caller=caller.as_tuple(),
        )
        return rec

    def cleanup_stage(self, path: str) -> None:
        with self.store.lock:
            rec = self.store.stages.get(path)
            if rec is not None:
                rec.active = False

    # -- traces (HP-10) ---------------------------------------------------

    def record_span(self, correlation_id: str, name: str, **fields: Any) -> None:
        if not correlation_id:
            return
        if "caller" in fields and hasattr(fields["caller"], "as_tuple"):
            fields = dict(fields)
            fields["caller"] = fields["caller"].as_tuple()
        redacted = {k: v for k, v in fields.items() if k not in ("secret", "password")}
        _log.info(
            "harness.span %s",
            json.dumps(
                {"correlation_id": correlation_id, "name": name, **redacted},
                default=str,
            ),
        )
        with self.store.lock:
            spans = self.store.traces.setdefault(correlation_id, [])
            spans.append(
                {
                    "name": name,
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "worker_id": self.worker_id,
                    **redacted,
                }
            )
            overflow = len(spans) - self.max_trace_spans
            if overflow > 0:
                del spans[:overflow]

    def traces_for(self, correlation_id: str) -> List[Dict[str, Any]]:
        return list(self.store.traces.get(correlation_id, []))

    def replay_document(self, correlation_id: str) -> Dict[str, Any]:
        journal = self.store.runs.get(correlation_id)
        caller = journal.caller.as_tuple() if journal else None
        traces = self.traces_for(correlation_id)
        for span in traces:
            other = span.get("caller")
            if other and caller and tuple(other) != caller:
                raise HarnessContractError("foreign-user content in trace")
        return {
            "correlation_id": correlation_id,
            "caller": journal.caller.to_mapping() if journal else {},
            "state": journal.state.value if journal else None,
            "snapshot_id": journal.snapshot_id if journal else None,
            "spans": traces,
            "journal": journal.entries if journal else [],
            "invocations": list(journal.completed_invocation_ids) if journal else [],
        }

    def mark_background(self, correlation_id: str) -> None:
        journal = self.get_run(correlation_id)
        if journal is None or journal.state in TURN_RUN_TERMINAL:
            return
        journal.plan_phase = "background"

    def prune_retention(self) -> None:
        with self.store.lock:
            for sid, stream in list(self.store.outbox.items()):
                if len(stream) > MAX_EVENTS_PER_SESSION:
                    self.store.outbox[sid] = stream[-MAX_EVENTS_PER_SESSION:]
            for corr, spans in list(self.store.traces.items()):
                if len(spans) > self.max_trace_spans:
                    self.store.traces[corr] = spans[-self.max_trace_spans :]

    # -- internals --------------------------------------------------------

    def _require_run(self, correlation_id: str) -> TurnRunJournal:
        journal = self.store.runs.get(correlation_id)
        if journal is None:
            raise HarnessContractError(f"unknown TurnRun {correlation_id}")
        return journal

    def _append_journal(
        self, journal: TurnRunJournal, dst: TurnRunState, reason: str
    ) -> None:
        journal.seq += 1
        journal.state = dst
        journal.reason = reason
        journal.entries.append(
            {
                "seq": journal.seq,
                "state": dst.value,
                "reason": reason,
                "snapshot_id": journal.snapshot_id,
                "correlation_id": journal.correlation_id,
                "ts": datetime.now(timezone.utc).isoformat(),
                "worker_id": self.worker_id,
            }
        )


def _input_digest(tool_name: str, payload: Mapping[str, Any]) -> str:
    blob = json.dumps(
        {"tool": tool_name, "args": dict(payload)}, sort_keys=True, default=str
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def get_runtime() -> HarnessRuntime:
    global _runtime
    with _runtime_guard:
        if _runtime is None:
            _runtime = HarnessRuntime()
        return _runtime


def reset_runtime(runtime: Optional[HarnessRuntime] = None) -> HarnessRuntime:
    global _runtime
    with _runtime_guard:
        _runtime = runtime if runtime is not None else HarnessRuntime()
        return _runtime


def set_runtime(runtime: HarnessRuntime) -> None:
    global _runtime
    with _runtime_guard:
        _runtime = runtime


__all__ = [
    "APPROVED_ISOLATION_BACKENDS",
    "CHECKPOINT_KIND",
    "AdmissionRefused",
    "HarnessRuntime",
    "HarnessStore",
    "MAX_EVENTS_PER_SESSION",
    "MAX_OBSERVATION_CHARS",
    "MAX_TRACE_SPANS",
    "TRACE_KIND",
    "SAME_SESSION_POLICY",
    "SessionBusy",
    "SkillIsolationRefused",
    "SkillManifest",
    "StageRecord",
    "TurnRunJournal",
    "get_runtime",
    "reset_runtime",
    "set_runtime",
]
