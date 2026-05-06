"""Task harness tools for cockpit.

The cockpit engine auto-creates one **trace task** per run (see
``CockpitEngine._auto_task_start``) and stores its ID on
``visitor._skill_state["cockpit_trace_task_id"]``. The model's task tools
below resolve to that shared trace task whenever it exists, so model-authored
plans live on the same task the engine is auto-recording. This keeps a single
task per run with both engine observability and model intent in one place.

Once the model calls ``task_create_plan`` the engine flips
``cockpit_model_planned=True`` and stops auto-appending iteration steps —
the model's plan drives from then on.
"""

from typing import Any, List, Optional

from jvagent.action.cockpit.context import CockpitContext
from jvagent.tooling.tool import Tool


def _resolve_trace_task(ctx: CockpitContext) -> Optional[Any]:
    """Return the engine's auto-created trace task, if any."""
    sk = getattr(ctx.visitor, "_skill_state", None) or {}
    task_id = sk.get("cockpit_trace_task_id")
    if not task_id:
        return None
    try:
        store = ctx.visitor.tasks
        for task in store.list():
            if getattr(task, "id", None) == task_id:
                return task
    except Exception:
        pass
    return None


def _build_task_tools(ctx: CockpitContext) -> List[Tool]:
    """Return harness tools that expose task tracking to the cockpit model."""

    async def _create_plan(title: str, steps: List[str], description: str = "") -> str:
        if not ctx.visitor or not ctx.conversation:
            return "Error: no conversation available for task tracking."
        try:
            task_store = ctx.visitor.tasks
            # Prefer re-purposing the engine's auto-created trace task so
            # there's one shared task per cockpit run.
            task = _resolve_trace_task(ctx)
            if task is not None:
                # Preserve any engine-trace steps that were recorded before
                # the model planned, so post-hoc observability isn't lost
                # when set_plan replaces the step list.
                try:
                    pre_plan_steps = []
                    for s in task.list_steps():
                        sd = (
                            s.to_dict()
                            if hasattr(s, "to_dict")
                            else {
                                "id": getattr(s, "id", None),
                                "description": getattr(s, "description", ""),
                                "status": getattr(s, "status", ""),
                                "data": getattr(s, "data", {}) or {},
                            }
                        )
                        pre_plan_steps.append(sd)
                    if pre_plan_steps:
                        await task.update(
                            engine_pre_plan_trace=pre_plan_steps,
                            title=title,
                            description=description or title,
                        )
                    else:
                        await task.update(
                            title=title,
                            description=description or title,
                        )
                except Exception:
                    pass
                await task.set_plan(steps)
            else:
                task = await task_store.create(
                    title=title,
                    description=description or title,
                    owner_action="CockpitEngine",
                )
                await task.start()
                await task.set_plan(steps)
            # Flip the "model has planned" flag so the engine stops appending
            # iteration steps and instead attaches tool-call detail as
            # sub-events on the in-progress model step.
            sk = getattr(ctx.visitor, "_skill_state", None)
            if isinstance(sk, dict):
                sk["cockpit_model_planned"] = True
            return (
                f'Plan "{title}" created with {len(steps)} step(s). '
                "Execute steps one at a time; call task_update_step when each completes. "
                "Tool calls you make will be attached to the active step's _events for observability."
            )
        except Exception as exc:
            return f"Error creating plan: {exc}"

    async def _update_step(step_index: int, status: str, result: str = "") -> str:
        if not ctx.visitor or not ctx.conversation:
            return "Error: no conversation available."
        try:
            task_store = ctx.visitor.tasks
            # Prefer the shared trace task; fall back to first active.
            task = _resolve_trace_task(ctx)
            if task is None:
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
            task = _resolve_trace_task(ctx)
            if task is None:
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
                line = f"{status_icon} Step {i}: {step.description}"
                if step.result:
                    line += f" [result: {step.result}]"
                # Surface engine-trace tool-call summary if present
                data = step.data or {}
                summary = data.get("summary")
                if isinstance(summary, dict) and summary.get("total"):
                    line += (
                        f" [tools: {summary.get('ok', 0)}/{summary['total']} ok"
                        + (
                            f", {summary['errored']} errored"
                            if summary.get("errored")
                            else ""
                        )
                        + "]"
                    )
                # Surface event count for sub-attached tool calls (plan mode)
                events = data.get("_events") or []
                tool_events = [e for e in events if e.get("type") == "tool_calls"]
                if tool_events:
                    line += f" [tool_call_events: {len(tool_events)}]"
                lines.append(line)
            return "\n".join(lines)
        except Exception as exc:
            return f"Error getting status: {exc}"

    async def _add_step(description: str) -> str:
        if not ctx.visitor or not ctx.conversation:
            return "Error: no conversation available."
        try:
            task_store = ctx.visitor.tasks
            task = _resolve_trace_task(ctx)
            if task is None:
                tasks = task_store.list(status="active")
                if not tasks:
                    return "Error: no active task plan."
                task = tasks[0]
            await task.add_step(description)
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
