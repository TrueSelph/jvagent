"""Reasoning-helm engine context, state, and result dataclasses.

Initially duplicated from ``jvagent/action/cockpit/context.py`` at commit
``4bc6db6`` as part of C-2 (BRIDGE-ROADMAP §C). Class names were renamed
from ``Cockpit*`` to ``Engine*`` in Phase 3 to reflect this module's
mission (Bridge-orchestrated engine, not a standalone Cockpit).
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from jvagent.action.helm.reasoning.contracts import TerminationReason

if TYPE_CHECKING:
    from jvagent.action.helm.reasoning.config import EngineConfig


@dataclass
class EngineStepResult:
    """Outcome of a single Engine step."""

    status: str  # "tool_calls" | "final_response" | "timeout" | "budget_exhausted" | "stuck"
    final_response: Optional[str] = None
    termination_reason: Optional[TerminationReason] = None
    iterations: int = 0
    duration_seconds: float = 0.0
    activated_skills: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EngineResult:
    final_response: str
    termination_reason: TerminationReason
    iterations: int
    duration_seconds: float
    activated_skills: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EngineState:
    """Persisted state between walker visits for the engine revisit pattern."""

    messages: List[Dict[str, Any]] = field(default_factory=list)
    iteration: int = 0
    activated_skills: List[str] = field(default_factory=list)
    started_at: float = 0.0
    tools_serialized: List[Dict[str, Any]] = field(default_factory=list)
    recent_tool_names: List[List[str]] = field(default_factory=list)
    recent_tool_signatures: List[List[str]] = field(default_factory=list)


@dataclass
class EngineContext:
    utterance: str
    conversation: Any
    interaction: Any
    agent: Any
    model_action: Any
    config: "EngineConfig"
    response_bus: Any
    session_id: str
    channel: str
    stream: bool
    user_id: Optional[str]
    persona: Any
    action: Any
    visitor: Any
    preloaded_skills: List[str]
    # Subset of ``preloaded_skills`` that came directly from the router
    # (excludes always-active skills). Used by the engine's pre-dispatch
    # path to gate structural skill invocation: only fire when the router
    # was confident enough to return exactly ONE skill. Multi-skill routes
    # are ambiguous and defer to the model.
    routed_skills: List[str] = field(default_factory=list)
    publish_callback: Optional[Callable] = None

    # Live tool registry (set by ``assemble_engine_tools`` after the full
    # surface is built). Exposed on ctx so the ``skill_activate`` harness
    # tool can hot-register additional skill tools mid-loop.
    registry: Optional[Any] = None
    # Action resolver for the running agent (same instance the assembler
    # creates). Cached here so dynamic activation can pass it to
    # ``load_one_skill`` without re-instantiating.
    action_resolver: Optional[Any] = None
    # Set to True by ``skill_activate`` after registering new tools.
    # ``Engine.step`` checks this at the top of each iteration and
    # re-serialises the tool list before the next model call.
    registry_dirty: bool = False
    # Counter for dynamic activations within this engine run; capped by
    # ``EngineConfig.max_dynamic_activations`` to bound runaway behaviour.
    dynamic_activations: int = 0

    @property
    def agent_name(self) -> str:
        return getattr(self.persona, "persona_name", "Agent")

    @property
    def agent_description(self) -> str:
        return getattr(self.persona, "persona_description", "")
