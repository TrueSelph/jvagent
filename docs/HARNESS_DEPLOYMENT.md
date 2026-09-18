# Harness deployment matrix (HP-12)

Contract version: `jvagent.harness.contracts.CONTRACT_VERSION` (`1.0.0`).

Native identity, snapshot, provider, and event-envelope versions share that tag.
See `jvagent.harness.release.release_record()`.

## Guarantee split

Process-local caches, buses, and in-process leases are **not** distributed
guarantees. JSON and SQLite remain single-writer. Active-active requires a
shared `HarnessStore` (tests) or Redis/Dynamo session leases (HP-07).

Same-session policy is **lease** (`SAME_SESSION_POLICY`). A second worker that
does not hold the lease is refused (`SessionBusy`). Drain stops admissions and
keeps outbox replay.

## Matrix

Cells are `guaranteed` / `degraded` / `unsupported`. Never blank.

| Backend | local | single-worker | active-active |
|---|---|---|---|
| json | guaranteed | guaranteed | unsupported |
| sqlite | guaranteed | guaranteed | unsupported |
| mongodb | guaranteed | guaranteed | degraded |
| dynamodb | guaranteed | guaranteed | guaranteed |
| postgres | guaranteed | guaranteed | degraded |

`degraded` means identity upsert and outbox work in-process / via the harness
store, but cluster leases are not the Dynamo/Redis backends unless configured.

## Migration from v1 process-local assumptions

- Tool/skill caches are keyed by `snapshot_id` + NativeCaller, not `agent_id` alone.
- ResponseBus remains fan-out; durable order lives on the outbox (`HarnessStore.outbox`).
- `register_host_skill_provider` is a shim. Hosts should put session tools/skills on the runtime and serve them through `HostCapabilityProvider`.
- Embed `_interact_tasks` is still a cancel handle. Turn truth is the TurnRun journal.

## Rollback

Revert to process-local caches/bus; disable drain and shared store. See
`release_record()["rollback"]`.
