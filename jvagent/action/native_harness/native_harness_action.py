"""JV ingress/graph/egress adapter for native Pydantic execution."""

import asyncio
import logging
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from jvspatial import flush_deferred_entities
from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.harness.native import build_native_agent, run_native_agent
from jvagent.tooling.access import is_tool_allowed

_CANCELLATION_SETTLEMENT_SECONDS = 5.0
_MAX_CANCELLATION_WRITES = 16
_pending_cancellation_writes: set[asyncio.Task[Any]] = set()


async def persist_cancelled_receipt(
    interaction: Any, *, model: str = "unknown"
) -> bool:
    """Bound host cleanup without masking cancellation or claiming a failed write.

    A non-cooperative adapter may finish later. Retain and observe that task,
    but cap outstanding cleanup writes instead of accumulating unbounded work.
    This is not a database deadline or a distributed ownership guarantee.
    """
    logger = logging.getLogger(__name__)
    run_id = interaction.native_run.get("run_id")
    if len(_pending_cancellation_writes) >= _MAX_CANCELLATION_WRITES:
        logger.error("native_cancellation_persist_capacity", extra={"run_id": run_id})
        return False

    async def save() -> None:
        if getattr(interaction, "native_steps", None):
            from jvagent.harness.accounting import recover_native_usage

            await recover_native_usage(interaction, run_id=run_id, model=model)
        await interaction.save()
        await flush_deferred_entities(interaction, strict=True)

    def finished(task: asyncio.Task[Any]) -> None:
        _pending_cancellation_writes.discard(task)
        try:
            task.result()
        except (Exception, asyncio.CancelledError) as exc:
            logger.error(
                "native_cancellation_persist_failed",
                extra={"run_id": run_id, "exception_type": type(exc).__name__},
            )

    task = asyncio.create_task(save(), name="native-cancellation-receipt")
    _pending_cancellation_writes.add(task)
    task.add_done_callback(finished)
    deadline = asyncio.get_running_loop().time() + _CANCELLATION_SETTLEMENT_SECONDS
    while not task.done():
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            task.cancel()
            logger.error(
                "native_cancellation_persist_timeout", extra={"run_id": run_id}
            )
            return False
        try:
            # wait() does not cancel the writer when the caller is cancelled.
            # Unlike wait_for(), it does not await cancellation acknowledgement.
            await asyncio.wait({task}, timeout=remaining)
        except asyncio.CancelledError:
            continue  # another cancel must not reset the original deadline
    try:
        task.result()
        return True
    except (Exception, asyncio.CancelledError):
        return False  # finished() observes/logs the exception exactly once


