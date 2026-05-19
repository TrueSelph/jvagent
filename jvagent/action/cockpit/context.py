from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from jvagent.action.cockpit.contracts import TerminationReason

if TYPE_CHECKING:
    from jvagent.action.cockpit.config import CockpitConfig


@dataclass
class CockpitStepResult:
    """Outcome of a single CockpitEngine step."""

    status: str  # "tool_calls" | "final_response" | "timeout" | "budget_exhausted" | "stuck"
    final_response: Optional[str] = None
    termination_reason: Optional[TerminationReason] = None
    iterations: int = 0
    duration_seconds: float = 0.0
    activated_skills: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CockpitResult:
    final_response: str
    termination_reason: TerminationReason
    iterations: int
    duration_seconds: float
    activated_skills: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CockpitState:
    """Persisted state between walker visits for the cockpit revisit pattern."""

    messages: List[Dict[str, Any]] = field(default_factory=list)
    iteration: int = 0
    activated_skills: List[str] = field(default_factory=list)
    started_at: float = 0.0
    tools_serialized: List[Dict[str, Any]] = field(default_factory=list)
    recent_tool_names: List[List[str]] = field(default_factory=list)
    recent_tool_signatures: List[List[str]] = field(default_factory=list)


@dataclass
class CockpitContext:
    utterance: str
    conversation: Any
    interaction: Any
    agent: Any
    model_action: Any
    config: "CockpitConfig"
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

    # Live tool registry (set by ``assemble_cockpit_tools`` after the full
    # surface is built). Exposed on ctx so the ``skill_activate`` harness
    # tool can hot-register additional skill tools mid-loop.
    registry: Optional[Any] = None
    # Action resolver for the running agent (same instance the assembler
    # creates). Cached here so dynamic activation can pass it to
    # ``load_one_skill`` without re-instantiating.
    action_resolver: Optional[Any] = None
    # Set to True by ``skill_activate`` after registering new tools.
    # ``CockpitEngine.step`` checks this at the top of each iteration and
    # re-serialises the tool list before the next model call.
    registry_dirty: bool = False
    # Counter for dynamic activations within this cockpit run; capped by
    # ``CockpitConfig.max_dynamic_activations`` to bound runaway behaviour.
    dynamic_activations: int = 0

    @property
    def agent_name(self) -> str:
        return getattr(self.persona, "persona_name", "Agent")

    @property
    def agent_description(self) -> str:
        return getattr(self.persona, "persona_description", "")
