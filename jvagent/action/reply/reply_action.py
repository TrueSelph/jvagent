"""ReplyAction — the agent's egress voice (ADR-0014).

A lean, SkillExecutive-native replacement for ``PersonaAction``'s egress role:

- ``reply(text)`` — the SkillExecutive's egress. Slim by default (a thin literal
  publish, no model call); when there is shaping to apply — pending
  **directives**, **parameters**, or a channel that needs **formatting** — it
  composes via ``respond`` instead.
- ``respond(...)`` — voice text in the agent's identity (single model call),
  applying directives (as instructions), parameters (as conditional rules), and
  channel formatting when present.
- ``publish(content)`` — the egress primitive (persist + response-bus publish).

Channel formats live in ``CHANNEL_FORMATS`` (overridable per channel via the
``channel_formats`` descriptor attribute); the default channel carries none, so
ordinary web turns stay slim for token efficiency.

Identity (``alias`` + ``role``) is read from the **Agent node** (ADR-0014), not
held here, so the brain (SkillExecutive) and the mouth (ReplyAction) share one
source. Shaping stays optional: with no directives/parameters present the reply
is slim — ReplyAction never *collects* them to drive a reply on its own.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action

logger = logging.getLogger(__name__)

# Keeper voice guardrails (the genuinely useful part of PersonaAction's
# framework) baked in statically — not collected per-interaction.
VOICE_RULES = (
    "End on the substantive answer — no invitation closers ('let me know', "
    "'feel free to ask', 'anything else?'). Speak in the user's language with a "
    "natural, concise voice. Never claim to be an AI language model or name a "
    "model provider."
)

# Channel formatting (the "format" axis), keyed by normalized channel name. The
# default channel (web) is deliberately absent → no directive → slim prompt;
# only channels that genuinely need different markup carry one. Operators extend
# or override per channel via the ``channel_formats`` descriptor attribute.
_PLAIN = "Plain text only — no markdown, headings, or bullet symbols."
CHANNEL_FORMATS: Dict[str, str] = {
    "voice": (
        "Plain speech only — no markdown, lists, or symbols. Keep it brief and "
        "natural; write out anything that wouldn't be spoken aloud."
    ),
    "sms": f"{_PLAIN} Keep it short — a sentence or two.",
    "whatsapp": "WhatsApp markup only: *bold*, _italic_, ~strike~ (sparingly); raw URLs.",
    "facebook": f"{_PLAIN} Use raw URLs (no hyperlinks).",
    "messenger": f"{_PLAIN} Use raw URLs.",
    "instagram": f"{_PLAIN} Use raw URLs.",
    "twitter": f"{_PLAIN} Be concise; raw URLs.",
    "x": f"{_PLAIN} Be concise; raw URLs.",
    "linkedin": "Professional tone; light formatting; raw URLs.",
    "email": "Clear paragraphs; simple structure suitable for HTML rendering.",
}


class ReplyAction(Action):
    """The agent's egress voice (ADR-0014): ``reply`` / ``respond`` / ``publish``."""

    model_action_type: str = attribute(default="OpenAILanguageModelAction")
    model: str = attribute(default="gpt-4o-mini")
    model_temperature: float = attribute(default=0.4)
    model_max_tokens: int = attribute(default=1024)
    apply_voice_rules: bool = attribute(
        default=True,
        description="Bake the keeper guardrails (no-closer, identity) into respond.",
    )
    apply_channel_format: bool = attribute(
        default=True,
        description="Apply channel-specific formatting in respond (default channel stays slim).",
    )
    channel_formats: Dict[str, str] = attribute(
        default_factory=dict,
        description=(
            "Per-channel format overrides keyed by normalized channel name; "
            "merged over the built-in CHANNEL_FORMATS."
        ),
    )

    def get_channel_format(self, channel: str) -> str:
        """The format directive for ``channel`` — descriptor override, else the
        built-in default, else '' (the default channel is slim)."""
        from jvagent.core.channel import normalize_channel

        key = normalize_channel(channel or "default")
        override = (self.channel_formats or {}).get(key)
        return (override or CHANNEL_FORMATS.get(key, "")).strip()

    # ------------------------------------------------------------------
    # Identity (from the Agent node) + system prompt
    # ------------------------------------------------------------------

    async def _identity(self) -> str:
        try:
            agent = await self.get_agent()
        except Exception:
            agent = None
        alias = (getattr(agent, "alias", "") or "").strip()
        role = (getattr(agent, "role", "") or "").strip()
        if alias and role:
            return f"You are {alias}, {role}."
        if alias:
            return f"You are {alias}."
        return role

    async def _system_prompt(
        self,
        *,
        extra_system: Optional[str] = None,
        directives_text: str = "",
        parameters_text: str = "",
        format_text: str = "",
    ) -> str:
        parts: List[str] = []
        identity = await self._identity()
        if identity:
            parts.append(identity)
        if self.apply_voice_rules:
            parts.append(VOICE_RULES)
        if format_text:
            parts.append(
                "CHANNEL FORMATTING — format your reply for this "
                "channel:\n" + format_text
            )
        if parameters_text:
            parts.append(
                "CONDITIONAL RULES — apply each whose condition matches this "
                "turn:\n" + parameters_text
            )
        if directives_text:
            parts.append(
                "MANDATORY — accomplish each of these in your reply:\n"
                + directives_text
            )
        if extra_system and extra_system.strip():
            parts.append(extra_system.strip())
        return "\n\n".join(parts).strip() or "You are a helpful assistant."

    # ------------------------------------------------------------------
    # Egress primitive
    # ------------------------------------------------------------------

    async def _pipe_response(
        self,
        content: str,
        interaction: Any,
        visitor: Optional[Any],
        streaming: bool = False,
        transient: bool = False,
    ) -> bool:
        """Persist and/or publish ``content`` (ported from PersonaAction).

        No bus → set/append ``interaction.response`` and save. Bus + streaming →
        no-op (the model already published). Bus + non-streaming → publish once.
        Returns True when something was emitted/persisted.
        """
        if not content:
            return False
        response_bus = getattr(visitor, "response_bus", None) if visitor else None
        has_bus = bool(
            response_bus and visitor and getattr(visitor, "session_id", None)
        )

        if not has_bus:
            if transient or interaction is None:
                return True
            current = interaction.response or ""
            if current and current.strip() and current != content:
                changed = interaction.set_response(f"{current}\n\n{content}")
            else:
                changed = interaction.set_response(content)
            if changed:
                await interaction.save()
            return True

        if streaming:
            return True  # model already streamed via the bus

        channel = getattr(visitor, "channel", "default") or "default"
        visitor_data = getattr(visitor, "data", None) or {} if visitor else {}
        await response_bus.publish(
            session_id=visitor.session_id,
            content=content,
            channel=channel,
            stream=False,
            metadata=dict(visitor_data),
            interaction_id=getattr(interaction, "id", None),
            interaction=interaction,
            user_id=getattr(interaction, "user_id", None),
            streaming_complete=True,
            transient=transient,
        )
        return True

    async def publish(
        self, content: str, visitor: Optional[Any] = None, *, transient: bool = False
    ) -> bool:
        """Egress primitive — publish literal ``content`` to the user."""
        interaction = getattr(visitor, "interaction", None)
        return await self._pipe_response(
            content, interaction, visitor, streaming=False, transient=transient
        )

    # ------------------------------------------------------------------
    # Voice
    # ------------------------------------------------------------------

    async def reply(self, text: str, visitor: Optional[Any] = None) -> bool:
        """Send the user's reply — the SkillExecutive's egress.

        Slim by default: a thin literal publish, no model call. But when the
        interaction carries pending **directives** or **parameters**, those are
        applied via a composed ``respond`` (one model call) so the reply honors
        them. No shaping present → stay slim.
        """
        text = (text or "").strip()
        interaction = getattr(visitor, "interaction", None)
        channel = getattr(visitor, "channel", "default") or "default"
        has_shaping = (
            bool(self._collect_directive_text(None, interaction))
            or bool(self._collect_parameters(None, interaction))
            or (self.apply_channel_format and bool(self.get_channel_format(channel)))
        )
        if has_shaping:
            return bool(await self.respond(interaction, visitor=visitor, text=text))
        if not text:
            return False
        return await self.publish(text, visitor)

    async def respond(
        self,
        interaction: Any = None,
        visitor: Optional[Any] = None,
        *,
        text: Optional[str] = None,
        history: Optional[List[dict]] = None,
        extra_system: Optional[str] = None,
        directives: Optional[List[Any]] = None,
        parameters: Optional[List[Any]] = None,
        transient: bool = False,
    ) -> str:
        """Voice text in the agent's identity (one model call), then publish.

        The base content (prompt) is the explicit ``text`` → directive text →
        ``interaction.utterance``. **Directives** and **parameters** (from the
        args or the interaction) shape the *system* prompt: parameters as
        conditional rules, directives as mandatory instructions when they are
        additional to base text. When an explicit message arrives *with* queued
        directives, the message is folded in as the lead directive so the two
        compose together (a directive can never override the reply's substance).
        With neither present it's just identity + voice rules. Falls back to a
        thin publish if no model action is available.
        """
        interaction = interaction or getattr(visitor, "interaction", None)
        base = (text or "").strip()
        directive_text = self._collect_directive_text(directives, interaction)
        parameters_text = self._collect_parameters(parameters, interaction)

        # Queued directives + an explicit message: fold the message in as the
        # lead directive so it is composed WITH the directives, never overridden
        # by the MANDATORY directive block (e.g. a first-contact intro directive
        # must not replace a task-completion reply). The message and the pending
        # directives then voice together.
        if base and directive_text:
            directive_text = f"Tell the user: {base}\n{directive_text}"
            base = ""

        # Base content (the prompt): explicit text, else the directives, else the
        # user's utterance. Directives go to the *system* (as instructions) only
        # when they're additional to base text — when they ARE the content, they
        # are already the prompt.
        content = base or directive_text
        if not content and interaction is not None:
            content = (getattr(interaction, "utterance", "") or "").strip()
        if not content and not parameters_text:
            return ""
        directives_for_system = directive_text if base else ""
        channel = getattr(visitor, "channel", "default") or "default"
        format_text = (
            self.get_channel_format(channel) if self.apply_channel_format else ""
        )

        model_action = await self.get_model_action(required=False)
        if model_action is None:
            await self.publish(content or " ", visitor, transient=transient)
            return content

        system = await self._system_prompt(
            extra_system=extra_system,
            directives_text=directives_for_system,
            parameters_text=parameters_text,
            format_text=format_text,
        )
        streaming = bool(
            visitor
            and getattr(visitor, "stream", False)
            and getattr(visitor, "response_bus", None)
            and getattr(visitor, "session_id", None)
        )
        response_bus = getattr(visitor, "response_bus", None) if visitor else None
        try:
            response = await model_action.generate(
                prompt=content or " ",
                stream=streaming,
                system=system,
                history=list(history or []),
                calling_action_name=self.get_class_name(),
                model=self.model or None,
                temperature=self.model_temperature,
                max_tokens=self.model_max_tokens,
                response_bus=response_bus if streaming else None,
                interaction=interaction,
                transient=transient,
            )
        except Exception as exc:
            logger.warning("ReplyAction.respond: generate failed: %s", exc)
            return ""

        if response and response.strip():
            await self._pipe_response(
                response, interaction, visitor, streaming=streaming, transient=transient
            )
            if interaction is not None:
                try:
                    interaction.record_action_execution(self.get_class_name())
                except Exception:
                    pass
        return response or ""

    @staticmethod
    def _collect_directive_text(
        directives: Optional[List[Any]], interaction: Any
    ) -> str:
        """Pull text from explicit ``directives`` or the interaction's unexecuted ones."""
        items = directives
        if items is None and interaction is not None:
            try:
                items = interaction.get_unexecuted_directives()
            except Exception:
                items = None
        if not isinstance(items, (list, tuple)):
            items = []
        lines: List[str] = []
        for d in items:
            if isinstance(d, dict):
                val = (d.get("content") or "").strip()
            else:
                val = str(d).strip()
            if val:
                lines.append(val)
        return "\n".join(lines).strip()

    @staticmethod
    def _collect_parameters(parameters: Optional[List[Any]], interaction: Any) -> str:
        """Render conditional parameters (``{condition, response}``) as a bulleted
        section, from explicit ``parameters`` or ``interaction.parameters``."""
        items = parameters
        if items is None and interaction is not None:
            items = getattr(interaction, "parameters", None)
        if not isinstance(items, (list, tuple)):
            items = []
        lines: List[str] = []
        for p in items:
            if isinstance(p, dict):
                cond = (p.get("condition") or "").strip()
                resp = (p.get("response") or "").strip()
                if cond and resp:
                    lines.append(f"- When {cond}: {resp}")
                elif resp:
                    lines.append(f"- {resp}")
            else:
                val = str(p).strip()
                if val:
                    lines.append(f"- {val}")
        return "\n".join(lines).strip()

    # ------------------------------------------------------------------
    # Tools (the SkillExecutive surface)
    # ------------------------------------------------------------------

    async def get_tools(self) -> List[Any]:
        """Furnish ``reply`` / ``respond`` tools (the same contract PersonaAction
        exposes, so ReplyAction drops into the SkillExecutive tool surface)."""
        from jvagent.tooling.tool import Tool

        text_schema = {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "The text."}},
            "required": ["text"],
        }
        return [
            Tool(
                name="reply",
                description=(
                    "Send your reply to the user. This is the way to deliver your "
                    "message — pass your final text; any pending directives or "
                    "parameters are applied automatically."
                ),
                parameters_schema=text_schema,
                execute=self._tool_reply,
            ),
            Tool(
                name="respond",
                description=(
                    "Reply to the user in the agent's voice. Use when a styled, "
                    "identity-consistent response is wanted."
                ),
                parameters_schema=text_schema,
                execute=self._tool_respond,
            ),
        ]

    async def _tool_reply(self, visitor: Any = None, text: str = "") -> Any:
        from jvagent.tooling.tool_result import ToolResult

        ok = await self.reply(text, visitor)
        return ToolResult(content="(replied to user)" if ok else "(nothing to reply)")

    async def _tool_respond(self, visitor: Any = None, text: str = "") -> Any:
        from jvagent.tooling.tool_result import ToolResult

        interaction = getattr(visitor, "interaction", None)
        out = await self.respond(interaction, visitor=visitor, text=text)
        return ToolResult(content="(responded to user)" if out else "(no output)")
