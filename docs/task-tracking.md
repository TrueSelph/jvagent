# Task Tracking

Task lifecycle management is now handled by `TaskStore`, scoped per conversation and exposed through `visitor.tasks`.

## Overview

- **Storage**: `Conversation.tasks` is the canonical store (list of task dicts, persisted on the conversation node).
- **Writer**: `TaskStore` is the single write path for create/update/complete/fail/cancel.
- **Scope**: One store instance per conversation (`TaskStore(conversation)`), lazily available as `InteractWalker.tasks`.
- **Consumers**: Thinking loop, interview flows, proactive task creation/dispatch/trigger, router/persona context, and response payloads.

## Task Model

Each entry in `tasks` uses this normalized shape:

```json
{
  "id": "ReasoningHelm:907b4a8af2e5",
  "type": "AGENTIC_LOOP",
  "description": "Agentic task: ...",
  "owner_action": "ReasoningHelm",
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

For scheduler-style flows:

```python
task = visitor.tasks.create(
    description="Follow up tomorrow",
    task_type="PROACTIVE",
    data={"trigger_at": "2026-04-19T09:00", "trigger_condition": "none"},
)
handle = visitor.tasks.get(task["id"])
handle.start()
# ... later ...
handle.complete()
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

## Integration notes

- **ReasoningHelm** uses `visitor.tasks.track(...)` and records structured steps via its engine.
- **DirectiveBuilder** starts and completes/cancels interview tasks through `visitor.tasks`.
- **TaskCreationInteractAction** creates proactive tasks via `visitor.tasks.create(...)`.
- **TaskDispatcher** starts tasks before dispatch and completes/fails through store APIs. It generates the dispatched message via `PersonaAction.respond(...)` (LLM-generated). For **canned** (pre-formed) proactive messages from any task or scheduler, use `Agent.send_proactive_message(...)` directly instead — see [proactive-messages.md](proactive-messages.md).
- **TaskTriggerInteractAction** marks matched proactive tasks complete through the store.

## See also

- [Proactive messages](proactive-messages.md)
- [Conversation](jvagent/memory/conversation.py)
- [TaskStore](jvagent/memory/task_store.py)
- [InteractWalker](jvagent/action/interact/interact_walker.py)
- [ReasoningHelm](jvagent/action/helm/reasoning/reasoning_helm.py)
