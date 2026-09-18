"""Durable dump/load of a HarnessStore (HP-06 transport, HP-11 retention).

JSON file on disk. Two workers share a path. Not Redis. Not a claim of
exactly-once channel send.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from jvagent.harness.contracts import (
    EventEnvelope,
    IdempotencyClass,
    InvocationRecord,
    SkillMaterialization,
    ToolSurfaceSnapshot,
    TurnRunState,
    native_caller_from_mapping,
)
from jvagent.harness.runtime import (
    HarnessStore,
    SkillManifest,
    StageRecord,
    TurnRunJournal,
)

STORE_FORMAT = 1


def _tuple_key(parts: Tuple[str, ...]) -> str:
    return "\x1f".join(parts)


def _split_pair(raw: str) -> Tuple[str, str]:
    a, b = raw.split("\x1f", 1)
    return a, b


def _split_triple(raw: str) -> Tuple[str, str, str]:
    a, rest = raw.split("\x1f", 1)
    b, c = rest.split("\x1f", 1)
    return a, b, c


def dump_store(store: HarnessStore, path: Path) -> None:
    """Write identities, snapshots, journals, ledger, outbox, traces, skills."""
    with store.lock:
        snapshots = {}
        for sid, snap in store.snapshots.items():
            blob = asdict(snap)
            blob["caller"] = snap.caller.to_mapping()
            snapshots[sid] = blob
        current = {_tuple_key(k): v for k, v in store.current_snapshot.items()}
        generation = {_tuple_key(k): v for k, v in store.generation.items()}
        identities = {_tuple_key(k): v for k, v in store.identities.items()}
        conversations = {_tuple_key(k): v for k, v in store.conversations.items()}
        runs: Dict[str, Dict[str, Any]] = {}
        for corr, journal in store.runs.items():
            runs[corr] = {
                "correlation_id": journal.correlation_id,
                "caller": journal.caller.to_mapping(),
                "state": journal.state.value,
                "snapshot_id": journal.snapshot_id,
                "interaction_id": journal.interaction_id,
                "seq": journal.seq,
                "worker_id": journal.worker_id,
                "entries": list(journal.entries),
                "completed_invocation_ids": list(journal.completed_invocation_ids),
                "observation_refs": list(journal.observation_refs),
                "plan_phase": journal.plan_phase,
                "reason": journal.reason,
            }
        invocations: Dict[str, Dict[str, Any]] = {}
        for key, rec in store.invocations.items():
            rec_map = asdict(rec)
            klass = rec.idempotency_class
            rec_map["idempotency_class"] = klass.value if klass else None
            invocations[key] = rec_map
        outbox = {
            sid: [asdict(e) for e in events] for sid, events in store.outbox.items()
        }
        manifests = {digest: asdict(m) for digest, m in store.skill_manifests.items()}
        stages = {}
        for p, rec in store.stages.items():
            stages[p] = {
                "caller": rec.caller.to_mapping(),
                "snapshot_id": rec.snapshot_id,
                "digest": rec.digest,
                "path": rec.path,
                "active": rec.active,
            }
        payload = {
            "format": STORE_FORMAT,
            "dumped_at": datetime.now(timezone.utc).isoformat(),
            "identities": identities,
            "conversations": conversations,
            "snapshots": snapshots,
            "current_snapshot": current,
            "generation": generation,
            "runs": runs,
            "runs_by_interaction": dict(store.runs_by_interaction),
            "invocations": invocations,
            "invocation_results": dict(store.invocation_results),
            "outbox": outbox,
            "traces": {k: list(v) for k, v in store.traces.items()},
            "skill_manifests": manifests,
            "stages": stages,
            "host_tools": {k: list(v) for k, v in store.host_tools.items()},
            "host_skills": {k: list(v) for k, v in store.host_skills.items()},
            "host_skill_materializations": {
                _tuple_key(key): asdict(value)
                for key, value in store.host_skill_materializations.items()
            },
            "revoked_host_tools": {
                k: sorted(v) for k, v in store.revoked_host_tools.items()
            },
            "revoked_manifests": sorted(getattr(store, "revoked_manifests", set())),
            "leases": dict(store.leases),
            "draining": store.draining,
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, default=str), encoding="utf-8")


def load_store(path: Path, store: Optional[HarnessStore] = None) -> HarnessStore:
    """Hydrate a store from :func:`dump_store` JSON. Callables are not restored."""
    target = store or HarnessStore()
    raw: Mapping[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    with target.lock:
        target.identities = {
            _split_pair(k): v for k, v in (raw.get("identities") or {}).items()
        }
        target.conversations = {
            _split_pair(k): v for k, v in (raw.get("conversations") or {}).items()
        }
        snaps: Dict[str, ToolSurfaceSnapshot] = {}
        for sid, blob in (raw.get("snapshots") or {}).items():
            snaps[sid] = ToolSurfaceSnapshot(
                snapshot_id=str(blob["snapshot_id"]),
                caller=native_caller_from_mapping(blob["caller"]),
                native_tool_names=tuple(blob.get("native_tool_names") or ()),
                native_skill_keys=tuple(blob.get("native_skill_keys") or ()),
                host_tool_names=tuple(blob.get("host_tool_names") or ()),
                host_skill_keys=tuple(blob.get("host_skill_keys") or ()),
                created_at=str(blob.get("created_at") or ""),
                expires_at=str(blob.get("expires_at") or ""),
                revoked=bool(blob.get("revoked")),
            )
        target.snapshots = snaps
        target.current_snapshot = {
            _split_triple(k): v for k, v in (raw.get("current_snapshot") or {}).items()
        }
        target.generation = {
            _split_triple(k): int(v) for k, v in (raw.get("generation") or {}).items()
        }
        runs: Dict[str, TurnRunJournal] = {}
        for corr, blob in (raw.get("runs") or {}).items():
            runs[corr] = TurnRunJournal(
                correlation_id=str(blob["correlation_id"]),
                caller=native_caller_from_mapping(blob["caller"]),
                state=TurnRunState(str(blob["state"])),
                snapshot_id=str(blob.get("snapshot_id") or ""),
                interaction_id=str(blob.get("interaction_id") or ""),
                seq=int(blob.get("seq") or 0),
                worker_id=str(blob.get("worker_id") or ""),
                entries=list(blob.get("entries") or []),
                completed_invocation_ids=list(
                    blob.get("completed_invocation_ids") or []
                ),
                observation_refs=list(blob.get("observation_refs") or []),
                plan_phase=str(blob.get("plan_phase") or ""),
                reason=str(blob.get("reason") or ""),
            )
        target.runs = runs
        target.runs_by_interaction = dict(raw.get("runs_by_interaction") or {})
        invocations: Dict[str, InvocationRecord] = {}
        for key, rec_map in (raw.get("invocations") or {}).items():
            klass_raw = rec_map.get("idempotency_class")
            invocations[key] = InvocationRecord(
                invocation_id=str(rec_map["invocation_id"]),
                snapshot_id=str(rec_map.get("snapshot_id") or ""),
                tool_name=str(rec_map.get("tool_name") or ""),
                input_digest=str(rec_map.get("input_digest") or ""),
                idempotency_class=(IdempotencyClass(klass_raw) if klass_raw else None),
                attempt=int(rec_map.get("attempt") or 1),
                outcome=rec_map.get("outcome"),
            )
        target.invocations = invocations
        target.invocation_results = {
            k: str(v) for k, v in (raw.get("invocation_results") or {}).items()
        }
        outbox: Dict[str, List[EventEnvelope]] = {}
        for sid, events in (raw.get("outbox") or {}).items():
            outbox[sid] = [EventEnvelope(**e) for e in events]
        target.outbox = outbox
        target.traces = {k: list(v) for k, v in (raw.get("traces") or {}).items()}
        manifests: Dict[str, SkillManifest] = {}
        for digest, blob in (raw.get("skill_manifests") or {}).items():
            manifests[digest] = SkillManifest(
                skill_key=str(blob["skill_key"]),
                source=str(blob.get("source") or ""),
                digest=str(blob["digest"]),
                declared_tools=tuple(blob.get("declared_tools") or ()),
                capabilities=tuple(blob.get("capabilities") or ()),
                trust_tier=str(blob.get("trust_tier") or "trusted"),
                spec=str(blob.get("spec") or "jv"),
                signature=str(blob.get("signature") or ""),
                body=str(blob.get("body") or ""),
            )
        target.skill_manifests = manifests
        stages: Dict[str, StageRecord] = {}
        for p, blob in (raw.get("stages") or {}).items():
            stages[p] = StageRecord(
                caller=native_caller_from_mapping(blob["caller"]),
                snapshot_id=str(blob["snapshot_id"]),
                digest=str(blob["digest"]),
                path=str(blob["path"]),
                active=bool(blob.get("active", True)),
            )
        target.stages = stages
        target.host_tools = {
            k: list(v) for k, v in (raw.get("host_tools") or {}).items()
        }
        target.host_skills = {
            k: list(v) for k, v in (raw.get("host_skills") or {}).items()
        }
        target.host_skill_materializations = {
            _split_pair(key): SkillMaterialization(
                skill_key=str(value["skill_key"]),
                digest=str(value["digest"]),
                spec=str(value["spec"]),
                body=str(value["body"]),
            )
            for key, value in (raw.get("host_skill_materializations") or {}).items()
        }
        target.revoked_host_tools = {
            k: set(v) for k, v in (raw.get("revoked_host_tools") or {}).items()
        }
        target.revoked_manifests = set(raw.get("revoked_manifests") or [])
        target.leases = dict(raw.get("leases") or {})
        target.draining = bool(raw.get("draining"))
    return target


__all__ = ["STORE_FORMAT", "dump_store", "load_store"]
