# Task Tracking

Task lifecycle management is now centralized in `TaskService`, scoped per conversation and exposed through `visitor.tasks`.

## Overview

- **Storage**: `Conversation.active_tasks` remains the canonical store.
- **Writer**: `TaskService` is the single write path for create/update/complete/fail/cancel/reserve.
- **Scope**: One service instance per conversation (`TaskService(conversation)`), lazily available as `InteractWalker.tasks`.
- **Consumers**: Thinking loop, interview flows, proactive task creation/dispatch/trigger, router/persona context, and response payloads.

## Task Model

Each entry in `active_tasks` uses this normalized shape:

```json
{
  "task_id": "SkillInteractAction:907b4a8af2e5",
  "task_type": "AGENTIC_LOOP",
  "description": "Agentic task: ...",
  "action_name": "SkillInteractAction",
  "status": "active",
  "next_trigger_at": null,
  "trigger_condition": null,
  "metadata": {
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

- Active statuses: `pending`, `active`, `triggered`, `reserved`
- Terminal statuses: `completed`, `failed`, `cancelled`, `timed_out`, `max_iterations`, `superseded`
- Terminal statuses are one-way; completed tasks remain in the list for audit history.

## Preferred API (`visitor.tasks`)

Use `TaskService` directly from actions:

```python
async with visitor.tasks.track(
    description="Agentic task: ...",
    task_type="AGENTIC_LOOP",
    action_name=self.get_class_name(),
    metadata={"skills": self.skills},
) as task:
    await task.record_step("thinking", iteration=1)
    await task.record_step("tool_call", iteration=1, details={"count": 2})
    await task.update_metadata(current_phase="tool_dispatch")
    await task.complete(status="completed", summary="Done")
```

For scheduler-style flows:

```python
task = await visitor.tasks.start(
    description="Follow up tomorrow",
    task_type="PROACTIVE",
    trigger_at="2026-04-19T09:00",
    trigger_condition="none",
    metadata={"context": "follow-up", "channel": "sms"},
)

await visitor.tasks.complete(task_id=task.task_id, status="completed")
```

## Read accessors

`Conversation` exposes read-only helpers for inspecting the task list:

- `conversation.get_active_tasks(status=..., action_name=...)`
- `conversation.get_active_task(task_id=..., task_type=..., description=..., action_name=..., status=...)`
- `conversation.get_active_tasks_for_context()`

All writes go through `TaskService` (`visitor.tasks` or `TaskService(conversation)`).
Use `singleton_action=True` when an action should keep at most one active task — the
prior active entry is automatically transitioned to `superseded` and a new entry is
created so lineage is preserved.

## Lifecycle callbacks

`TaskService` emits callbacks through `jvagent/core/callback.py`:

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

- **SkillInteractAction** uses `visitor.tasks.track(...)` and records structured steps.
- **DirectiveBuilder** starts and completes/cancels interview tasks through `visitor.tasks`.
- **TaskCreationInteractAction** creates proactive tasks via `visitor.tasks.start(...)`.
- **TaskDispatcher** reserves tasks (`reserved`) before dispatch and completes/fails through service APIs.
- **TaskTriggerInteractAction** marks matched proactive tasks complete through the service.

## See also

- [Conversation](jvagent/memory/conversation.py)
- [TaskService](jvagent/memory/services/task_service.py)
- [InteractWalker](jvagent/action/interact/interact_walker.py)
- [SkillInteractAction](jvagent/action/skill/skill_interact_action.py)
