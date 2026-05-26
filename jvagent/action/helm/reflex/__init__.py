"""ReflexHelm — sub-500ms first-response helm (BRIDGE-ROADMAP §E, ADR-0007 v0).

ReflexHelm is a fast classifier helm orchestrated by ``BridgeInteractAction``.
It owns the trivial-turn path:

- Greetings, smalltalk, acknowledgements → :class:`EMIT` directly.
- Anything substantive → :class:`SHIFT` to the peer helm whose
  :class:`Manifest` best matches (typically ``ReasoningHelm``).

Latency target: sub-500ms p50 on trivial turns. The helm consumes a single
fast-model call per visit (default ``OpenAILanguageModelAction`` /
``gpt-4o-mini``); structured JSON output avoids the round-trip overhead
of function-calling tool surfaces.

The peer-awareness prompt is built at runtime from every other
:class:`BaseHelm` instance on the agent, reading each helm's
``get_manifest()`` (D) — so Reflex learns about Reasoning / Persona /
Specialist helms without code changes.

Public exports:
- :class:`ReflexHelm`
"""

from jvagent.action.helm.reflex import endpoints  # noqa: F401
from jvagent.action.helm.reflex.reflex_helm import ReflexHelm

__all__ = ["ReflexHelm"]
