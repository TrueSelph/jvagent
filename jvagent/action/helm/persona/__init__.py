"""PersonaHelm — wraps :class:`PersonaAction` as a Bridge helm
(BRIDGE-ROADMAP §G, ADR-0007 v0).

PersonaHelm is the polish-and-deliver helm. Other helms ``SHIFT`` into it
with a ``handoff_state`` carrying the draft they want polished, and
PersonaHelm renders the final response through :class:`PersonaAction`
with the agent's voice. ``latency_class: quick`` — typically a single
fast model call.

Use cases:

- ReasoningHelm produces a structured answer; SHIFTs to PersonaHelm
  with the answer as a draft so PersonaAction can reshape it to the
  agent's voice without re-running the engine.
- ReflexHelm decides a trivial answer needs persona polish (rare) and
  SHIFTs here.
- The legacy ``response_deliver_via_persona`` tool will become an
  alias that issues ``SHIFT(target=PersonaHelm)`` in a future commit.

Public exports:
- :class:`PersonaHelm`
"""

from jvagent.action.helm.persona import endpoints  # noqa: F401
from jvagent.action.helm.persona.persona_helm import PersonaHelm

__all__ = ["PersonaHelm"]
