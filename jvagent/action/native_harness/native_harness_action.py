"""JV ingress/graph/egress adapter for native Pydantic execution."""

import asyncio
import logging
import traceback
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from jvspatial import flush_deferred_entities
from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.harness.inputs import (
    INPUT_NOTICE,
    NativeInputError,
    native_user_prompt,
    validate_input_media_types,
)
from jvagent.harness.native import build_native_agent, run_native_agent
from jvagent.tooling.access import is_tool_allowed

_CANCELLATION_SETTLEMENT_SECONDS = 5.0
_MAX_CANCELLATION_WRITES = 16
_pending_cancellation_writes: set[asyncio.Task[Any]] = set()


async def publish_native_event(
    visitor: Any,
    interaction: Any,
    ctx: Any,
    event: Any,
    *,
    publish_reasoning: bool,
    proactive: bool = False,
) -> None:
    """Project completed public SDK content without provider signatures."""
    from pydantic_ai.messages import (
        FunctionToolCallEvent,
        FunctionToolResultEvent,
        PartEndEvent,
        ThinkingPart,
    )

    metadata: dict[str, Any] = {
        "is_proactive": proactive,
        "native_run_id": ctx.run_id,
        "native_agent_name": ctx.agent.name if ctx.agent is not None else None,
    }
    if isinstance(event, PartEndEvent) and isinstance(event.part, ThinkingPart):
        if not publish_reasoning or not event.part.content.strip():
            return
        phase = "reasoning"
        content = event.part.content[:8000]
        segment_id = None  # The bus assigns an ID; part indices repeat per request.
        metadata["native_reasoning_source"] = "provider"
        metadata["truncated"] = len(event.part.content) > 8000
    elif isinstance(event, (FunctionToolCallEvent, FunctionToolResultEvent)):
        part = event.part
        metadata["tool_name"] = part.tool_name
        segment_id = part.tool_call_id
        if isinstance(event, FunctionToolCallEvent):
            phase = "tool_call"
            metadata["tool_args"] = part.args_as_dict()
            content = part.tool_name
        else:
            phase = "tool_result"
            content = str(part.content)[:8000]
            metadata["tool_result"] = content
    else:
        return
    bus = getattr(visitor, "response_bus", None)
    if bus is not None:
        await bus.publish(
            session_id=visitor.session_id,
            content=content,
            channel=getattr(visitor, "channel", "default"),
            category="thought",
            thought_type=phase,
            segment_id=segment_id,
            transient=True,
            interaction=interaction,
            interaction_id=interaction.id,
            user_id=visitor.user_id,
            metadata=metadata,
        )


