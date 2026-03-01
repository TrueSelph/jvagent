# Task Tracking

The task tracker provides a central mechanism for the AI to track current and upcoming tasks that require user intervention. Tasks are stored on the **Conversation** node (not per-interaction), ensuring a single source of truth that persists across turns and is not affected by history pruning.

## Overview

- **Location**: `Conversation.active_tasks` (list of task dicts)
- **Purpose**: Track ongoing activities (e.g., active interviews) that require user input
- **Consumers**: InteractRouter (context signals), PersonaAction (prompt awareness, reminder parameter), interact response payload (development mode)

## Task Model

Each task entry has the following structure:

```json
{
  "task_id": "ReportInterviewInteractAction:35a045f50ab7",
  "description": "Guide user to complete ReportInterviewInteractAction",
  "action_name": "ReportInterviewInteractAction",
  "status": "active",
  "metadata": {
    "interview_type": "ReportInterviewInteractAction",
    "state": "active"
  },
  "created_at": "2026-02-28T13:00:23.397095+00:00",
  "updated_at": "2026-02-28T13:00:23.397095+00:00"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | str | Unique identifier. Auto-generated as `{action_name}:{uuid}` when `action_name` is provided, else `task_{uuid}`. Preserved on upsert. |
| `description` | str | Human/AI-readable task description (e.g., "Guide user to complete SignupInterviewInteractAction") |
| `action_name` | str | Optional. Action class name for actions that manage their own tasks. Used for cleanup and filtering. |
| `status` | str | `"active"` \| `"inactive"` \| `"upcoming"` \| `"completed"` \| `"cancelled"` |
| `metadata` | dict | Optional. Interview type, state, current question, etc. |
| `created_at` | str | ISO datetime |
| `updated_at` | str | ISO datetime |

## Conversation API

### add_active_task

```python
await conversation.add_active_task(
    description="Guide user to complete SignupInterviewInteractAction",
    metadata={"interview_type": "SignupInterview", "state": "ACTIVE"},
    task_id=None,  # Optional; auto-generated when not provided
    action_name="SignupInterviewInteractAction",
)
```

- **Upsert behavior**: Matches by `task_id`, `description`, or `action_name`. Updates existing task if found.
- **task_id**: When not provided, generates `{action_name}:{12-char-uuid}` or `task_{32-char-uuid}`.

### update_task

```python
updated = await conversation.update_task(
    status="completed",  # or "cancelled"
    task_id="SignupInterviewInteractAction:abc123",  # optional, for exact match
    description="Guide user to complete SignupInterviewInteractAction",  # optional
    action_name="SignupInterviewInteractAction",  # optional
)
```

- Tasks are **never removed**; they are updated in status to preserve an audit log.
- Provide at least one of `task_id`, `description`, or `action_name`. When multiple tasks exist per action, use `task_id` or `description` to distinguish.
- Valid `status` values: `"completed"`, `"cancelled"` (and `"active"`, `"inactive"`, `"upcoming"` for other use cases).

### remove_active_task

```python
updated = await conversation.remove_active_task(
    task_id="SignupInterviewInteractAction:abc123",  # optional
    description="Guide user to complete SignupInterviewInteractAction",  # optional
    action_name="SignupInterviewInteractAction",  # optional
)
```

- Delegates to `update_task` with `status="completed"`. Kept for backward compatibility.
- Task remains in the list with updated status; it is not removed.

### get_active_tasks

```python
tasks = conversation.get_active_tasks(
    status="active",  # Optional filter
    action_name="SignupInterviewInteractAction",  # Optional filter
)
```

### get_active_tasks_for_context

```python
descriptions = conversation.get_active_tasks_for_context()
# Returns ["Guide user to complete SignupInterviewInteractAction", ...]
```

Returns list of descriptions for active tasks (status=active). Used by InteractRouter for context signals.

### Optional lookup (get_active_task_by_description, get_active_task_by_action)

```python
task = conversation.get_active_task_by_description("Guide user to complete SignupInterviewInteractAction")
task = conversation.get_active_task_by_action("SignupInterviewInteractAction")
```

Returns the matching task dict if found, `None` otherwise. Useful for lookup before update or conditional logic.

## InteractWalker Helpers

When executing within an InteractAction, use the walker's convenience methods (they delegate to the conversation):

```python
await visitor.add_active_task(
    description="Guide user to complete MyInterviewInteractAction",
    metadata={"state": "ACTIVE"},
    action_name="MyInterviewInteractAction",
)

