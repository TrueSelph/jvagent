"""Reusable, configurable one-shot LLM call for any Action.

Use this instead of copying private ``_call_model`` helpers on interact
actions. Resolves a :class:`~jvagent.action.model.language.base.LanguageModelAction`
by provider override or ``action.get_model_action()``, optionally loads
conversation history via ``Conversation.get_interaction_history`` (with full
flag control), calls ``generate``, and optionally parses JSON.

**Providers.** OpenAI honors ``response_format={"type": "json_object"}``.
Ollama maps the same kwarg to ``format: "json"``. Fence-wrapped JSON is cleaned
via :func:`~jvagent.action.model.utils.json_utils.strip_json_fences`.

**Returns.**

- ``str`` — text mode success
- ``dict`` — JSON mode parse success
- ``False`` — no model action available (or ``action`` is ``None``)
- ``None`` — exception or JSON parse failure

**Examples.**

Text::

    text = await call_model(self, "Summarize", "Be brief")

JSON::

    data = await call_model(
        self, utterance, system, json_response=True, use_history=True,
        interaction=visitor.interaction, history_limit=5,
    )

Provider + sampling override::

    text = await call_model(
        self, prompt, system,
        provider="ollama", model="llama3.2", temperature=0.2, max_tokens=256,
    )

Prebuilt history (skip fetch)::

    text = await call_model(
        self, prompt, system, history=[{"role": "user", "content": "hi"}],
    )

Injected model action::

    text = await call_model(self, prompt, system, model_action=lm)

Agent apps that still ship a private ``_call_model`` can migrate to this util
when ready; this module does not modify those agents.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Union

from jvagent.action.model.utils.json_utils import strip_json_fences

logger = logging.getLogger(__name__)


async def _resolve_conversation(interaction: Any) -> Any:
    """Best-effort conversation from an interaction (or its visitor).

    Real Interaction nodes expose ``conversation_id`` + ``get_conversation()``,
    not a ``.conversation`` attribute. Fall back to that API when needed.
    """
    if interaction is None:
        return None
    conversation = getattr(interaction, "conversation", None)
    if conversation is not None:
        return conversation
    visitor = getattr(interaction, "visitor", None)
    if visitor is not None:
        conv = getattr(visitor, "conversation", None)
        if conv is not None:
            return conv
    getter = getattr(interaction, "get_conversation", None)
    if callable(getter):
        try:
            return await getter()
        except Exception as exc:
            logger.debug("call_model: get_conversation failed: %s", exc)
    return None


async def _load_history(
    *,
    interaction: Any,
    history_limit: int,
    with_utterance: bool,
    with_response: bool,
    with_interpretation: bool,
    with_event: bool,
    max_statement_length: Optional[int],
) -> Optional[List[Dict[str, Any]]]:
    conversation = await _resolve_conversation(interaction)
    if conversation is None:
        logger.debug("call_model: use_history set but no conversation on interaction")
        return None

    getter = getattr(conversation, "get_interaction_history", None)
    if not callable(getter):
        logger.debug("call_model: conversation has no get_interaction_history")
        return None

    excluded = getattr(interaction, "id", None) or False
    try:
        history = await getter(
            limit=int(history_limit),
            excluded=excluded,
            with_utterance=with_utterance,
            with_response=with_response,
            with_interpretation=with_interpretation,
            with_event=with_event,
            formatted=True,
            max_statement_length=max_statement_length,
        )
    except Exception as exc:
        logger.warning("call_model: failed to load history: %s", exc)
        return None

    if (
        with_interpretation
        and not with_response
        and interaction is not None
        and getattr(interaction, "response", None)
        and history is not None
    ):
        history = list(history)
        history.append(
            {
                "role": "assistant",
                "content": interaction.response,
            }
        )
    return history or []


async def _resolve_model_action(
    action: Any,
    *,
    model_action: Any,
    provider: Optional[str],
) -> Any:
    if model_action is not None:
        return model_action

    if provider:
        from jvagent.action.model.context import model_action_class_for_provider

        class_name = model_action_class_for_provider(provider)
        if not class_name:
            logger.error("call_model: unknown provider %r", provider)
            return None
        resolved = await action.get_action(class_name)
        if resolved is None:
            logger.error(
                "call_model: provider %r → %s not found on agent",
                provider,
                class_name,
            )
        return resolved

    return await action.get_model_action()


def _pick(
    explicit: Any,
    action: Any,
    attr: str,
) -> Any:
    if explicit is not None:
        return explicit
    return getattr(action, attr, None)


async def call_model(
    action: Any,
    user_prompt: str,
    system_prompt: str,
    *,
    # Output
    json_response: bool = False,
    stream: bool = False,
    # History
    use_history: bool = False,
    interaction: Optional[Any] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    history_limit: int = 3,
    with_utterance: bool = True,
    with_response: bool = True,
    with_interpretation: bool = False,
    with_event: bool = True,
    max_statement_length: Optional[int] = 100,
    # Model / provider (per-call overrides; fall back to action attrs)
    model: Optional[str] = None,
    provider: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    model_action: Any = None,
    calling_action_name: Optional[str] = None,
) -> Union[str, Dict[str, Any], bool, None]:
    """Call a language model with configurable history and sampling.

    Args:
        action: Action with ``get_model_action`` / ``get_action`` /
            ``get_class_name``. Optional attrs: ``model``,
            ``model_temperature``, ``model_max_tokens``.
        user_prompt: Primary user-side prompt text.
        system_prompt: System instructions.

        json_response: Parse model output as a JSON object when True.
        stream: Forwarded to ``generate``. Forced ``False`` when
            ``json_response`` is True (JSON needs a full string).

        use_history: Load history from the interaction's conversation when
            True and ``history`` is not provided.
        interaction: Current interaction (needed for history load / exclude).
        history: Prebuilt ``[{role, content}, ...]``. When set, skips fetch.
        history_limit: Max past interactions to include (default 3).
        with_utterance: Include user utterances in loaded history.
        with_response: Include assistant responses in loaded history.
        with_interpretation: Include interpretation snippets in loaded history.
        with_event: Include events in loaded history.
        max_statement_length: Truncate utterance/response lines when loading
            history (``None`` = no truncation).

        model: Model id override (else ``action.model``).
        provider: Provider slug override — ``openai``, ``anthropic``,
            ``ollama``, ``openrouter``, ``groq``. Resolves the matching LM
            action on the agent. Else ``action.get_model_action()``.
        temperature: Sampling temperature override (else
            ``action.model_temperature``).
        max_tokens: Max tokens override (else ``action.model_max_tokens``).
        model_action: Inject a LanguageModelAction; skips provider/default
            resolution.
        calling_action_name: Observability label (else
            ``action.get_class_name()``).

    Returns:
        See module docstring.
    """
    if action is None:
        return False

    conversation_history: Optional[List[Dict[str, Any]]]
    if history is not None:
        conversation_history = history
    elif use_history:
        conversation_history = await _load_history(
            interaction=interaction,
            history_limit=history_limit,
            with_utterance=with_utterance,
            with_response=with_response,
            with_interpretation=with_interpretation,
            with_event=with_event,
            max_statement_length=max_statement_length,
        )
    else:
        conversation_history = None

    resolved_model = await _resolve_model_action(
        action, model_action=model_action, provider=provider
    )
    if not resolved_model:
        return False

    use_stream = False if json_response else bool(stream)
    model_id = _pick(model, action, "model")
    temp = _pick(temperature, action, "model_temperature")
    tokens = _pick(max_tokens, action, "model_max_tokens")
    caller = calling_action_name or action.get_class_name()

    kwargs: Dict[str, Any] = {
        "prompt": user_prompt,
        "stream": use_stream,
        "system": system_prompt,
        "history": conversation_history,
        "calling_action_name": caller,
    }
    if model_id is not None:
        kwargs["model"] = model_id
    if temp is not None:
        kwargs["temperature"] = temp
    if tokens is not None:
        kwargs["max_tokens"] = tokens
    if json_response:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        result_str = await resolved_model.generate(**kwargs)
        if not json_response:
            return result_str

        cleaned = strip_json_fences(result_str or "")
        if not cleaned:
            return None
        if not cleaned.lstrip().startswith(("{", "[")):
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return None
            cleaned = cleaned[start : end + 1]
        return json.loads(cleaned)
    except Exception as exc:
        logger.error("Error in LLM helper: %s", exc)
        return None


__all__ = ["call_model"]
