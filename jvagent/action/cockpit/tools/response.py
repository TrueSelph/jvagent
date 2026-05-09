"""Response harness tools for cockpit (bug fix: deliver_via_persona, merged final_delivery)."""

from typing import Any, List, Optional

from jvagent.action.cockpit.context import CockpitContext
from jvagent.action.cockpit.prompts import CITATION_INSTRUCTION
from jvagent.action.cockpit.session import get_session
from jvagent.tooling.tool import Tool


def _build_response_tools(ctx: CockpitContext) -> List[Tool]:
    """Return harness tools that expose response delivery to the cockpit model."""

    async def _publish(
        content: str,
        streaming: bool = False,
        transient: bool = False,
        finalize: bool = True,
    ) -> str:
        """Publish a message to the user.

        When finalize=True, this signals the model considers this its final answer
        and the cockpit loop will terminate. Use finalize=False for intermediate
        progress updates."""
        if not ctx.response_bus or not ctx.session_id or not ctx.interaction:
            return "Error: response bus or session unavailable."

        await ctx.response_bus.publish(
            session_id=ctx.session_id,
            content=content,
            channel=ctx.channel,
            stream=streaming and ctx.stream,
            interaction_id=ctx.interaction.id,
            interaction=ctx.interaction,
            user_id=ctx.user_id,
            streaming_complete=True,
            transient=transient,
            category="user",
        )

        if finalize:
            get_session(ctx.visitor).finalized = True

        return "Final response delivered." if finalize else "Response published."

    async def _emit_thought(content: str, thought_type: str = "reasoning") -> str:
        if not ctx.response_bus or not ctx.session_id or not ctx.interaction:
            return "Error: response bus or session unavailable."
        # Honor the unified streaming flag — if internal progress is muted,
        # the thought still records on the interaction trace but isn't streamed.
        stream_progress = bool(getattr(ctx.config, "stream_internal_progress", True))
        await ctx.response_bus.publish(
            session_id=ctx.session_id,
            content=content,
            channel=ctx.channel,
            stream=ctx.stream and stream_progress,
            interaction_id=ctx.interaction.id,
            interaction=ctx.interaction,
            user_id=ctx.user_id,
            streaming_complete=True,
            transient=True,
            category="thought",
            thought_type=thought_type,
        )
        return "Thought published."

    async def _deliver_via_persona(content: str) -> str:
        if not ctx.persona or not ctx.visitor or not ctx.action:
            return "Error: PersonaAction or visitor unavailable."

        interaction = ctx.interaction
        if not interaction:
            return "Error: no interaction available."

        try:
            effective_mode = ctx.config.response_mode
            if effective_mode == "respond":
                await ctx.visitor.add_directive(
                    f"Tell the user: {content}{CITATION_INSTRUCTION}"
                )
                await ctx.action.respond(ctx.visitor)
            else:
                await ctx.persona.respond_slim(
                    interaction,
                    ctx.visitor,
                    prompt=content,
                    history=[],
                )
            return "Response delivered via persona."
        except Exception as exc:
            return f"Error delivering via persona: {exc}"

    return [
        Tool(
            name="response_publish",
            description=(
                "Publish a message to the user. Use this when you have content "
                "to share. Set finalize=True (default) to signal this is your "
                "final answer and end the conversation turn. Set finalize=False "
                "for intermediate progress that the user should see while you "
                "continue working."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The message to send to the user.",
                    },
                    "streaming": {
                        "type": "boolean",
                        "description": "Whether to stream the content (default false).",
                        "default": False,
                    },
                    "transient": {
                        "type": "boolean",
                        "description": "If true, skip recording in interaction history (default false).",
                        "default": False,
                    },
                    "finalize": {
                        "type": "boolean",
                        "description": "If true (default), signal this is the final answer and end the turn.",
                        "default": True,
                    },
                },
                "required": ["content"],
            },
            execute=_publish,
        ),
        Tool(
            name="response_emit_thought",
            description=(
                "Emit a reasoning trace or tool progress thought. "
                "Thoughts are not seen by the user but are recorded for audit."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The thought content to emit.",
                    },
                    "thought_type": {
                        "type": "string",
                        "description": "Type of thought: reasoning, tool_progress, plan_update, or correction.",
                        "enum": [
                            "reasoning",
                            "tool_progress",
                            "plan_update",
                            "correction",
                        ],
                        "default": "reasoning",
                    },
                },
                "required": ["content"],
            },
            execute=_emit_thought,
        ),
        Tool(
            name="response_deliver_via_persona",
            description=(
                "Deliver content through the agent's persona for polished, natural prose."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The raw content for the persona to polish into a final response.",
                    },
                },
                "required": ["content"],
            },
            execute=_deliver_via_persona,
        ),
    ]
