"""The typed boundary between preparing a turn and stepping it.

``_run_loop`` was 1090 lines in one scope: roughly 390 lines deciding what the
turn *is* (tools, skills, parameters, flow ownership, budget) followed by 700
lines stepping it, with 82 locals visible to both halves. Any change to either
could reach the other, which is how several fixes on this branch had to be
threaded through by hand.

The two halves actually communicate through exactly 47 names — measured, not
guessed, by walking the function's AST for names stored before the split and
loaded after it. Four more looked like they crossed and do not: ``doc``,
``emitted``, ``prep_obs_before`` and ``tool_t0`` are assigned by the loop before
it reads them, so they are loop-locals that merely share a name with a setup
binding.

Of the 47, **18 are read-only** and 29 are rebound during the loop. That is why
this is a mutable dataclass rather than a frozen one: the tool surface genuinely
changes mid-turn when a skill activates, and the counters are loop state. A
frozen context would have to lie about a third of the fields.

This class does not try to be a good abstraction. It is an honest inventory of a
boundary that used to be invisible — and a list of 47 is something you can argue
about and shrink, which 82 shared locals is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class TurnState:
    """What ``_prepare_turn`` hands to the tick loop."""

    # --- identity of the turn -------------------------------------------
    utterance: str
    interaction: Any
    history: List[Dict[str, str]]

    # --- the surface the model reasons over ------------------------------
    tools: Dict[str, Any]
    visible: Set[str]
    activated: List[str]
    skill_docs: List[Any]
    skill_names: Set[str]
    skills_section: str
    capabilities_section: str
    parameters_section: str
    lean_surface: bool
    loop_actions: List[Any]
    user_named_tools: Any
    active_skill_doc: Any
    refreshed: Any

    # --- flow ownership and continuation ---------------------------------
    flow_owner: Optional[str]
    flow_note: str
    plan_note: str
    pending_chain: Any
    drain_directive: Any
    locked_companion_skill_names: Any
    locked_companion_tools: Any

    # --- budget and deadlines --------------------------------------------
    budget: int
    deadline: float
    loop_t0: float
    ticks: int
    ticks_light: int
    ticks_heavy: int

    # --- observations and telemetry --------------------------------------
    observations: List[Dict[str, Any]]
    tool_timings: List[Dict[str, Any]]
    last_obs: Any
    last_sig: Any
    ended_via: str

    # --- guard counters (ADR-0034 / ADR-0037 enforcement) -----------------
    chain_deflections: int
    plan_deflections: int
    grounding_deflections: int
    deflected_named: Set[str]
    repeats: Any
    nd_streak: int
    substantive_tool_calls: int
    soft_abandon_evaluated: bool
    soft_abandon_streak: int
    soft_abandon_title: str

    # --- the transient acknowledgement task -------------------------------
    ack_started: bool
    ack_task: Any = field(default=None)
