"""Groq language model action implementation.

Provides integration with Groq's OpenAI-compatible inference API. Groq
runs Llama and Mixtral models on dedicated LPU hardware with very low
latency (sub-200ms p50 on small models), making it the typical provider
choice for :class:`ReflexHelm` in Bridge deployments.

Because the API is OpenAI-compatible at the wire level, this class is a
thin subclass of :class:`OpenAILanguageModelAction` that only changes:

- ``api_endpoint`` — points at Groq.
- default ``model`` — picks a fast Llama variant.
- ``_http_bearer_token`` — reads ``GROQ_API_KEY`` (falls back to
  ``OPENAI_API_KEY`` for shared-key deployments, mirroring OpenRouter's
  fallback).

Reasoning-model detection is left to the OpenAI parent: Groq does not
expose reasoning models with the o1/o3-style API today, so the parent's
heuristic ("model id contains 'o1' / 'o3'") naturally returns False.

Configuration:
    api_key:      Groq API key (from environment, ``GROQ_API_KEY``)
    api_endpoint: Groq API base (defaults to ``https://api.groq.com/openai/v1``)
    model:        Groq model id (e.g. ``llama-3.1-8b-instant``,
                  ``llama-3.3-70b-versatile``, ``mixtral-8x7b-32768``)
    temperature:  Sampling temperature
    max_tokens:   Maximum tokens to generate
"""

import logging
from typing import Any

from jvspatial.core.annotations import attribute

from jvagent.action.model.language.openai.openai import OpenAILanguageModelAction

logger = logging.getLogger(__name__)


class GroqLanguageModelAction(OpenAILanguageModelAction):
    """Groq language model integration action.

    Implements the LanguageModelAction interface using Groq's
    OpenAI-compatible inference API. The default model
    (``llama-3.1-8b-instant``) is selected for Reflex-class latency
    targets; deployments that need higher quality at slightly higher
    latency can override to ``llama-3.3-70b-versatile``.

    Examples:
        Reflex helm pointing at Groq for sub-500ms first-response:

        >>> # agent.yaml
        >>> - action: jvagent/reflex_helm
        >>>   context:
        >>>     model_action_type: GroqLanguageModelAction
        >>>     model: llama-3.1-8b-instant
        >>> - action: jvagent/groq_lm
        >>>   context:
        >>>     enabled: true
        >>>     # GROQ_API_KEY in env
    """

    # Groq-specific configuration overrides.
    api_endpoint: str = attribute(
        default="https://api.groq.com/openai/v1",
        description="Groq API endpoint URL (OpenAI-compatible).",
    )
    model: str = attribute(
        default="llama-3.1-8b-instant",
        description=(
            "Groq model identifier. Fast defaults to "
            "``llama-3.1-8b-instant`` (sub-200ms typical). Higher-quality "
            "options: ``llama-3.3-70b-versatile``, ``mixtral-8x7b-32768``."
        ),
    )
    provider: str = attribute(default="groq", description="Provider name")

    def _http_bearer_token(self) -> str:
        """Resolve the bearer token for Groq API requests.

        Reads ``GROQ_API_KEY`` from the action context / environment.
        **No fallback** to ``OPENAI_API_KEY`` — Groq issues its own
        keys, and OpenAI keys are rejected with HTTP 401. Mirroring
        OpenRouter's fallback pattern here is incorrect because
        OpenRouter accepts OpenAI-compatible keys while Groq does not.

        When ``GROQ_API_KEY`` is unset, returns an empty string. The
        OpenAI-parent ``_query`` path then raises before any HTTP call,
        and the calling helm (typically ReflexHelm) catches the
        exception and falls through to its safe-default path.
        """
        return self.api_key_from_context("GROQ_API_KEY")

    def _detect_reasoning_model(self, model_id: str, **kwargs: Any) -> bool:
        """Groq does not expose o1/o3-style reasoning models today.

        Override returns False unconditionally so the parent's heuristic
        (which would match ``o1`` substrings in larger Llama model ids
        by accident — e.g. nothing today, but future-proofing) cannot
        misclassify a Groq model as a reasoning model and reshape the
        request payload.
        """
        return False
