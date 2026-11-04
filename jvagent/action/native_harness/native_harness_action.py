"""JV ingress/graph/egress adapter for native Pydantic execution."""

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.harness.native import build_native_agent, run_native_agent
from jvagent.tooling.access import is_tool_allowed


async def persist_cancelled_receipt(interaction: Any) -> None:
    """Attempt cancellation settlement without replacing caller cancellation."""
    task = asyncio.create_task(interaction.save())
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # A second caller cancellation must not interrupt the graph write.
            continue
        except Exception:
            break
    try:
        task.result()
    except (Exception, asyncio.CancelledError) as exc:
        logging.getLogger(__name__).error(
            "native_cancellation_persist_failed",
            extra={
                "run_id": interaction.native_run.get("run_id"),
                "exception_type": type(exc).__name__,
            },
        )


class NativeHarnessAction(InteractAction):
    """Explicit development replacement for the Orchestrator Action.

    Configure this instead of Orchestrator, never alongside it. General flows,
    external effects and shared dollar-budget policy are not yet qualified.
    """

    model: str = attribute(default="openai:gpt-4.1")
    skill_directory: str = attribute(default="")
    instructions: str = attribute(default="Help the user using the available skill.")
    max_run_cost_usd: str = attribute(default="2.00")
    max_conversation_cost_usd: str = attribute(default="20.00")
    max_requests: int = attribute(default=20)
    max_tokens: int = attribute(default=100_000)
    timeout_seconds: float = attribute(default=180.0)

    async def execute(self, visitor: Any) -> None:
        """Run through the library and publish through the configured responder."""
        from jvagent.harness.spend_store import ConversationSpendStore
        from jvagent.harness.step_store import InteractionStepStore

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
        for action in actions:
            if isinstance(action, InteractAction):
                continue
            tools.extend(await action.get_tools() or [])

        async def access(label: str) -> bool:
            return await is_tool_allowed(
                host,
                label=label,
                user_id=visitor.user_id,
                channel=getattr(visitor, "channel", "default"),
            )

        agent = build_native_agent(
            self.model,
            skill_directory=Path(self.skill_directory),
            tools=tools,
            access_check=access,
            instructions=self.instructions,
            step_store=InteractionStepStore(interaction),
            spend_store=ConversationSpendStore(conversation),
            max_run_cost_usd=self.max_run_cost_usd,
            max_conversation_cost_usd=self.max_conversation_cost_usd,
            available_actions=[action.get_class_name() for action in actions],
            channel=getattr(visitor, "channel", "default"),
        )
        previous = await interaction.get_previous_interaction()
        history = []
        if previous is not None and previous.native_run:
            if previous.native_run.get("status") != "delivered":
                raise RuntimeError("Previous native run requires reconciliation")
        if previous is not None and previous.native_run:
            snapshot = await InteractionStepStore(previous).latest_snapshot(
                run_id=previous.native_run["run_id"]
            )
            if snapshot is None:
                raise RuntimeError(
                    "Previous native run has no settled library snapshot"
                )
            history = snapshot.messages

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
                metadata: dict[str, Any] = {"tool_name": part.tool_name}
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

        run_id = uuid.uuid4().hex
        interaction.native_run = {"run_id": run_id, "status": "running"}
        await interaction.save()
        try:
            from jvagent.tooling.context import bind_dispatch_context

            with bind_dispatch_context(visitor):
                result = await run_native_agent(
                    agent,
                    visitor.utterance,
                    conversation_id=visitor.session_id,
                    run_id=run_id,
                    message_history=history,
                    event_stream_handler=events,
                    max_requests=self.max_requests,
                    max_tokens=self.max_tokens,
                    timeout_seconds=self.timeout_seconds,
                )
            from jvagent.harness.accounting import record_native_usage

            record_native_usage(
                interaction, result.new_messages(), run_id=run_id, model=self.model
            )
            usage = result.usage
            interaction.native_run = {
                "run_id": run_id,
                "status": "pending_delivery",
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
            await persist_cancelled_receipt(interaction)
            raise
        except Exception as exc:
            if interaction.native_run.get("status") == "running":
                from jvagent.harness.accounting import recover_native_usage

                await recover_native_usage(interaction, run_id=run_id, model=self.model)
                interaction.native_run = {
                    **interaction.native_run,
                    "status": "failed",
                    "usage_complete": False,
                }
                await interaction.save()
                from pydantic_ai.exceptions import ModelAuthenticationError

                message = (
                    "The model provider rejected its API credentials. Please update the configured key."
                    if isinstance(exc, ModelAuthenticationError)
                    else "The model request could not complete. Please check the service configuration."
                )
                await responder.publish(
                    message, visitor=visitor, message_id=f"o.ResponseMessage.{run_id}"
                )
                return
            raise
