# Task Tracking

Task lifecycle management is now handled by `TaskStore`, scoped per conversation and exposed through `visitor.tasks`.

## Overview

- **Storage**: `Conversation.tasks` is the canonical store (list of task dicts, persisted on the conversation node).
- **Writer**: `TaskStore` is the single write path for create/update/complete/fail/cancel.
- **Scope**: One store instance per conversation (`TaskStore(conversation)`), lazily available as `InteractWalker.tasks`.
- **Consumers**: Orchestrator think-act-observe loop (flow continuation), interview flows, proactive task creation/dispatch/trigger, ReplyAction/PersonaAction egress context, and response payloads.

## Task Model

Each entry in `tasks` uses this normalized shape:

```json
{
  "id": "SignupInterviewSkill:907b4a8af2e5",
  "type": "INTERVIEW",
  "description": "Signup interview in progress",
  "owner_action": "SignupInterviewSkill",
  "status": "active",
  "data": {
    "trigger_at": null,
    "trigger_condition": null,
    "steps": [],
    "iterations": 0
  },
  "created_at": "2026-04-18T19:01:05.853007+00:00",
  "updated_at": "2026-04-18T19:01:05.853017+00:00",
  "last_heartbeat_at": "2026-04-18T19:01:05.853017+00:00",
  "terminal_at": null
}
```

### Status model

- Active statuses: `pending`, `active`
- Terminal statuses: `completed`, `failed`, `cancelled`, `timed_out`, `max_iterations`, `superseded`
- Terminal statuses are one-way; completed tasks remain in the list for audit history.

## Preferred API (`visitor.tasks`)

Use `TaskStore` directly from actions:

```python
async with visitor.tasks.track(
    description="Agentic task: ...",
    task_type="AGENTIC_LOOP",
    owner_action=self.get_class_name(),
    data={"skills": self.skills},
) as task:
    task.add_event("thinking", iteration=1)
    task.add_event("tool_call", iteration=1, details={"count": 2})
    task.update(current_phase="tool_dispatch")
    task.complete(summary="Done")
```

For proactive queue entries (`ProactiveTaskSpec`, `spec_version: 2`):

```python
from jvagent.memory.task_proactive import ProactiveTaskSpec

spec = ProactiveTaskSpec(
    directive="Follow up tomorrow",
    context="User asked for a check-in",
    not_before="2026-04-19T09:00:00+00:00",
    trigger_on="schedule",
    priority=0,
)
handle = await visitor.tasks.enqueue_proactive(
    spec,
    owner_action=self.get_class_name(),
    title="Follow up tomorrow",
)
# pending → active (claimed by TaskMonitor or TaskTrigger) → completed
```

## Read accessors

`Conversation` exposes read-only helpers for inspecting the task list:

- `conversation.get_tasks(status=..., owner_action=...)`
- `conversation.get_task(task_id=..., task_type=..., description=..., owner_action=..., status=...)`
- `conversation.get_active_tasks_for_context()`

All writes go through `TaskStore` (`visitor.tasks` or `TaskStore(conversation)`).
Use `singleton_action=True` when an action should keep at most one active task — the
prior active entry is automatically transitioned to `superseded` and a new entry is
created so lineage is preserved.

## Lifecycle callbacks

`TaskStore` emits callbacks through `jvagent/core/callback.py`:

- `task_created`
- `task_updated`
- `task_completed`
- `task_failed`
- `task_cancelled`

Webhook URLs can be configured with:

- `JVAGENT_TASK_CREATED_WEBHOOK_URL`
- `JVAGENT_TASK_UPDATED_WEBHOOK_URL`
- `JVAGENT_TASK_COMPLETED_WEBHOOK_URL`
- `JVAGENT_TASK_FAILED_WEBHOOK_URL`
- `JVAGENT_TASK_CANCELLED_WEBHOOK_URL`

## PROACTIVE queue lifecycle

| Status | Meaning |
|--------|---------|
| `pending` | In queue; eligibility engine evaluates schedule/prereqs/events |
| `active` | Claimed; Orchestrator turn in flight (at most one per conversation) |
| `completed` / `failed` / `cancelled` | Terminal; finalizer or explicit store API |

`ProactiveTaskSpec` fields: `directive`, `context`, `not_before`, `not_after`, `priority`, `requires_tasks`, `trigger_on` (`schedule` \| `user_message` \| `keyword` \| `mood` \| `any`), `trigger_keyword`, `trigger_mood`, `skill`, `max_attempts`.

Queued proactive tasks persist `spec_version: 2` in `task.data`:

```json
{
  "id": "TaskCreationInteractAction:abc123",
  "task_type": "PROACTIVE",
  "status": "pending",
  "description": "Follow up tomorrow",
  "owner_action": "TaskCreationInteractAction",
  "data": {
    "spec_version": 2,
    "directive": "Check in with the user about scheduling",
    "context": "User asked for a reminder",
    "not_before": "2026-06-08T09:00:00+00:00",
    "trigger_on": "schedule",
    "priority": 0,
    "max_attempts": 3
  }
}
```

