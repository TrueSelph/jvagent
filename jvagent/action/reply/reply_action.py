"""ReplyAction — the agent's single egress (ADR-0014).

A lean, Orchestrator-native replacement for ``PersonaAction``'s egress role:

- ``reply(text)`` — the Orchestrator's egress. Slim by default (a thin literal
  publish, no model call); when there is shaping to apply — pending
  **directives**, **parameters**, or a channel that needs **formatting** — it
  composes via ``respond`` instead.
- ``respond(...)`` — render text in the agent's identity (single model call),
  applying directives (as instructions), parameters (as conditional rules), and
  channel formatting when present.
- ``publish(content)`` — the egress primitive (persist + response-bus publish).

Channel formats live in ``CHANNEL_FORMATS`` (overridable per channel via the
``channel_formats`` descriptor attribute); the default channel carries none, so
ordinary web turns stay slim for token efficiency.

Identity (``alias`` + ``role``) is read from the **Agent node** (ADR-0014), not
held here, so the brain (Orchestrator) and the mouth (ReplyAction) share one
source. Shaping stays optional: with no directives/parameters present the reply
is slim — ReplyAction never *collects* them to drive a reply on its own.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.action.parameters import (
    render_parameters,
    reply_core_parameters,
    response_parameters,
    vet_egress,
)

logger = logging.getLogger(__name__)

# Directive / parameter framing (the essential, non-bloated core borrowed from
# PersonaAction). Numbered + "execute ALL" makes the compose model treat every
# queued directive — including the message itself — as a must-do item, so it
# never drops the real reply in favour of a styling directive (e.g. an intro).
DIRECTIVES_SECTION = (
    "MANDATORY — execute ALL {count} of the following directives in this single "
    "reply, in the order listed. Deliver each one IN FULL: if a directive has "
    "several parts (e.g. a statement and a question), convey every part; if it "
    "asks the user for something, your reply includes that ask. Directives are "
    "WHAT to say; your identity and style are only HOW. Do not deny or disclaim "
    "a capability — if one is genuinely impossible, say briefly why. Your reply "
    "is non-compliant if any directive, or any part of one, is missing.\n\n"
    "DIRECTIVES (deliver all, in order):\n{directive_list}"
)
# Recency reinforcement (PersonaAction's COMPLIANCE CHECK): appended at the very
# END of the system prompt when directives are present, so the last thing the
# model reads is the obligation to verify it executed them all and to drop any
# generic sign-off.
DIRECTIVE_COMPLIANCE_CHECK = (
    "COMPLIANCE CHECK (before sending): confirm your reply delivers every numbered "
    "directive in full and in order — including any question or request a directive "
    "makes — and obeys every RESPONSE RULE. End on the substantive content; no "
    "filler closers ('let me know', 'feel free to ask', 'anything else?', 'happy "
    "to help'). If anything is missing, revise before sending."
)
# Peak-attention reinforcement: a terse reminder injected into the compose
# *prompt* (the user-turn slot) so the obligation sits where the model attends
# most, not only in the system preamble.
DIRECTIVE_REMINDER = (
    "[Deliver every MANDATORY directive from the system prompt in full and in "
    "order, in the agent's identity — include any question a directive makes and "
    "end on substance, no sign-off closer.]"
)
# Behavioural rules section. The response-scoped core hardening (no model/AI
# disclosure, no cutoff, no internal-architecture reveal, no closers) is folded
# in as the always-on baseline, merged with any contributed/interaction params;
# unconditional rules apply every reply, "When …" rules apply when they hold.
PARAMETERS_SECTION = (
    "RESPONSE RULES — follow these in every reply (parameters shape HOW; directives "
    "define WHAT). Apply each unconditional rule always, and each 'When …' rule "
    "when its condition holds:\n{parameter_list}"
)

# Prefix that frames the Orchestrator's message as something to RELAY, not react
# to. ``respond`` renders via a model call; passing the bare answer as the prompt
# makes the model treat it as a user utterance to answer (so
# ``respond("Five plus five equals ten.")`` came back as "That's correct. Five
# plus five equals ten."). Enqueuing it as a "Tell the user: ..." directive makes
# the compose model deliver it faithfully in the agent's identity.
RELAY_PREFIX = "Tell the user: "

# A directive may carry model-only composition guidance after this marker: how to
# render the user-facing part ("you may paraphrase"), or producer chaining notes
# ("Do NOT call …", "Then call …"). The compose model sees it (it steers
# rendering); the literal-relay fast path MUST drop it so it never reaches the
# user. An invisible separator char, so it's inert even if a raw directive ever
# slips through unscrubbed.
DIRECTIVE_GUIDANCE_MARKER = "\u2063"  # invisible separator


def user_facing_directive(content: str) -> str:
    """The user-facing portion of a directive: everything before the first
    :data:`DIRECTIVE_GUIDANCE_MARKER`. Model-only guidance after it is dropped so
    it can never be relayed to the user."""
    return str(content or "").split(DIRECTIVE_GUIDANCE_MARKER, 1)[0].strip()


def compose_directive(content: str) -> str:
    """A directive rendered for the compose model: the bare marker token is
    removed (so it never appears literally) but the guidance after it is kept —
    it legitimately steers how the model renders the user-facing reply."""
    return str(content or "").replace(DIRECTIVE_GUIDANCE_MARKER, " ").strip()


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
    """The agent's single egress (ADR-0014): ``reply`` / ``respond`` / ``publish``."""

    model_action_type: str = attribute(default="OpenAILanguageModelAction")
    model: str = attribute(default="gpt-4o-mini")
    model_temperature: float = attribute(default=0.4)
    model_max_tokens: int = attribute(default=1024)
    # ReplyAction's native core: the response-scoped hardening (identity, cutoff,
    # no-internal-reveal, no-closers, grounding), applied in the response prompt.
    # Reuses the common Action.parameters subsystem; the Orchestrator pools these
    # onto the interaction with every other action's response params, and they're
    # rendered here on compose. Operators may extend/override in agent.yaml.
    parameters: List[Dict[str, Any]] = attribute(
        default_factory=reply_core_parameters,
        description=(
            "The reply's native response-scoped behavioural parameters, each "
            "{scope, condition?, response}. Applied in the response prompt; "
            "merged + deduped with the interaction's pooled response params."
        ),
    )
    apply_reply_rules: bool = attribute(
        default=True,
        description=(
            "Apply this action's native response-hardening parameters as the "
            "always-on compose baseline (no AI/model/provider disclosure, no "
            "knowledge cutoff, no internal-architecture reveal, no closers). The "
            "deterministic egress scrub still runs regardless."
        ),
    )
    apply_channel_format: bool = attribute(
        default=True,
        description="Apply channel-specific formatting in respond (default channel stays slim).",
    )
    include_history: bool = attribute(
        default=True,
        description=(
            "Include recent conversation history when composing a reply so the "
            "compose model has turn context (and doesn't improvise a clarifying "
            "question on a directive-only finalize). Set false to compose blind."
        ),
    )
    history_limit: int = attribute(
        default=10,
        description="Max prior interactions of history to include in respond().",
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

    async def _conversation_history(
        self, visitor: Optional[Any], interaction: Any
    ) -> List[Dict[str, str]]:
        """Recent ``{role, content}`` history for the compose model, or ``[]``.

        Excludes the in-flight interaction so the current user turn isn't
        duplicated. Sourced from the visitor's (or interaction's) conversation;
        gated by ``include_history`` and bounded by ``history_limit``.
        """
        if not self.include_history:
            return []
        conversation = getattr(visitor, "conversation", None) if visitor else None
        if conversation is None and interaction is not None:
            conversation = getattr(interaction, "conversation", None)
        getter = getattr(conversation, "get_interaction_history", None)
        if not callable(getter):
            return []
        try:
            return (
                await getter(
                    limit=int(self.history_limit),
                    excluded=getattr(interaction, "id", None),
                    formatted=True,
                )
                or []
            )
        except Exception:
            return []

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
        directive_items: Optional[List[str]] = None,
        parameters_text: str = "",
        format_text: str = "",
    ) -> str:
        parts: List[str] = []
        identity = await self._identity()
        if identity:
            parts.append(identity)
        if format_text:
            parts.append(
                "CHANNEL FORMATTING — format your reply for this "
                "channel:\n" + format_text
            )
        if parameters_text:
            parts.append(PARAMETERS_SECTION.format(parameter_list=parameters_text))
        items = [c for c in (directive_items or []) if c]
        if items:
            # Demarcate each directive with explicit fences so multi-line content
            # (e.g. a note followed by a blank line then a question) stays clearly
            # bound to its own directive and never bleeds into the next.
            blocks = [
                f"--- Directive {i + 1} ---\n{compose_directive(c)}"
                for i, c in enumerate(items)
            ]
            directive_list = "\n".join(blocks) + "\n--- end of directives ---"
            parts.append(
                DIRECTIVES_SECTION.format(
                    count=len(items), directive_list=directive_list
                )
            )
        if extra_system and extra_system.strip():
            parts.append(extra_system.strip())
        # Recency layer: the compliance check is the LAST thing in the prompt so
        # it's freshest at generation time (PersonaAction's pattern). Only when
        # there are directives to be faithful to.
        if items:
            parts.append(DIRECTIVE_COMPLIANCE_CHECK)
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

        Every non-streaming egress — fast literal publish AND composed reply —
        passes through here, so the deterministic ``vet_egress`` scrub runs once,
        at the single choke point, dropping the catastrophic leak classes (self-
        identifying as an AI/model/provider, stating a knowledge cutoff) the
        prompt layer might miss. Streaming replies already left via the bus
        token-by-token, so they rely on the prompt/parameter hardening alone.
        """
        if not content:
            return False
        content = vet_egress(content)
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
            if hasattr(interaction, "mark_emitted"):
                interaction.mark_emitted()
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
    # Egress
    # ------------------------------------------------------------------

    async def reply(self, text: str, visitor: Optional[Any] = None) -> bool:
        """Send the user's reply — the Orchestrator's egress.

        Slim by default: a thin literal publish, no model call. But when the
        interaction carries pending **directives** or **parameters**, those are
        applied via a composed ``respond`` (one model call) so the reply honors
        them. No shaping present → stay slim.
        """
        text = (text or "").strip()
        interaction = getattr(visitor, "interaction", None)
        channel = getattr(visitor, "channel", "default") or "default"
        directive_items = self._directive_items(interaction)
        has_params = bool(self._collect_parameters(None, interaction))
        has_format = self.apply_channel_format and bool(
            self.get_channel_format(channel)
        )

        # Any queued shaping (directives, parameters, or channel format) routes
        # through respond(), which enqueues the message as a directive so the
        # whole queue composes together and the message is never overridden.
        if directive_items or has_params or has_format:
            return bool(await self.respond(interaction, visitor=visitor, text=text))
        if not text:
            return False
        return await self.publish(text, visitor)

    async def gather(self, visitor: Optional[Any] = None) -> bool:
        """Conduit egress — emit the interaction's queued directives as ONE reply.

        Producers (the orchestrator, rails IAs) queue directives; ReplyAction only
        gathers them, never adds. A single relay directive (``Tell the user: …``)
        with no other shaping is slim-published literally (the N=1 fast path, no
        model call); anything else — an instruction directive, multiple
        directives, or queued parameters/channel format — composes into one
        identity-shaped reply.
        """
        interaction = getattr(visitor, "interaction", None)
        directive_items = self._directive_items(interaction)
        if not directive_items:
            return False
        channel = getattr(visitor, "channel", "default") or "default"
        has_params = bool(self._collect_parameters(None, interaction))
        has_format = self.apply_channel_format and bool(
            self.get_channel_format(channel)
        )
        first = directive_items[0]
        content = (
            (first.get("content") if isinstance(first, dict) else str(first)) or ""
        ).strip()
        is_relay = content.lower().startswith("tell the user:")
        if len(directive_items) == 1 and is_relay and not has_params and not has_format:
            # Drop any model-only guidance: the literal relay skips the compose
            # model, so guidance ("you may paraphrase", "Do NOT call …") would
            # otherwise reach the user verbatim.
            literal = user_facing_directive(content[len("tell the user:") :])
            if interaction is not None:
                try:
                    interaction.set_to_executed(directives=[first], parameters=[])
                except Exception:
                    pass
            return await self.publish(literal or content, visitor)
        return bool(await self.respond(interaction, visitor=visitor))

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
        """Render text in the agent's identity (one model call), then publish.

        The prompt is the explicit ``text`` (else the user's utterance for
        context). **Directives** and **parameters** (from the args or the
        interaction) shape the *system* prompt: parameters as conditional rules,
        directives as MANDATORY instructions — the whole directive queue, so a
        multi-directive set is fully addressed and none is dropped. With neither
        present it's just identity + reply rules. Applied directives/parameters
        are marked executed. Falls back to a thin publish if no model action.

        Note: when called with an explicit ``text`` while the interaction already
        has queued directives/parameters, the message is enqueued as a directive
        here (so it lands in ``interaction.directives``) and composed *with* the
        others — never overridden. Both egress tools (reply/respond) funnel here.
        """
        interaction = interaction or getattr(visitor, "interaction", None)
        original_text = (text or "").strip()
        base = original_text
        # The explicit message text is what to RELAY to the user, not a prompt to
        # react to. Frame it as a "Tell the user: ..." directive so the compose
        # model renders it faithfully in the agent's identity instead of treating
        # it as a user utterance to answer (the bug: respond("Five plus five
        # equals ten.") came back "That's correct. Five plus five equals ten.").
        # Enqueue it onto interaction.directives so it composes together with any
        # queued directives/parameters and is never overridden. Both egress tools
        # (reply/respond) funnel here. Skipped when the caller passes an explicit
        # ``directives`` list (then it owns the directive queue).
        relayed_directive: Optional[str] = None
        if base and directives is None:
            relayed_directive = (
                base
                if base.lower().startswith("tell the user")
                else f"{RELAY_PREFIX}{base}"
            )
            # The relayed message is a TRANSIENT compose input — it is composed
            # alongside the queued directives below but is NOT persisted onto
            # interaction.directives. The directive queue holds only genuine
            # upstream IA directives; ReplyAction never stores its own rendered
            # output there (that lives in interaction.response).
            base = ""
        directive_contents = self._directive_contents(directives, interaction)
        # Represent the relayed message exactly once in the in-memory compose set.
        if relayed_directive and relayed_directive not in directive_contents:
            directive_contents.append(relayed_directive)
        parameters_text = self._compose_parameters_text(parameters, interaction)

        # Directives are reply *instructions* and always go through the numbered
        # MANDATORY framing, so a multi-directive queue (e.g. the answer + an
        # intro) is fully addressed and never reduced to one. The prompt carries
        # only the explicit relayed text (if any). ReplyAction is a CONDUIT — it
        # NEVER answers the user's utterance on its own; with nothing passed or
        # queued, it emits nothing.
        content = base
        # Nothing to relay → emit nothing. Parameters shape HOW to phrase, not WHAT
        # to say, so they do not by themselves justify a compose: with no passed
        # text and no queued directive content, ReplyAction stays silent.
        if not content and not directive_contents:
            return ""
        # Peak-attention layer: when there are directives, append a terse
        # reminder to the compose prompt itself so the obligation sits in the
        # user-turn slot the model weights most (PersonaAction's pattern).
        if directive_contents:
            content = (
                f"{content}\n\n{DIRECTIVE_REMINDER}" if content else DIRECTIVE_REMINDER
            )
        channel = getattr(visitor, "channel", "default") or "default"
        format_text = (
            self.get_channel_format(channel) if self.apply_channel_format else ""
        )

        model_action = await self.get_model_action(required=False)
        if model_action is None:
            # No compose model — thin-publish the literal message. `content` is
            # now the user's utterance (the message was framed as a directive),
            # so prefer the original message text.
            literal = original_text or content or " "
            await self.publish(literal, visitor, transient=transient)
            return original_text or content

        # Conversation history for the compose model. Callers may pass it
        # explicitly; otherwise source it from the visitor's conversation. This
        # keeps EVERY reply/respond path consistent — including the locked-flow
        # directive finalize, where the orchestrator hands control back to the
        # responder with only a directive: without history the compose model has
        # no idea it is mid-interview and improvises ("Could you clarify what you
        # need for Monday at 9am?") instead of relaying the queued ask.
        if history is None:
            history = await self._conversation_history(visitor, interaction)

        system = await self._system_prompt(
            extra_system=extra_system,
            directive_items=directive_contents,
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
            # Slim fallback: the identity-shaped compose failed, but the user
            # still needs a reply — deliver the best plain text we have rather
            # than going silent. Prefer the original message (now framed as a
            # relay directive, so `content` may be the user's utterance), then
            # any queued directive text.
            fallback = (
                original_text
                or (content or "").strip()
                or self._collect_directive_text(directives, interaction)
            )
            fallback = fallback or "Sorry — I hit a problem composing that reply."
            try:
                await self.publish(fallback, visitor, transient=transient)
            except Exception:
                pass
            return fallback

        if response and response.strip():
            await self._pipe_response(
                response, interaction, visitor, streaming=streaming, transient=transient
            )
            if interaction is not None:
                try:
                    interaction.record_action_execution(self.get_class_name())
                    # Mark the applied directives/parameters executed so they
                    # aren't re-rendered later this turn (e.g. directive finalize).
                    interaction.set_to_executed(
                        directives=interaction.get_unexecuted_directives(),
                        parameters=interaction.get_unexecuted_parameters(),
                    )
                except Exception:
                    pass
        return response or ""

    @staticmethod
    def _directive_items(interaction: Any) -> List[Any]:
        """The interaction's unexecuted directive items (raw list), or []."""
        if interaction is None:
            return []
        try:
            items = interaction.get_unexecuted_directives()
        except Exception:
            items = None
        return list(items) if isinstance(items, (list, tuple)) else []

    @staticmethod
    def _directive_contents(
        directives: Optional[List[Any]], interaction: Any
    ) -> List[str]:
        """Directive content strings from explicit ``directives`` or the
        interaction's unexecuted ones (one per directive, for numbering)."""
        items = directives
        if items is None and interaction is not None:
            try:
                items = interaction.get_unexecuted_directives()
            except Exception:
                items = None
        if not isinstance(items, (list, tuple)):
            items = []
        out: List[str] = []
        for d in items:
            val = (
                (d.get("content") or "").strip()
                if isinstance(d, dict)
                else str(d).strip()
            )
            if val:
                out.append(val)
        return out

    @classmethod
    def _collect_directive_text(
        cls, directives: Optional[List[Any]], interaction: Any
    ) -> str:
        """Joined directive text (used for has-shaping checks)."""
        return "\n".join(cls._directive_contents(directives, interaction)).strip()

    @staticmethod
    def _collect_parameters(parameters: Optional[List[Any]], interaction: Any) -> str:
        """Render the *contributed* response parameters (explicit or
        ``interaction.parameters``) as a bulleted section — no core baseline.

        This is the slim-vs-compose gate: it must reflect only genuine per-turn
        shaping, so the fast literal-publish path is preserved. ``ambient`` params
        (the always-on core hardening seeded onto the interaction) are excluded —
        their presence alone never forces a compose; the egress scrub enforces
        them on the fast path and ``_compose_parameters_text`` renders them when a
        compose does happen. Loop-scoped params are filtered out too.
        """
        items = parameters
        if items is None and interaction is not None:
            items = getattr(interaction, "parameters", None)
        if not isinstance(items, (list, tuple)):
            items = []
        shaping = [p for p in items if not (isinstance(p, dict) and p.get("ambient"))]
        return render_parameters(response_parameters(shaping))

    def _compose_parameters_text(
        self, parameters: Optional[List[Any]], interaction: Any
    ) -> str:
        """The response-parameter set rendered for a compose: this action's native
        response core (the baseline) merged with explicit + interaction-pooled
        params, filtered to response scope and de-duplicated.

        The Orchestrator pools every action's response params onto the
        interaction; this action's own native core is added too (so a standalone
        reply, with no orchestrator accumulation, is still hardened). Only
        ``response``-scoped rules render here — orchestration params stay in the
        loop prompt.
        """
        merged: List[Any] = []
        if self.apply_reply_rules:
            merged.extend(self.parameters or [])
        if parameters:
            merged.extend(parameters)
        if interaction is not None:
            merged.extend(getattr(interaction, "parameters", None) or [])
        return render_parameters(response_parameters(merged))

    # ------------------------------------------------------------------
    # Tools (the Orchestrator surface)
    # ------------------------------------------------------------------

    async def get_tools(self) -> List[Any]:
        """Furnish ``reply`` / ``respond`` tools (the same contract PersonaAction
        exposes, so ReplyAction drops into the Orchestrator tool surface)."""
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
                    "Reply to the user in the agent's identity. Use when a styled, "
                    "identity-consistent response is wanted."
                ),
                parameters_schema=text_schema,
                execute=self._tool_respond,
            ),
        ]

    @staticmethod
    def _coalesce_text(text: str, kwargs: Dict[str, Any]) -> str:
        """Resolve the reply text from ``text`` or a model-named alias.

        Models routinely name the argument ``message``/``content``/``answer``/
        ``reply``/``response`` instead of ``text``; accept any of them so the
        egress tool doesn't error on a near-miss arg shape.
        """
        if (text or "").strip():
            return text
        for key in ("message", "content", "answer", "reply", "response", "body"):
            val = kwargs.get(key)
            if isinstance(val, str) and val.strip():
                return val
        return text or ""

    async def _tool_reply(
        self, visitor: Any = None, text: str = "", **kwargs: Any
    ) -> Any:
        from jvagent.tooling.tool_result import ToolResult

        ok = await self.reply(self._coalesce_text(text, kwargs), visitor)
        return ToolResult(content="(replied to user)" if ok else "(nothing to reply)")

    async def _tool_respond(
        self, visitor: Any = None, text: str = "", **kwargs: Any
    ) -> Any:
        from jvagent.tooling.tool_result import ToolResult

        interaction = getattr(visitor, "interaction", None)
        out = await self.respond(
            interaction, visitor=visitor, text=self._coalesce_text(text, kwargs)
        )
        return ToolResult(content="(responded to user)" if out else "(no output)")
