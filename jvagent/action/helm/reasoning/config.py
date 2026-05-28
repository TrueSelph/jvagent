"""Reasoning-helm engine config.

Duplicated from ``jvagent/action/cockpit/config.py`` at commit ``4bc6db6``
as part of C-2 (BRIDGE-ROADMAP §C). Zero imports from
``jvagent.action.cockpit`` per the C-strategy hard constraint. The
``EngineConfig`` class name is preserved verbatim so the duplicated
modules diff cleanly against their standalone-Cockpit ancestors during review.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

DEFAULT_SKILL_MODEL: str = "claude-sonnet-4-20250514"


@dataclass
class EngineConfig:
    model: str = DEFAULT_SKILL_MODEL
    model_temperature: float = 0.3
    model_max_tokens: int = 8192
    model_action_type: str = "AnthropicLanguageModelAction"

    max_iterations: int = 25
    max_duration_seconds: float = 300.0
    max_concurrent_tools: int = 5
    tool_call_timeout: float = 60.0
    sanitize_tool_errors: bool = True

    stuck_detection_window: int = 4
    stuck_intent_jaccard_threshold: float = 0.65
    stuck_primary_tool_repeat: int = 4
    stuck_min_iterations: int = 4

    plan_first: bool = True
    max_task_plan_steps: int = 50

    skills: Optional[Union[str, List[str]]] = None
    denied_skills: List[str] = field(default_factory=list)
    skills_source: str = "both"

    response_mode: str = "publish"

    # Single switch for internal-progress streaming
    # (model thoughts, reasoning chunks, tool progress badges).
    stream_internal_progress: bool = True

    # Defends against raw tool/skill invocation embedded in a user message
    # (e.g. "/skill web_search", "call memory_set ..."). When True (the
    # secure default), the engine system prompt is augmented with a
    # security block instructing the model to treat user text as content,
    # not a command, and to never dispatch a tool just because its name
    # appears in the utterance. Turn off only on agents that intentionally
    # want to expose tool dispatch through natural language (rare).
    block_raw_tool_invocation: bool = True

    enable_skill_helper_tools: bool = True
    enable_artifact_tools: bool = True
    enable_capability_search: bool = True
    # Cap on dynamic ``skill_activate`` invocations per engine run.
    # Prevents an agent loop from runaway-activating the full catalogue.
    # 0 disables the ``skill_activate`` harness tool entirely.
    max_dynamic_activations: int = 10
    # Harness-tool tier: "minimal" | "standard" | "full". Trims rarely-used
    # harness tools from the engine prompt to control token cost. Action and
    # skill tools are not affected by this knob.
    tool_tier: str = "standard"
    skill_index_inline_max_skills: int = 5

    # Phase B: general-purpose memory pre-load
    preload_user_memory: bool = True
    user_memory_max_chars: int = 4096

    # Auto-track each engine run as a Task so observability sees structured
    # progress (active_tasks / completed_tasks on the interaction response)
    # even when the model doesn't explicitly call task_create_plan.
    auto_track_tasks: bool = True

    history_limit: int = 5
    max_statement_length: Optional[int] = None

    reasoning_budget_tokens: int = 0
    reasoning_enabled: Optional[bool] = None
    reasoning_effort: Optional[str] = None
    reasoning_extra: Optional[Dict[str, Any]] = None

    degenerate_response_max_chars: int = 25
    tool_servers: List[str] = field(default_factory=list)

    # Overridable prompt templates (passed through from ReasoningHelm).
    # Empty string = use module-level constant from reasoning/prompts.py.
    system_prompt: str = ""
    task_planning_prompt: str = ""
    security_prompt: str = ""
    capability_search_prompt: str = ""
    citation_instruction: str = ""

    # ── User-facing fallback messages (Wave 9j.6) ────────────────────
    # Returned as ``EngineStepResult.final_response`` when the engine
    # terminates without producing a user-facing answer. Defaults are
    # neutral and free of (a) internal mechanics ("maximum number of
    # steps", "time limit"), (b) banned closer phrases ("let me know
    # if", "feel free to", "anything else"), and (c) options-menu
    # shapes. Operators can override per-deployment.
    time_cap_response_text: str = (
        "I wasn't able to finish that. Please rephrase or simplify the request."
    )
    iter_cap_response_text: str = (
        "I wasn't able to finish that. Please rephrase or simplify the request."
    )
    stuck_response_text: str = (
        "I wasn't able to finish that. Please rephrase or simplify the request."
    )
    error_response_text: str = "Something went wrong. Please try again."