Legacy rows that use ad-hoc `trigger_at` / `trigger_condition` without `spec_version: 2` are ignored by the eligibility engine (forward-only; no migration).

### Agent wiring

Install the proactive pipeline on agents that need scheduled or event-triggered follow-ups:

```yaml
actions:
  - action: jvagent/task_trigger_interact_action   # weight -250 — event bridge on user turns
    context:
      enabled: true

  - action: jvagent/task_creation_interact_action    # weight 200 — post-turn LLM scheduler
    context:
      enabled: true

  - action: jvagent/task_monitor                   # periodic dispatch via Orchestrator
    context:
      enabled: true
      tick_interval: "every 2 minutes"
      max_parallel_conversations: 5
      terminal_ttl_days: 0   # optional: prune old terminal PROACTIVE rows
```

The orchestrator reference agent at `examples/jvagent_app/agents/jvagent/orchestrator_agent/agent.yaml` includes this stack.

### Dispatch paths

| Trigger | Who claims the task | When it runs |
|---------|---------------------|--------------|
| `trigger_on: schedule` (default) | `TaskMonitor` | Native scheduler tick or `GET /api/proactive/tick/{agent_id}` |
| `keyword` / `mood` / `user_message` / `any` | `TaskTriggerInteractAction` | Same turn as the matching user message (Orchestrator finalizes) |

At most **one** `PROACTIVE` task is `active` per conversation. An active `SKILL` control-task blocks the monitor until the skill session completes.

### Scheduler setup

`TaskMonitor` relies on jvspatial's `SchedulerService` for periodic ticks. jvagent bootstraps it during `pre_startup_bootstrap` via `jvagent/core/scheduler_bootstrap.py`:

- If any `agents/**/agent.yaml` lists `jvagent/task_monitor`, **`server.scheduler_enabled` is auto-enabled** unless you override it.
- You can also set explicitly in `app.yaml`:

```yaml
config:
  server:
    scheduler_enabled: true
    scheduler_interval: 1   # seconds between scheduler thread checks
```

Or via environment: `JVSPATIAL_SCHEDULER_ENABLED=true`, `JVSPATIAL_SCHEDULER_INTERVAL=1`.

**Serverless** (`--serverless` / `SERVERLESS_MODE=true`): the native background scheduler does not start. Poll proactively instead:

```http
GET /api/proactive/tick/{agent_id}?api_key=<webhook-key>
```

Mint the URL from `TaskCreationInteractAction.get_webhook_url()` or list webhooks at `GET /api/proactive/webhooks/{agent_id}` (admin).

If you see `TaskMonitor: scheduler service unavailable after startup` on a **non-serverless** deploy, enable the scheduler as above and restart. The HTTP tick endpoint still works as a fallback.

### Programmatic enqueue

Outside the walker pipeline:

```python
from jvagent.memory.task_proactive import ProactiveTaskSpec

spec = ProactiveTaskSpec(
    directive="Send a check-in about the open ticket",
    not_before="2026-06-08T14:00:00+00:00",
    trigger_on="schedule",
)
handle = await agent.enqueue_proactive_task(
    user_id=user_id,
    spec=spec,
    channel="whatsapp",
)
# handle.id, handle.status == "pending"
```

Embed hosts use `jvagent.embed.enqueue_proactive_task(agent_id=..., user_id=..., spec=...)`.

During an Orchestrator turn, the model can call the **`queue_task`** tool when `proactive_tasks_enabled: true` (default) on `jvagent/orchestrator`.

## Integration notes

- **Orchestrator** and **DirectiveBuilder** start and complete/cancel flow tasks through `visitor.tasks`.
- **TaskCreationInteractAction** enqueues proactive tasks via `enqueue_proactive()`.
- **TaskMonitor** ticks on a schedule (or `GET /api/proactive/tick/{agent_id}`), claims one eligible task per conversation, and dispatches through the full Orchestrator pipeline.
- **TaskTriggerInteractAction** claims event-eligible tasks on user turns; completion is deferred to the Orchestrator finalizer.
- **queue_task** orchestrator tool and **`Agent.enqueue_proactive_task()`** / **`embed.enqueue_proactive_task()`** are programmatic enqueue paths.
- For **canned** (pre-formed) proactive messages, use `Agent.send_proactive_message(...)` — see [proactive-messages.md](proactive-messages.md).

## See also

- [Proactive messages](proactive-messages.md) — canned `send_proactive_message` vs queued agentic tasks
- [ADR-0022](../.planning/adr/0022-proactive-task-monitor.md) — design decision record
- [Conversation](jvagent/memory/conversation.py)
- [TaskStore](jvagent/memory/task_store.py)
- [InteractWalker](jvagent/action/interact/interact_walker.py)
- [Orchestrator](jvagent/action/orchestrator/orchestrator_interact_action.py)
