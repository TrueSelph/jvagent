"""NativeConversation: lightweight conversational handler for fast responses.

Uses the configured ``native_conv_model`` (default ``gpt-4o-mini``) with no tool
access. When the primary LM is Ollama, that OpenAI default is replaced by the LM
action's ``model`` so requests target a valid Ollama model id.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from jvagent.action.agent_interact.agent_interact_action import AgentInteractAction
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


class NativeConversation:
    """Encapsulated handler for native conversational skill execution.

    Uses ``native_conv_model`` (default ``gpt-4o-mini`` when the LM is not
    Ollama; Ollama stacks inherit the primary LM's model id for that default).
    Optimized for minimal latency:

    - Cached model action (resolved on first use, reused across calls)
    - Minimal context window (last 2-3 interactions only)
    - Direct streaming to client
    - No skill discovery, tool executor, or task tracking overhead

    Usage::

        conv = NativeConversation(action)
        await conv.respond(visitor)
    """

    def __init__(self, action: "AgentInteractAction") -> None:
        self._action = action
        self._model_action: Optional[Any] = None

    def _effective_model(self, model_action: Any) -> str:
        """Model id for native conversation.

        Defaults to ``native_conv_model`` (gpt-4o-mini). When the primary LM is
        Ollama, that OpenAI default is invalid on the remote server and commonly
        yields HTTP 404 — fall back to the language model action's configured
        ``model`` unless the user set ``native_conv_model`` to something else.
        """
        configured = getattr(self._action, "native_conv_model", "gpt-4o-mini")
        primary = (getattr(model_action, "model", None) or "").strip()
        provider = getattr(model_action, "provider", "") or ""
        if provider != "ollama":
            return configured
        if configured.strip() != "gpt-4o-mini":
            return configured
        return primary or configured

    @property
    def _temperature(self) -> float:
        return getattr(self._action, "native_conv_temperature", 0.7)

    @property
    def _max_tokens(self) -> int:
        return getattr(self._action, "native_conv_max_tokens", 256)

    @property
    def _persona_prompt(self) -> str:
        return getattr(self._action, "native_conv_persona_prompt", "")

    @property
    def _context_limit(self) -> int:
        return getattr(self._action, "native_conv_context_limit", 2)

    async def _ensure_model(self) -> Any:
        """Resolve and cache the fast conversational model action."""
        if self._model_action is None:
            self._model_action = await self._action.get_model_action(
                required=True, purpose="native"
            )
        return self._model_action

    async def respond(self, visitor: "InteractWalker") -> None:
        """Generate and publish a fast conversational response.

        Args:
            visitor: The InteractWalker carrying interaction state.
        """
        conversation = visitor.conversation
        interaction = visitor.interaction
        if not conversation or not interaction:
            logger.warning("NativeConversation: No conversation or interaction")
            return

        try:
            model = await self._ensure_model()

            history: List[Dict[str, Any]] = []
            if conversation:
                history = await conversation.get_interaction_history(
                    limit=self._context_limit,
                    excluded=interaction.id,
                    with_utterance=True,
                    with_response=True,
                    formatted=True,
                )

            response = await model.generate(
                prompt=interaction.utterance or "",
                system=self._persona_prompt,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                model=self._effective_model(model),
                history=history,
                stream=getattr(visitor, "stream", False),
                calling_action_name=self._action.get_class_name(),
                interaction=interaction,
            )

            if response:
                await self._action.publish(
                    visitor,
                    content=response,
                    streaming_complete=True,
                )

        except Exception as exc:
            logger.error(
                "NativeConversation: Error generating response: %s", exc, exc_info=True
            )
