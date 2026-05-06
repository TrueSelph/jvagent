"""Task harness tools for cockpit (using TaskStore consistently)."""

from typing import Any, List

from jvagent.action.cockpit.context import CockpitContext
from jvagent.tooling.tool import Tool


def _build_task_tools(ctx: CockpitContext) -> List[Tool]:
    """Return harness tools that expose task tracking to the cockpit model."""

    async def _create_plan(title: str, steps: List[str], description: str = "") -> str:
        if not ctx.visitor or not ctx.conversation:
            return "Error: no conversation available for task tracking."
        try:
            task_store = ctx.visitor.tasks
            task = await task_store.create(
                title=title,
                description=description or title,
                owner_action="CockpitEngine",
            )
            await task.start()
            await task.set_plan(steps)
            return (
                f'Plan "{title}" created with {len(steps)} step(s). '
                "Execute steps one at a time; call task_update_step when each completes."
            )
        except Exception as exc:
            return f"Error creating plan: {exc}"

    async def _update_step(step_index: int, status: str, result: str = "") -> str:
        if not ctx.visitor or not ctx.conversation:
            return "Error: no conversation available."
        try:
            task_store = ctx.visitor.tasks
            tasks = task_store.list(status="active")
            if not tasks:
                return "Error: no active task plan. Create one with task_create_plan first."
            task = tasks[0]
            steps = task.list_steps()
            if step_index < 0 or step_index >= len(steps):
                return f"Error: step {step_index} out of range (0-{len(steps) - 1})."

            step = steps[step_index]
            if status == "in_progress":
                await step.start()
            elif status == "done":
                await step.complete(result=result or None)
            elif status == "failed":
                await step.fail(reason=result or None)
            elif status == "skipped":
                await step.skip(reason=result or None)
            else:
                return f"Error: unknown status '{status}'. Use: in_progress, done, failed, or skipped."

            remaining = sum(
                1 for s in steps if s.status not in ("done", "skipped", "failed")
            )
            return f"Step {step_index} set to '{status}'." + (
                f" {remaining} step(s) remaining."
                if remaining > 0
                else " All steps complete!"
            )
        except Exception as exc:
            return f"Error updating step: {exc}"

    async def _get_status() -> str:
        if not ctx.visitor or not ctx.conversation:
            return "Error: no conversation available."
        try:
            task_store = ctx.visitor.tasks
            tasks = task_store.list(status="active")
            if not tasks:
                return "No active task plan. Create one with task_create_plan."
            task = tasks[0]
            steps = task.list_steps()
            if not steps:
                return f'Plan "{task.title}" has no steps defined.'
            lines = [f"Plan: {task.title}"]
            for i, step in enumerate(steps):
                status_icon = {
                    "pending": "○",
                    "in_progress": "●",
                    "done": "✓",
                    "failed": "✗",
                    "skipped": "→",
                }.get(step.status, "?")
                lines.append(
                    f"{status_icon} Step {i}: {step.description}"
                    + (f" [result: {step.result}]" if step.result else "")
                )
            return "\n".join(lines)
        except Exception as exc:
            return f"Error getting status: {exc}"

    async def _add_step(description: str) -> str:
        if not ctx.visitor or not ctx.conversation:
            return "Error: no conversation available."
        try:
            task_store = ctx.visitor.tasks
            tasks = task_store.list(status="active")
            if not tasks:
                return "Error: no active task plan."

            task = tasks[0]
            step = await task.add_step(description)
            return f"Added step {len(task.steps) - 1}: {description}"
        except Exception as exc:
            return f"Error adding step: {exc}"

    return [
        Tool(
            name="task_create_plan",
            description=(
                "Create a structured task plan with steps. "
                "Use when a multi-step task needs tracking."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short title for the plan.",
                    },
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of step descriptions.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Longer plan description (optional, defaults to title).",
                    },
                },
                "required": ["title", "steps"],
            },
            execute=_create_plan,
        ),
        Tool(
            name="task_update_step",
            description=(
                "Update the status of a task plan step. "
                "Mark in_progress when starting, done when complete, "
                "failed or skipped when appropriate."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "step_index": {
                        "type": "integer",
                        "description": "Zero-based index of the step to update.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["in_progress", "done", "failed", "skipped"],
                        "description": "New status for the step.",
                    },
                    "result": {
                        "type": "string",
                        "description": "Brief result summary when marking done (optional).",
                    },
                },
                "required": ["step_index", "status"],
            },
            execute=_update_step,
        ),
        Tool(
            name="task_get_status",
            description=("Get the current status of the active task plan."),
            parameters_schema={"type": "object", "properties": {}},
            execute=_get_status,
        ),
        Tool(
            name="task_add_step",
            description=("Add a new step to the active task plan mid-execution."),
            parameters_schema={
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "Description of the new step.",
                    },
                },
                "required": ["description"],
            },
            execute=_add_step,
        ),
    ]
