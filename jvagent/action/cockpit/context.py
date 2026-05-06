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
    publish_callback: Optional[Callable] = None

    @property
    def agent_name(self) -> str:
        return getattr(self.persona, "persona_name", "Agent")

    @property
    def agent_description(self) -> str:
        return getattr(self.persona, "persona_description", "")
