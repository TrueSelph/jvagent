"""Contracts for SkillAction: programmatic interface types.

SkillRunConfig, SkillRunContext, and SkillRunResult form the public API
any Action (or service) uses to invoke SkillAction without coupling to
InteractWalker or the interact subsystem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union

# Default model used when no explicit model is specified (4.1).
# Update this constant (not the field default) when rotating model versions.
DEFAULT_SKILL_MODEL: str = "claude-sonnet-4-20250514"


class LoopPhase(str, Enum):
    """Explicit phase transitions for the think-act-observe loop state machine."""

    INIT = "init"
    MODEL_CALL = "model_call"
    TOOL_DISPATCH = "tool_dispatch"
    OBSERVE = "observe"
    NUDGE = "nudge"
    FINALIZE = "finalize"
    TERMINATE = "terminate"


class TerminationReason(str, Enum):
    """Canonical termination reasons shared between SkillAction and TaskService."""

    COMPLETED = "completed"
    ITER_CAP = "max_iterations"
    TIME_CAP = "timed_out"
    ERROR = "failed"
    STUCK = "stuck_forced"


@dataclass
class SkillRunConfig:
    """All tunable parameters for one SkillAction run.

    Mirror of the declarative attributes on SkillInteractAction; collected
    here so non-interact callers can supply them as a plain dataclass.
    """

    # ---- Model ----
    model: str = DEFAULT_SKILL_MODEL
    model_temperature: float = 0.3
    model_max_tokens: int = 8192
    model_action_type: str = "AnthropicLanguageModelAction"

    # ---- Guardrails ----
    max_iterations: int = 25
    max_duration_seconds: float = 300.0

    # ---- Reasoning ----
    reasoning_budget_tokens: int = 0
    reasoning_enabled: Optional[bool] = None
    reasoning_effort: Optional[str] = None
    reasoning_extra: Optional[Dict[str, Any]] = None
    mirror_assistant_stream_as_thoughts: Optional[bool] = None

    # ---- Streaming ----
    stream_thinking: bool = True
    stream_reasoning: bool = True
    stream_tool_progress: bool = True
    commit_intermediate_messages: bool = True
    relay_thoughts_to_channels: bool = False

    # ---- Context window ----
    max_full_tool_results: int = 10
    max_tool_result_tokens: int = 400
    tool_result_truncation_chars: int = 500
    history_limit: int = 5
    call_timeout_seconds: float = 60.0

    # ---- Skills ----
    # Valid values: None (all skills), "-all" (no skills), a glob string, or a list of glob strings.
    skills: Optional[Union[str, List[str]]] = None
    denied_skills: List[str] = field(default_factory=list)
    skills_source: str = "both"
    enable_skill_helper_tools: bool = True
    max_skill_activations: int = 8
    skill_first_retry_limit: int = 1
    skill_first_retry_min_relevance: float = 0.25
    prioritize_skills_first: bool = True

    # ---- Tools ----
    tool_servers: List[str] = field(default_factory=list)
    allow_local_tools: bool = False
    local_tools_path: Optional[str] = None

    # ---- Grounding / quality ----
    strict_grounding: bool = True
    # When True, substantive tools are blocked until task_tracker create (except helpers;
    # meta-utterance turns skip the gate). See SkillAction._apply_plan_first_tool_gate.
    plan_first: bool = True
    final_review: bool = True
    final_review_max_plan_steps: Optional[int] = None

    # ---- Task-tracker loop nudges ----
    task_nudge_retry_limit: int = 2
    # Hard ceiling on total task-plan nudges across the entire loop (5.5).
    # Prevents unbounded nudging when the counter resets on tool calls.
    max_total_task_nudges: int = 6

    # ---- Task plan limits ----
    # Maximum steps allowed in a single task plan (5.8).
    max_task_plan_steps: int = 50

    # ---- Stuck detection ----
    stuck_detection_window: int = 3
    # Cosine-similarity threshold for semantic intent matching in StuckDetector (3.4).
    stuck_intent_similarity_threshold: float = 0.7
    max_midcourse_corrections: int = 2

    # ---- Progress checks ----
    progress_check_interval: int = 5

    # ---- Conversational heuristics ----
    conversational_skip_patterns: List[str] = field(default_factory=list)
    skill_first_conversational_heuristic: bool = True
    conversational_short_utterance_max_chars: int = 60
    conversational_short_utterance_max_tokens: int = 8
    conversational_heuristic_max_relevance: float = 3.0
    conversational_min_response_chars: int = 20
    meta_intent_skip_nudge: bool = True
    meta_intent_patterns: List[str] = field(default_factory=list)

    # ---- Candidate quality ----
    degenerate_response_max_chars: int = 25
    best_candidate_shrink_ratio: float = 0.4

    # ---- Response delivery ----
    response_mode: str = "publish"

    # ---- Infrastructure toggles ----
    enable_checkpoints: bool = True
    enable_evidence_log: bool = True


@dataclass
class SkillRunContext:
    """All dependencies required to execute a SkillAction run.

    Decouples the skill loop engine from InteractWalker so any Action —
    or any Python service — can invoke SkillAction.run_to_completion()
    without going through the interact subsystem.

    Interact-specific fields (interaction, response_bus, session_id, channel,
    stream) may be None when called from a non-interact context.  In that
    case the caller should supply a ``publish_callback`` for response delivery.

    Args:
        utterance: The user's input text driving the task.
        conversation: Conversation node (memory anchor for tasks/checkpoints).
        model_action: Pre-resolved LanguageModelAction instance.
        task_service: TaskService bound to this conversation.
        config: Full loop configuration.
        interaction: Interact-subsystem Interaction node (optional).
        response_bus: ResponseBus for streaming output (optional).
        session_id: Session ID for ResponseBus routing (optional).
        channel: Delivery channel (optional).
        stream: Whether the client expects streamed output.
        agent: Agent node, used for MCP / action resolution.
        user_id: Authenticated end-user id for ToolExecutor / MCP per-user
            filesystem sandboxes (optional; system _default directory used when absent).
        publish_callback: Async callable used instead of ResponseBus when
            response_bus is None.  Signature::

                async def cb(content: str, *, category: str, thought_type: str | None,
                             segment_id: str | None, streaming_complete: bool,
                             relay_to_adapters: bool) -> None: ...

        agent_name: Persona name injected into the system prompt.
        agent_description: Persona description injected into the system prompt.
        skill_state: Mutable dict shared with SkillInteractAction for hot-reload
            (``refresh_skills``). Populated by SkillAction after ``prepare_run``.
    """

    utterance: str
    conversation: Any
    model_action: Any
    task_service: Any
    config: SkillRunConfig

    # Optional interact-subsystem fields
    interaction: Optional[Any] = None
    response_bus: Optional[Any] = None
    session_id: Optional[str] = None
    channel: Optional[str] = None
    stream: bool = False

    # Agent for action/MCP resolution
    agent: Optional[Any] = None

    # End-user id (e.g. InteractWalker.user_id) for per-user MCP sandbox paths
    user_id: Optional[str] = None

    # Non-interact output delivery
    publish_callback: Optional[Callable] = None

    # Persona identity for system prompt
    agent_name: str = "Agent"
    agent_description: str = "An intelligent skills-based agent."

    # Mutable hot-reload state dict (set by SkillInteractAction; written after prepare_run)
    skill_state: Optional[Dict[str, Any]] = None


@dataclass
class SkillRunResult:
    """Complete result of a finished SkillAction run.

    Returned by ``SkillAction.run_to_completion()`` regardless of caller type.
    """

    final_response: str
    termination_reason: TerminationReason
    stuck_corrections: int
    result_attributions: List[Dict[str, Any]]
    iterations: int
    duration_seconds: float
    task_id: Optional[str]
    activated_skills: List[str]
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Steps that were still pending/in_progress when the loop ended (abandoned).
    # 0 means every tracked step was completed or explicitly skipped.
    task_plan_abandoned_steps: int = 0
    # Steps that were explicitly skipped by the model via task_tracker skip.
    task_plan_intentional_skips: int = 0