def delivery_metadata(visitor: Any, interaction: Any, model: str) -> dict[str, Any]:
    """Public numeric receipt for both foreground and unsolicited delivery."""
    usage = interaction.compute_usage()
    return {
        "native_replay": False,
        "is_proactive": getattr(visitor, "_native_task_dispatch", None) is not None,
        "observability": {
            "usage": usage,
            **(
                {"model": model[:128]}
                if usage.get("model_attempt_count") or usage.get("model_call_count")
                else {}
            ),
        },
    }


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
    input_media_types: list[str] = attribute(default_factory=list)
    skill_directory: str = attribute(default="")
    skill_directories: list[str] = attribute(default_factory=list)
    delegates: list[dict[str, Any]] = attribute(default_factory=list)
    planning: bool = attribute(default=False)
    publish_reasoning: bool = attribute(default=False)
    instructions: str = attribute(default="Help the user using the available skill.")
    max_run_cost_usd: str = attribute(default="2.00")
    max_conversation_cost_usd: str = attribute(default="20.00")
    context_max_tokens: int = attribute(default=32_000)
    context_keep_tokens: int = attribute(default=16_000)
    max_requests: int = attribute(default=20)
    max_tool_calls: int = attribute(default=40)
    max_tokens: int = attribute(default=100_000)
    max_output_tokens: int | None = attribute(default=None)
    timeout_seconds: float = attribute(default=180.0)
    tool_execution_mode: str = attribute(default="parallel")

    async def on_startup(self) -> None:
        """Reject invalid native settings before the process accepts traffic."""
        from jvagent.core.execution_owner import require_native_local_ownership
        from jvagent.harness.model_settings import (
            validate_model_settings,
            validate_tool_execution_mode,
        )

        require_native_local_ownership()
        validate_model_settings(self.model_settings)
        validate_input_media_types(self.input_media_types)
        validate_tool_execution_mode(self.tool_execution_mode)
        if not isinstance(self.planning, bool):
            raise ValueError("planning must be a boolean")
        if type(self.publish_reasoning) is not bool:
            raise ValueError("publish_reasoning must be a boolean")

    async def execute(self, visitor: Any) -> None:
        """Run through the library and publish through the configured responder."""
        try:
            await NativeHarnessAction._execute(self, visitor)
        except Exception as error:
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
            input_error = isinstance(error, NativeInputError)
            interaction.native_run = {
                "status": "blocked",
                "reason": "invalid_input" if input_error else "admission_failed",
            }
            await interaction.save()
            if not await responder.publish(
                (
                    INPUT_NOTICE
                    if input_error
                    else "Agent configuration prevented this request from starting. Ask an administrator to check the configuration, then start a new conversation."
                ),
                visitor=visitor,
                metadata=delivery_metadata(
                    visitor, interaction, getattr(self, "model", "unknown")
                ),
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
        script_sandbox = None
        for action in actions:
            if isinstance(action, InteractAction):
                continue
            script_provider = getattr(action, "get_native_script_sandbox", None)
            if script_provider is not None:
                declared = await script_provider()
                if declared is not None:
                    if script_sandbox is not None:
                        raise ValueError("Configure only one native script sandbox")
                    script_sandbox = declared
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
                tool_name=label,
                requires_tool_permission=(
                    script_sandbox is not None and label == "code_execution__bash"
                ),
                user_id=caller_user_id,
                channel=caller_channel,
            )

        recovery_read_only = False

        async def tool_access(tool: Any) -> bool:
            from jvagent.harness.mutation_tools import NativeMutationTool

            if recovery_read_only and isinstance(tool, NativeMutationTool):
                return False
            return await is_tool_allowed(
                host,
                label=tool.access_label or tool.name,
                tool_name=tool.name,
                requires_tool_permission=tool.requires_tool_permission,
                user_id=caller_user_id,
                channel=caller_channel,
            )

        publish_reasoning = getattr(self, "publish_reasoning", False)
        if type(publish_reasoning) is not bool:
            raise ValueError("publish_reasoning must be a boolean")

        async def events(ctx: Any, stream: Any) -> None:
            async for event in stream:
                await publish_native_event(
                    visitor,
                    interaction,
                    ctx,
                    event,
                    publish_reasoning=publish_reasoning,
                    proactive=task_spec is not None,
                )

        from jvagent.core.task_dispatch import (
            native_task_spec,
            task_prompt,
            task_skill_directory,
        )
        from jvagent.harness.accounting import NativeSpendRecorder

        task_spec = native_task_spec(visitor)
        from jvagent.harness.skill_assets import resolve_skill_directory

        configured_catalog = getattr(self, "skill_directories", [])
        if self.skill_directory and configured_catalog:
            raise ValueError("Configure one primary skill or an explicit skill catalog")
        skill_directory = (
            resolve_skill_directory(self.skill_directory)
            if self.skill_directory
            else None
        )
        skill_directories = [
            resolve_skill_directory(path) for path in configured_catalog
        ]
        prompt = native_user_prompt(
            getattr(visitor, "utterance", ""),
            getattr(visitor, "data", None),
            allowed_media_types=getattr(self, "input_media_types", []),
        )
        if task_spec is not None:
            from jvagent.harness.skill_assets import project_skill

            if skill_directory is None:
                matches = [
                    path for path in skill_directories if path.name == task_spec.skill
                ]
                if len(matches) != 1:
                    raise ValueError(
                        "Catalog tasks require one explicitly configured skill"
                    )
                skill_directory = matches[0]
                skill_directories = []
            else:
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

        step_store = InteractionStepStore(interaction, agent_id=host.id)
        from jvagent.harness.recovery import (
            RECOVERY_NOTICE,
            NativeHistoryError,
            history_for_new_turn,
        )

        previous = await interaction.get_previous_interaction()
        try:
            prepared_history = await history_for_new_turn(
                previous,
                conversation_id=conversation.id,
                user_id=visitor.user_id,
                session_id=visitor.session_id,
                conversation=conversation,
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
                metadata=delivery_metadata(visitor, interaction, self.model),
            )
            return
        history, history_source = prepared_history
        recovery_read_only = (
            history_source.get("frontier") == "operator_verified_effect"
        )
        spend_store = ConversationSpendStore(conversation)
        if script_sandbox is not None:
            await script_sandbox.bind_policy(
                conversation,
                agent_id=host.id,
                user_id=caller_user_id,
                session_id=visitor.session_id,
                lock=spend_store.lock,
                assets=script_sandbox.assets(
                    [skill_directory]
                    if skill_directory is not None
                    else skill_directories
                ),
            )
        plan_store = None
        planning = getattr(self, "planning", False)
        if not isinstance(planning, bool):
            raise ValueError("planning must be a boolean")
        if planning:
            from jvagent.harness.plan_store import ConversationPlanStore

            plan_store = ConversationPlanStore(
                conversation,
                agent_id=host.id,
                user_id=caller_user_id,
                session_id=visitor.session_id,
                lock=spend_store.lock,
            )
        agent = build_native_agent(
            self.model,
            plan_store=plan_store,
            script_sandbox=script_sandbox,
            allow_mutations=not recovery_read_only,
            model_settings=self.model_settings,
            skill_directory=skill_directory,
            skill_directories=skill_directories,
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
                    (
                        "This is a read-only continuation after an operator verified an interrupted mutation. Acknowledge the verified state without issuing another mutation."
                        if recovery_read_only
                        else None
                    ),
                    getattr(visitor, "_host_system_context", None),
                    "Host attachment input policy: "
                    + (", ".join(getattr(self, "input_media_types", [])) or "none")
                    + ". Files outside this MIME list cannot be accepted.",
                    "Image uploads: "
                    + (
                        "enabled"
                        if any(
                            m.startswith("image/")
                            for m in getattr(self, "input_media_types", [])
                        )
                        else "disabled"
                    )
                    + ". PDF uploads: "
                    + (
                        "enabled"
                        if "application/pdf" in getattr(self, "input_media_types", [])
                        else "disabled"
                    )
                    + ". Do not suggest uploads in a disabled format.",
                )
                if part
            ),
            step_store=step_store,
            spend_observer=NativeSpendRecorder(interaction, lock=step_store.lock),
            context_max_tokens=self.context_max_tokens,
            context_keep_tokens=self.context_keep_tokens,
            spend_store=spend_store,
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
                    deferred_tool_results=getattr(prepared_history, "deferred", None),
                    event_stream_handler=events,
                    max_cost_usd=self.max_run_cost_usd,
                    max_requests=self.max_requests,
                    max_tool_calls=self.max_tool_calls,
                    max_tokens=self.max_tokens,
                    max_output_tokens=self.max_output_tokens,
                    timeout_seconds=self.timeout_seconds,
                    tool_execution_mode=getattr(
                        self, "tool_execution_mode", "parallel"
                    ),
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
            response_models = [
                getattr(message, "model_name", None)
                for message in result.new_messages()
            ]
            response_model = next(
                (
                    name
                    for name in reversed(response_models)
                    if isinstance(name, str) and name
                ),
                self.model,
            )
            # This is an egress receipt, not a second SDK execution journal.
            # Store only routing/observability; governed text remains response.
            interaction.native_run["delivery"] = {
                "version": 1,
                "message_id": f"o.ResponseMessage.{run_id}",
                "channel": getattr(visitor, "channel", "default") or "default",
                "metadata": delivery_metadata(visitor, interaction, response_model),
            }
            await interaction.save()
            await flush_deferred_entities(interaction, strict=True)
            if not await responder.publish(
                result.output,
                visitor=visitor,
                message_id=f"o.ResponseMessage.{run_id}",
                metadata=delivery_metadata(visitor, interaction, response_model),
            ):
                raise RuntimeError("Native response delivery is unconfirmed")
            interaction.native_run = {**interaction.native_run, "status": "delivered"}
            try:
                await interaction.save()
                await flush_deferred_entities(interaction, strict=True)
            except BaseException:
                interaction.native_run = {
                    **interaction.native_run,
                    "status": "delivery_unknown",
                }
                raise
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
                frames = traceback.extract_tb(exc.__traceback__)
                origin = frames[-1] if frames else None
                failure_origin = (
                    f"{Path(origin.filename).name}:{origin.lineno}:{origin.name}"
                    if origin is not None
                    else "unknown"
                )
                logging.getLogger(__name__).warning(
                    "native_execution_failed: %s at %s",
                    type(exc).__name__,
                    failure_origin,
                )
                from jvagent.harness.accounting import recover_native_usage
                from jvagent.harness.contracts import OutputTokenLimitReached
                from jvagent.harness.mutation_tools import MutationAccessDenied

                await recover_native_usage(interaction, run_id=run_id, model=self.model)
                interaction.native_run = {
                    **interaction.native_run,
                    "status": "failed",
                    "usage_complete": False,
                    "failure_type": type(exc).__name__,
                    "failure_origin": failure_origin,
                }
                if isinstance(exc, OutputTokenLimitReached):
                    interaction.native_run["reason"] = "output_token_limit"
                elif isinstance(exc, MutationAccessDenied):
                    interaction.native_run["reason"] = "mutation_access_denied"
                await interaction.save()
                from pydantic_ai.exceptions import (
                    ModelAuthenticationError,
                    UsageLimitExceeded,
                )
                from pydantic_ai_harness.spend import SpendLimitExceeded

                if isinstance(exc, MutationAccessDenied):
                    message = (
                        "Your current permissions do not allow that write operation."
                    )
                elif isinstance(exc, ModelAuthenticationError):
                    message = "The model provider rejected its API credentials. Please update the configured key."
                elif isinstance(exc, OutputTokenLimitReached):
                    message = "The model reached its reply token limit before finishing. Increase the per-request output limit in agent settings or ask for a shorter answer."
                elif isinstance(exc, (UsageLimitExceeded, SpendLimitExceeded)):
                    message = "This turn reached its configured model budget. Increase the run limits in the agent settings or start a narrower request."
                else:
                    message = "The model request could not complete. Please check the service configuration."
                await responder.publish(
                    message,
                    visitor=visitor,
                    message_id=f"o.ResponseMessage.{run_id}",
                    metadata=delivery_metadata(visitor, interaction, self.model),
                )
                return
            raise