updated = await visitor.update_task(
    status="completed",
    description="Guide user to complete MyInterviewInteractAction",
    action_name="MyInterviewInteractAction",
)
# Or for cancellation:
updated = await visitor.update_task(
    status="cancelled",
    description="Guide user to complete MyInterviewInteractAction",
    action_name="MyInterviewInteractAction",
)

tasks = await visitor.get_active_tasks(status="active", action_name="MyInterviewInteractAction")
```

Requires `visitor.conversation` to be set (the walker sets this during initialization).

## Interview Integration

The **DirectiveBuilder** (used by InterviewInteractAction) automatically:

- **Registers** an active task when the session is ACTIVE or REVIEW (in `queue_directive`)
- **Updates** the task to `"completed"` or `"cancelled"` when the interview completes or is cancelled (in `generate_completed_directive` and `generate_cancelled_directive`). Tasks are never removed; they remain for audit.

**Cancellation gate**: Interview cancellation is only permitted when the interview is listed in active tasks. If the user says "cancel" but no active task exists for that interview, the cancellation is ignored (treated as NONE).

Description format: `"Guide user to complete {action_name}"` (from `ACTIVE_TASK_DESCRIPTION_TEMPLATE`).

## Persona Integration

PersonaAction uses active tasks for:

1. **Prompt context**: An "ACTIVE TASKS" section lists pending tasks when `remind_on_active_tasks=True`
2. **Reminder parameter**: When the user strays from completing tasks, the persona is instructed to briefly remind them to return
3. **Filtering**: Tasks with `metadata.requires_user_intervention=False` are excluded (default is True)

## Response Payload (Development Mode)

In development mode, the interact endpoint response includes `interaction.active_tasks`:

```json
{
  "interaction": {
    "id": "int_123",
    "utterance": "Hello",
    "response": "...",
    "directives": [],
    "parameters": [],
    "events": [],
    "active_tasks": [
      {
        "task_id": "ReportInterviewInteractAction:35a045f50ab7",
        "description": "Guide user to complete ReportInterviewInteractAction",
        "action_name": "ReportInterviewInteractAction",
        "status": "active",
        "metadata": {...},
        "created_at": "...",
        "updated_at": "..."
      }
    ],
    "observability_metrics": [],
    "streamed": false
  }
}
```

Active tasks are fetched from the conversation when building the response; they are not stored on the Interaction model.

## Creating Custom Task-Managing Actions

For multi-turn flows that require user input (beyond interviews):

```python
class MyInteractAction(InteractAction):
    async def execute(self, visitor: InteractWalker) -> None:
        if some_condition_requiring_user_input:
            await visitor.add_active_task(
                description="Complete the X flow",
                action_name=self.get_class_name(),
                metadata={"step": "awaiting_confirmation"},
            )
        # ...
        if flow_complete:
            await visitor.update_task(
                status="completed",
                description="Complete the X flow",
                action_name=self.get_class_name(),
            )
```

Use `action_name` for cleanup so your action can remove its task when done.

## See Also

- [Conversation](jvagent/memory/conversation.py) - Task tracker implementation
- [InteractWalker](jvagent/action/interact/interact_walker.py) - `add_active_task`, `update_task`, `remove_active_task`, `get_active_tasks`
- [DirectiveBuilder](jvagent/action/interview/core/processing/directive_builder.py) - Interview task registration
- [InteractAction API](jvagent/action/interact/README.md) - Interact endpoint and response format
