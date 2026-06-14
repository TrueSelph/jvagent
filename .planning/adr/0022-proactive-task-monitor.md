# ADR-0022: Proactive Task Monitor

## Status

Accepted

## Context

jvagent had fragmented proactive execution: `TaskCreationInteractAction` scheduled tasks with an ad-hoc schema, `TaskTriggerInteractAction` fired them on user turns, and `TaskDispatcher` dispatched via `PersonaAction` only — bypassing the Orchestrator, tools, and skills. There was no queue semantics, prerequisite model, or single finalizer.

## Decision

Unify proactive work behind **TaskMonitor** and a structured **`ProactiveTaskSpec`** (`spec_version: 2`) on `Conversation.tasks`:

1. **Single queue** — `TaskStore.enqueue_proactive()` always creates `task_type="PROACTIVE"`, `status="pending"`.
2. **One active PROACTIVE per conversation** — `claim_proactive()` under `conversation_mutation_lock`; active `SKILL` tasks block the monitor.
3. **Eligibility engine** — schedule (`not_before`/`not_after`), prerequisites (`requires_tasks`), and event triggers (`trigger_on`, keyword/mood) in `task_eligibility.py`.
4. **Dual dispatch paths**:
   - Schedule-only → `TaskMonitor.tick()` spawns `InteractWalker` (full Orchestrator pipeline).
   - Event triggers → `TaskTriggerInteractAction` claims and attaches on the user turn; Orchestrator finalizes.
5. **Enqueue surfaces** — `queue_task` orchestrator tool, `TaskCreationInteractAction`, `Agent.enqueue_proactive_task()`, `embed.enqueue_proactive_task()`.
6. **Forward-only** — `TaskDispatcher` removed; legacy ad-hoc rows are ignored by eligibility (no migration shims).

## Consequences

- Proactive tasks run with full tool/skill surface via Orchestrator.
- `Agent.send_proactive_message()` remains for canned (pre-formed) messages; queued tasks are for agentic completion.
- External cron can call `GET /api/proactive/tick/{agent_id}` when the native scheduler is disabled (serverless).
- Lifecycle webhooks fire through `TaskStore` → `callback.py`.
