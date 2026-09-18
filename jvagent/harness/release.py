"""Contract versions and deployment matrix (HP-12)."""

from __future__ import annotations

from typing import Dict

from jvagent.harness.contracts import CONTRACT_VERSION

NATIVE_CALLER_VERSION = CONTRACT_VERSION
SNAPSHOT_VERSION = CONTRACT_VERSION
PROVIDER_VERSION = CONTRACT_VERSION
EVENT_ENVELOPE_VERSION = CONTRACT_VERSION

# guaranteed | degraded | unsupported
# JSON/SQLite are single-writer. Active-active needs a store that shares the
# HarnessStore (or Redis/Dynamo leases). Never treat process-local as distributed.
DEPLOYMENT_MATRIX: Dict[str, Dict[str, str]] = {
    "json": {
        "local": "guaranteed",
        "single-worker": "guaranteed",
        "active-active": "unsupported",
    },
    "sqlite": {
        "local": "guaranteed",
        "single-worker": "guaranteed",
        "active-active": "unsupported",
    },
    "mongodb": {
        "local": "guaranteed",
        "single-worker": "guaranteed",
        "active-active": "degraded",
    },
    "dynamodb": {
        "local": "guaranteed",
        "single-worker": "guaranteed",
        "active-active": "guaranteed",
    },
    "postgres": {
        "local": "guaranteed",
        "single-worker": "guaranteed",
        "active-active": "degraded",
    },
}


def cell(backend: str, mode: str) -> str:
    row = DEPLOYMENT_MATRIX.get(backend)
    if row is None:
        return "unsupported"
    return row.get(mode, "unsupported")


def release_record(*, digest: str, topology: str) -> dict:
    return {
        "artifact_digest": digest,
        "contract_versions": {
            "native_caller": NATIVE_CALLER_VERSION,
            "snapshot": SNAPSHOT_VERSION,
            "provider": PROVIDER_VERSION,
            "event_envelope": EVENT_ENVELOPE_VERSION,
        },
        "topology": topology,
        "matrix": DEPLOYMENT_MATRIX,
        "limitations": [
            "JSON and SQLite are single-writer; do not claim active-active.",
            "Outbox dump/load is JSON on disk via dump_store; Redis/Dynamo streams are not implied.",
            "Session leases: in-process default; file/redis/dynamo adapters require an explicit client or path (no silent fallback).",
            "Subprocess skill execution is development containment, not a sandbox.",
            "Untrusted skills refuse unless gvisor/firecracker/nsjail is on PATH.",
            "Exactly-once third-party effects require an idempotency mechanism.",
            "HostCapabilityProvider.invoke runs a registered host runner; native tools stay on wrap_action_tool.",
        ],
        "rollback": "revert to process-local caches/bus; disable drain and shared store.",
    }


__all__ = [
    "DEPLOYMENT_MATRIX",
    "EVENT_ENVELOPE_VERSION",
    "NATIVE_CALLER_VERSION",
    "PROVIDER_VERSION",
    "SNAPSHOT_VERSION",
    "cell",
    "release_record",
]
