# Process-local state inventory (HP-01)

**Date:** 2026-09-17
**Scope:** jvagent process memory that does not survive restart and is not shared across workers.
**Replacement rule:** record here; do not replace in HP-01.

Identity column uses ADR-0054 names (`NativeCaller`, `snapshot_id`) when the *target* key is known. Today many rows have no identity key.

| Component | Owner | Process scope | Identity key today | Survives restart? | Cross-worker leak? | Replacement | Regression |
|---|---|---|---|---|---|---|---|
| `_agent_bus_registry` + session queues/subscribers | ResponseBus | per `agent_id` dict | session_id on queues; not NativeCaller | no | yes — other worker has empty bus | HP-06 outbox | `tests/conformance/test_process_local_baseline.py::test_response_bus_registry_is_process_local`; replay overlap: `tests/action/response/test_streaming_dedup.py` |
| Tool surface cache `_TOOL_SURFACE_CACHE` | orchestrator/catalog | per snapshot+NativeCaller | snapshot_id + caller | no | mitigated by snapshot key (HP-03) | HP-03 done | `test_tool_surface_cache_is_keyed_by_snapshot_and_caller` |
| Skill discovery `_SKILL_DISCOVERY_CACHE` | orchestrator/skills | process dict | path/mtime tuple, not snapshot | no | yes | HP-03 | `test_skill_discovery_cache_is_process_local` |
| Host skill `_providers` | orchestrator/skill_providers | process list | none (agent arg at collect) | no | yes — global overlay | HP-08 | `test_host_skill_providers_are_process_global` |
| `MODEL_BREAKER` / `_states` | model/resilience | process-wide | loop_id | no | yes — breaker not shared | HP-07 optional shared backend | `test_model_breaker_is_process_local` |
| `turn_cache` ContextVar | orchestrator/turn_cache | asyncio task | implicit task | no | no — keep | **keep** | `test_turn_cache_is_contextvar_not_module_dict` |
| Memory lock managers | memory/lock_manager | per loop+key | memory_id+user_id / session | no | yes | HP-02/07 store-backed | `test_memory_locks_are_in_process` |
| Distributed lease `_inproc_locks` | core/distributed_lease | process fallback | lease key | no | yes if used as if distributed | HP-07 | inventory only |
| Embed `_interact_tasks` | embed/interact | process set | none | no | yes | HP-04 TurnRun | inventory only |
| MCP `user_clients` / `tool_cache` | action/mcp | action instance | user / server | no | yes | out of harness core | inventory only |
| Webhook `_seen_wamids` | meta_webhook_dedup | process OrderedDict | wamid | no | yes — duplicate webhooks | channel-local; not HP core | inventory only |
| Rate limiter timestamps | interact/rate_limiter | module singleton | none | no | yes | document | inventory only |
| Agent/action TTL caches | core/cache | process | agent_id | no | stale reads only | keep with TTL; not snapshot | inventory only |
| App `_cached_app` | core/app | process singleton | none | no | N/A single App | keep | inventory only |
| Sandbox / `STAGED_SKILLS_DIR` | core/sandbox, code_execution | filesystem | user path, not snapshot | partial (disk) | path collision if shared FS | HP-09 snapshot staging | inventory only |
| Task monitor `_TICK_ACTIONS_INITIALIZED` | task_monitor | process latch | none | no | duplicate ticks possible | document | inventory only |
| Startup `_startup_completed` | core/startup | process latch | none | no | ok | keep | inventory only |
| Circuit/profile ContextVars | core/profiling | task-local | none | no | no | keep | inventory only |
| Messenger coalescer buffers | facebook_action | process | sender key | no | yes | channel-local | inventory only |
| WhatsApp `_user_locks` / media batch | whatsapp | process | user | no | yes | channel-local | inventory only |

## Current failure behaviour (characterization)

| Event | Today | Target HP |
|---|---|---|
| Process restart mid-SSE | subscribers and queues gone; client reconnects to empty bus | HP-06 |
| Overlapping SSE replay | deduped by message id in-process | HP-06 must preserve; see `test_streaming_dedup.py` |
| Concurrent distinct sessions | ContextVar turn cache isolates tasks; tool cache is per-agent so two users of one agent share assembled surface | HP-03 |
| Concurrent same session | in-process conversation lock; not cross-worker | HP-02/07 |
| Model error / interrupted tool | loop terminal via existing guards; no TurnRun journal | HP-04/05 |
| Host skill overlay | every agent in process sees registered providers | HP-08 |

## Benchmark fixtures (stubs)

Named in `tests/conformance/test_process_local_baseline.py` and skipped until HP-11:

- short chat
- tool-rich chat
- streaming
- long session
- many-user concurrency

## Keep vs replace

Keep as-is: `turn_cache` ContextVar, App singleton cache, TTL agent/action caches (not tool/skill snapshots).
