"""The loop's working set, and how one tick tells the loop to proceed.

``_run_loop`` was 1090 lines in one scope: roughly 390 lines deciding what the
turn *is* (tools, skills, parameters, flow ownership, budget) followed by 700
lines stepping it, with 82 locals visible to both halves. The first split
(``_prepare_turn`` → ``TurnState`` → loop) made the interface between the two
halves explicit: 47 names, measured by walking the AST. The second split
(audit follow-up S1) cut the 700-line tick into ``_tick`` / ``_tick_final`` /
``_tick_tool`` (``_guard_tool_call`` → ``_dispatch_tool`` → ``_after_dispatch``)
plus ``_after_loop`` and ``_close_turn`` — and those methods share state the
same way: through this object, not a scope. The four names the tick used to
keep as loop-locals across iterations (``last_gear``, ``last_dec_meta``,
``last_obs_len``, ``model_failures``) live here now for the same reason.

It is a mutable dataclass because the tool surface genuinely changes mid-turn
when a skill activates and the counters are loop state; a frozen context would
have to lie about a third of the fields. It does not try to be a good
abstraction — it is an honest inventory of a working set that used to be
invisible, and a list you can argue about and shrink.

:class:`TickOutcome` is what one tick returns: keep going, stop the loop (the
post-loop path decides the egress), or the turn's output is delivered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional, Set


@dataclass(frozen=True)
class TickOutcome:
    """How the loop proceeds after one tick.

    ``continue`` — take another tick. ``break`` — the loop is over; ``ended_via``
    says why and :meth:`_after_loop` decides the egress. ``return`` — the turn's
    output has been delivered (a reply, a terminal tool, a directive); nothing
    more is sent.
    """

    CONTINUE: ClassVar[str] = "continue"
    BREAK: ClassVar[str] = "break"
    RETURN: ClassVar[str] = "return"

    kind: str
    ended_via: Optional[str] = None

    # The shared "take another tick" outcome (no reason to carry).
    CONTINUE_: ClassVar["TickOutcome"]

    @classmethod
    def stop(cls, ended_via: str) -> "TickOutcome":
        return cls(cls.BREAK, ended_via)

    @classmethod
    def done(cls, ended_via: str) -> "TickOutcome":
        return cls(cls.RETURN, ended_via)


TickOutcome.CONTINUE_ = TickOutcome(TickOutcome.CONTINUE)


@dataclass
class RecentCall:
    """One dispatched tool call the repeat guard remembers: its signature
    (tool name + serialised args) and whether the attempt errored/timed out."""

    sig: tuple
    errored: bool = False


@dataclass
class TurnState:
    """The turn's working set: built by ``_prepare_turn``, stepped by ``_tick``."""

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
    # Recent tool-call signatures with whether each attempt errored (repeat
    # guard, ADR audit M7): a bounded deque of ``RecentCall``.
    recent_calls: Any
    ended_via: str

    # --- guard counters (ADR-0034 / ADR-0037 enforcement) -----------------
    chain_deflections: int
    plan_deflections: int
    grounding_deflections: int
    deflected_named: Set[str]
    nd_streak: int
    substantive_tool_calls: int
    soft_abandon_evaluated: bool
    soft_abandon_streak: int
    soft_abandon_title: str

    # --- the transient acknowledgement task -------------------------------
    ack_started: bool
    ack_task: Any = field(default=None)

    # --- carried across ticks (tick-loop locals before the S1 split) -------
    last_gear: str = "light"
    # Native-protocol transcript bookkeeping (ADR-0044): the decision the
    # previous tick acted on, and where its observations start, so the tool
    # result it produced can be tied back to the provider's tool-call id.
    last_dec_meta: Dict[str, Any] = field(default_factory=dict)
    last_obs_len: int = 0
    # Consecutive provider failures this turn (a fault, not a model choice).
    model_failures: int = 0
    # Ticks that dispatched more than one tool call (max_concurrent_tools > 1).
    parallel_batches: int = 0