class NativeHarnessAction(InteractAction):
    """Explicit development replacement for the Orchestrator Action.

    Configure this instead of Orchestrator, never alongside it. General flows,
    external effects and distributed dollar-budget policy are not yet qualified.
    """

    model: str = attribute(default="openai:gpt-4.1")
    model_settings: dict[str, Any] = attribute(default_factory=dict)
    skill_directory: str = attribute(default="")
    delegates: list[dict[str, Any]] = attribute(default_factory=list)
    instructions: str = attribute(default="Help the user using the available skill.")
    max_run_cost_usd: str = attribute(default="2.00")
    max_conversation_cost_usd: str = attribute(default="20.00")
    context_max_tokens: int = attribute(default=32_000)
    context_keep_tokens: int = attribute(default=16_000)
    max_requests: int = attribute(default=20)
    max_tokens: int = attribute(default=100_000)
    timeout_seconds: float = attribute(default=180.0)

    async def on_startup(self) -> None:
        """Reject invalid native settings before the process accepts traffic."""
        from jvagent.core.execution_owner import require_native_local_ownership
        from jvagent.harness.model_settings import validate_model_settings

        require_native_local_ownership()
        validate_model_settings(self.model_settings)

    async def execute(self, visitor: Any) -> None:
        """Run through the library and publish through the configured responder."""
        try:
            await NativeHarnessAction._execute(self, visitor)
        except Exception:
            interaction = getattr(visitor, "interaction", None)
            if (
                interaction is None
                or interaction.native_run
                or interaction.has_emitted()
            ):
                raise
            responder = await self.get_responder()
            if responder is None:
                raise
            logging.getLogger(__name__).exception("native_admission_failed")
            interaction.native_run = {"status": "blocked", "reason": "admission_failed"}
            await interaction.save()
            if not await responder.publish(
                "Agent configuration prevented this request from starting. Ask an administrator to check the configuration, then start a new conversation.",
                visitor=visitor,
            ):
                raise RuntimeError("Native admission notice delivery is unconfirmed")

    async def _execute(self, visitor: Any) -> None:
        """Admit configuration, run the native agent, and reconcile its delivery."""
        from jvagent.core.execution_owner import require_native_local_ownership
        from jvagent.harness.spend_store import ConversationSpendStore
        from jvagent.harness.step_store import InteractionStepStore

        require_native_local_ownership()
        host = await self.get_agent()
        interaction = getattr(visitor, "interaction", None)
        responder = await self.get_responder()
        conversation = getattr(visitor, "conversation", None)
        if (
            host is None
            or interaction is None
            or responder is None
            or conversation is None
        ):
            raise RuntimeError("Native execution requires admitted graph and responder")
        if not getattr(visitor, "user_id", None) or not getattr(
            visitor, "session_id", None
        ):
            raise RuntimeError(
                "Native execution requires host-established caller identity"
            )
        actions = await host.get_actions(enabled_only=True)
        for action in actions:
            if action.id != self.id and action.get_manifest().pattern_orchestrator:
                raise RuntimeError("Configure only one harness execution Action")
        if interaction.has_emitted():
            return
        if interaction.native_run:
            raise RuntimeError("Existing native invocation requires reconciliation")
        tools: list[Any] = []
        toolsets: list[Any] = []
        action_skill_bases: dict[str, Path] = {}
        for action in actions:
            if isinstance(action, InteractAction):
                continue
            base_provider = getattr(action, "get_native_skill_bases", None)
            if base_provider is not None:
                bases = await base_provider()
                if action_skill_bases.keys() & bases.keys():
                    raise ValueError("Duplicate native Action skill bases")
                action_skill_bases.update(bases)
            native_toolsets = getattr(action, "get_native_toolsets", None)
            if native_toolsets is not None:
                toolsets.extend(await native_toolsets())
                continue
            tool_provider = (
                getattr(action, "get_native_tools", None) or action.get_tools
            )
            for tool in await tool_provider() or []:
                if getattr(action, "binds_tools_to_visitor", False):
                    tool = replace(tool, binds_visitor=True)
                tools.append(tool)

        # Admission identity stays fixed even if a legacy integration mutates
        # its live visitor while an asynchronous tool is running.
        caller_user_id = visitor.user_id
        caller_channel = getattr(visitor, "channel", "default")

        async def access(label: str) -> bool:
            return await is_tool_allowed(
                host,
                label=label,
                user_id=caller_user_id,
                channel=caller_channel,
            )

        async def tool_access(tool: Any) -> bool:
            return await is_tool_allowed(
                host,
                label=tool.access_label or tool.name,
                tool_name=tool.name,
                requires_tool_permission=tool.requires_tool_permission,
                user_id=caller_user_id,
                channel=caller_channel,
            )

        async def events(ctx: Any, stream: Any) -> None:
            from pydantic_ai.messages import (
                FunctionToolCallEvent,
                FunctionToolResultEvent,
            )

            async for event in stream:
                if not isinstance(
                    event, (FunctionToolCallEvent, FunctionToolResultEvent)
                ):
                    continue
                part = event.part
                phase = (
                    "tool_call"
                    if isinstance(event, FunctionToolCallEvent)
                    else "tool_result"
                )
                metadata: dict[str, Any] = {
                    "tool_name": part.tool_name,
                    "native_run_id": ctx.run_id,
                    "native_agent_name": (
                        ctx.agent.name if ctx.agent is not None else None
                    ),
                }
                if phase == "tool_call":
                    metadata["tool_args"] = part.args_as_dict()
                    content = part.tool_name
                else:
                    content = str(part.content)[:8000]
                    metadata["tool_result"] = content
                bus = getattr(visitor, "response_bus", None)
                if bus is not None:
                    await bus.publish(
                        session_id=visitor.session_id,
                        content=content,
                        channel=getattr(visitor, "channel", "default"),
                        category="thought",
                        thought_type=phase,
                        segment_id=part.tool_call_id,
                        transient=True,
                        interaction=interaction,
                        interaction_id=interaction.id,
                        user_id=visitor.user_id,
                        metadata=metadata,
                    )

        from jvagent.core.task_dispatch import (
            native_task_spec,
            task_prompt,
            task_skill_directory,
        )
        from jvagent.harness.accounting import NativeSpendRecorder

        task_spec = native_task_spec(visitor)
        skill_directory = Path(self.skill_directory)
        prompt = getattr(visitor, "utterance", "")
        if task_spec is not None:
            from jvagent.harness.skill_assets import project_skill

            skill_directory = task_skill_directory(skill_directory, task_spec)
            metadata, _ = project_skill(
                skill_directory, action_bases=action_skill_bases
            )
            if (
                not isinstance(task_spec.pinned_tools, list)
                or any(not isinstance(name, str) for name in task_spec.pinned_tools)
                or set(task_spec.pinned_tools) - set(metadata.get("allowed-tools", []))
            ):
                raise ValueError(
                    "Proactive pinned tools cannot expand the skill's tool policy"
                )
            prompt = task_prompt(task_spec)

        step_store = InteractionStepStore(interaction)
        agent = build_native_agent(
            self.model,
            model_settings=self.model_settings,
            skill_directory=skill_directory,
            action_skill_bases=action_skill_bases,
            delegates=self.delegates,
            conversation_id=visitor.session_id,
            event_stream_handler=events,
            tools=tools,
            toolsets=toolsets,
            access_check=access,
            tool_access_check=tool_access,
            instructions="\n\n".join(
                part
                for part in (
                    self.instructions,
                    getattr(visitor, "_host_system_context", None),
                )
                if part
            ),
            step_store=step_store,
            spend_observer=NativeSpendRecorder(interaction, lock=step_store.lock),
            context_max_tokens=self.context_max_tokens,
            context_keep_tokens=self.context_keep_tokens,
            spend_store=ConversationSpendStore(conversation),
            max_run_cost_usd=self.max_run_cost_usd,
            max_conversation_cost_usd=self.max_conversation_cost_usd,
            available_actions=sorted(
                {
                    name
                    for action in actions
                    for name in (
                        action.get_class_name(),
                        *(base.__name__ for base in type(action).__mro__),
                    )
                }
            ),
            channel=getattr(visitor, "channel", "default"),
        )
        from jvagent.harness.recovery import (
            RECOVERY_NOTICE,
            NativeHistoryError,
            history_for_new_turn,
        )

        previous = await interaction.get_previous_interaction()
        try:
            history, history_source = await history_for_new_turn(
                previous,
                conversation_id=conversation.id,
                user_id=visitor.user_id,
                session_id=visitor.session_id,
            )
        except NativeHistoryError:
            interaction.native_run = {
                "status": "blocked",
                "reason": "predecessor_requires_reconciliation",
                "predecessor_interaction_id": previous.id,
            }
            await interaction.save()
            await responder.publish(
                RECOVERY_NOTICE,
                visitor=visitor,
            )
            return
        run_id = getattr(visitor, "correlation_id", "") or uuid.uuid4().hex
        interaction.native_run = {
            "run_id": run_id,
            "status": "running",
            "history_source": history_source,
        }
        task_binding = getattr(visitor, "_native_task_dispatch", None)
        if task_spec is not None:
            native_task_spec(visitor)
            interaction.add_parameter(
                {"is_proactive": True, "proactive_task_id": task_binding.task_id},
                "TaskMonitor",
            )
        await interaction.save()
        try:
            from jvagent.tooling.context import bind_dispatch_context

            with bind_dispatch_context(visitor):
                result = await run_native_agent(
                    agent,
                    prompt,
                    conversation_id=visitor.session_id,
                    run_id=run_id,
                    message_history=history,
                    event_stream_handler=events,
                    max_cost_usd=self.max_run_cost_usd,
                    max_requests=self.max_requests,
                    max_tokens=self.max_tokens,
                    timeout_seconds=self.timeout_seconds,
                )
            from jvagent.harness.accounting import record_native_usage

            record_native_usage(
                interaction, result.new_messages(), run_id=run_id, model=self.model
            )
            from jvagent.harness.accounting import recover_native_usage

            await recover_native_usage(interaction, run_id=run_id, model=self.model)
            usage = result.usage
            interaction.native_run = {
                "run_id": run_id,
                "status": "pending_delivery",
                "history_source": history_source,
                "usage_complete": True,
                "usage": {
                    "requests": usage.requests,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                },
                "output": result.output,
            }
            await interaction.save()
            if not await responder.publish(
                result.output, visitor=visitor, message_id=f"o.ResponseMessage.{run_id}"
            ):
                raise RuntimeError("Native response delivery is unconfirmed")
            interaction.native_run = {**interaction.native_run, "status": "delivered"}
            await interaction.save()
        except asyncio.CancelledError:
            receipt = interaction.native_run
            delivery_started = receipt.get("status") in {
                "pending_delivery",
                "delivered",
            }
            interaction.native_run = {
                **receipt,
                "status": "delivery_unknown" if delivery_started else "cancelled",
                "usage_complete": bool(receipt.get("usage_complete")),
            }
            if not await persist_cancelled_receipt(interaction, model=self.model):
                interaction.native_run = {
                    **interaction.native_run,
                    "status": "delivery_unknown" if delivery_started else "blocked",
                    "reason": "cancellation_receipt_unconfirmed",
                }
            raise
        except Exception as exc:
            if interaction.native_run.get("status") == "running":
                from jvagent.harness.accounting import recover_native_usage
                from jvagent.harness.contracts import OutputTokenLimitReached

                await recover_native_usage(interaction, run_id=run_id, model=self.model)
                interaction.native_run = {
                    **interaction.native_run,
                    "status": "failed",
                    "usage_complete": False,
                }
                if isinstance(exc, OutputTokenLimitReached):
                    interaction.native_run["reason"] = "output_token_limit"
                await interaction.save()
                from pydantic_ai.exceptions import (
                    ModelAuthenticationError,
                    UsageLimitExceeded,
                )
                from pydantic_ai_harness.spend import SpendLimitExceeded

                if isinstance(exc, ModelAuthenticationError):
                    message = "The model provider rejected its API credentials. Please update the configured key."
                elif isinstance(exc, OutputTokenLimitReached):
                    message = "The model reached its reply token limit before finishing. Increase the per-request output limit in agent settings or ask for a shorter answer."
                elif isinstance(exc, (UsageLimitExceeded, SpendLimitExceeded)):
                    message = "This turn reached its configured model budget. Increase the run limits in the agent settings or start a narrower request."
                else:
                    message = "The model request could not complete. Please check the service configuration."
                await responder.publish(
                    message, visitor=visitor, message_id=f"o.ResponseMessage.{run_id}"
                )
                return
            raise
