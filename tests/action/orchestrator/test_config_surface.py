"""Orchestrator configuration surface (ADR-0015): reasoning passthrough,
thinking/progress stream, budgets, and tooling/UX knobs (tier, block-raw,
transient ack, MCP tool-server selection)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from jvagent.action.orchestrator.core_tools import build_core_tools
from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)

# --- System-prompt override -----------------------------------------------


def _compose(ex):
    return ex._compose_system_prompt(
        identity_section="You are Ada.\n\n",
        tools_section="- reply: say something",
        skills_section="- research: investigate",
    )


async def test_system_prompt_default_is_builtin():
    ex = OrchestratorInteractAction()
    out = _compose(ex)
    assert "You are Ada." in out
    assert "- reply: say something" in out
    assert "- research: investigate" in out
    assert "AVAILABLE TOOLS:" in out  # built-in body present


async def test_system_prompt_extra_is_appended():
    ex = OrchestratorInteractAction()
    ex.system_prompt_extra = "HOUSE RULE: always greet in French."
    out = _compose(ex)
    assert "AVAILABLE TOOLS:" in out  # built-in still there
    assert out.rstrip().endswith("HOUSE RULE: always greet in French.")


async def test_system_prompt_override_replaces_body():
    ex = OrchestratorInteractAction()
    # str.format template — literal JSON braces must be doubled.
    ex.system_prompt = (
        "{identity_section}CUSTOM EXECUTIVE. Tools:\n{tools_section}\n"
        'Skills:\n{skills_section}\nReply with {{"action": "final"}}.'
    )
    out = _compose(ex)
    assert "CUSTOM EXECUTIVE." in out
    assert "AVAILABLE TOOLS:" not in out  # built-in body replaced
    assert "You are Ada." in out  # identity token substituted
    assert "- reply: say something" in out and "- research: investigate" in out
    assert '{"action": "final"}' in out  # doubled braces collapse to one


async def test_system_prompt_override_bad_placeholder_falls_back():
    ex = OrchestratorInteractAction()
    ex.system_prompt = "Broken {unknown_token} template"  # KeyError on format
    out = _compose(ex)
    assert "AVAILABLE TOOLS:" in out  # fell back to built-in default
    assert "- reply: say something" in out


async def test_system_prompt_override_and_extra_combine():
    ex = OrchestratorInteractAction()
    ex.system_prompt = "CUSTOM {tools_section} // {skills_section}"
    ex.system_prompt_extra = "AND BE BRIEF."
    out = _compose(ex)
    assert out.startswith("CUSTOM ")
    assert out.rstrip().endswith("AND BE BRIEF.")


async def test_subprompts_default_to_constants():
    from jvagent.action.orchestrator import prompts as P

    ex = OrchestratorInteractAction()
    assert ex.system_prompt == P.ORCHESTRATOR_SYSTEM_PROMPT
    assert ex.user_prompt == P.ORCHESTRATOR_USER_PROMPT_TEMPLATE
    assert ex.tool_use_policy_prompt == P.TOOL_USE_POLICY
    assert ex.flow_in_progress_prompt == P.FLOW_IN_PROGRESS_PROMPT
    assert ex.length_limit_prompt == P.LENGTH_LIMIT_PROMPT
    assert ex.finalize_prompt == P.FINALIZE_PROMPT
    assert ex.no_skills_text == P.NO_SKILLS_AVAILABLE


async def test_fmt_helper_falls_back_on_bad_template():
    # Bad override → built-in default used; good override → applied.
    assert OrchestratorInteractAction._fmt("hi {x}", "DEF {x}", x="there") == "hi there"
    assert OrchestratorInteractAction._fmt("bad {nope}", "DEF {x}", x="ok") == "DEF ok"


# --- Phase 1: reasoning passthrough ---------------------------------------


async def test_reasoning_kwargs_disabled_by_default():
    assert OrchestratorInteractAction()._reasoning_kwargs() == {}


async def test_reasoning_kwargs_effort_and_budget():
    ex = OrchestratorInteractAction()
    ex.reasoning_effort = "high"
    ex.reasoning_budget_tokens = 2048
    ex.reasoning_extra = {"foo": "bar"}
    out = ex._reasoning_kwargs()
    assert out["reasoning_effort"] == "high"
    assert out["reasoning"]["effort"] == "high"
    assert out["reasoning"]["budget_tokens"] == 2048
    assert out["reasoning"]["foo"] == "bar"


async def test_reasoning_kwargs_explicit_disable():
    ex = OrchestratorInteractAction()
    ex.reasoning_enabled = False
    out = ex._reasoning_kwargs()
    assert out["reasoning_effort"] is None
    assert out["reasoning"] == {"enabled": False}


async def test_reasoning_threads_into_model_call(monkeypatch):
    ex = OrchestratorInteractAction()
    ex.reasoning_effort = "medium"
    ex.max_statement_length = 200
    captured = {}

    model = MagicMock()

    async def _qm(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(response='{"action":"final","answer":"hi"}')

    model.query_messages = _qm

    async def _gma(self, required=False):
        return model

    async def _agent(self):
        return SimpleNamespace(alias="Ex", role="a guide")

    monkeypatch.setattr(OrchestratorInteractAction, "get_model_action", _gma)
    monkeypatch.setattr(OrchestratorInteractAction, "get_agent", _agent)

    await ex._run_model(MagicMock(), "hi", [], [], [])
    assert captured.get("reasoning_effort") == "medium"
    assert "LENGTH LIMIT" in captured["system"]
    assert "200 characters" in captured["system"]


# --- Phase 2: thinking / progress stream ----------------------------------


async def test_progress_line_variants():
    pl = OrchestratorInteractAction._progress_line
    assert "research" in pl("tool", "use_skill", {"name": "research"}, {})
    assert pl("tool", "reply", {}, {}) == "Composing a reply…"
    assert pl("tool", "web_search", {}, {}) == "Using web_search…"
    assert pl("final", "", {}, {}) == "Wrapping up…"
    # An explicit model thought wins.
    assert pl("tool", "x", {}, {"thought": "Looking it up"}) == "Looking it up"


async def test_emit_thought_noop_without_bus():
    ex = OrchestratorInteractAction()
    v = MagicMock()
    v.response_bus = None
    v.session_id = None
    await ex._emit_thought(v, "thinking…")  # must not raise


async def test_emit_thought_publishes_thought_over_bus():
    ex = OrchestratorInteractAction()
    bus = MagicMock()
    bus.publish = AsyncMock()
    v = MagicMock()
    v.response_bus = bus
    v.session_id = "sess_1"
    v.channel = "web"
    v.interaction = SimpleNamespace(id="int_1", user_id="u")
    await ex._emit_thought(v, "thinking…")
    assert bus.publish.await_args.kwargs["category"] == "thought"
    assert bus.publish.await_args.kwargs["transient"] is True


async def test_reasoning_trace_emitted_when_enabled(monkeypatch):
    ex = OrchestratorInteractAction()
    ex.stream_reasoning_trace = True
    emitted = []

    async def _emit(self, visitor, text):
        emitted.append(text)

    monkeypatch.setattr(OrchestratorInteractAction, "_emit_thought", _emit)

    model = MagicMock()

    async def _qm(**kwargs):
        return SimpleNamespace(
            response='{"action":"final"}', thinking_content="step-by-step…"
        )

    model.query_messages = _qm

    async def _gma(self, required=False):
        return model

    async def _agent(self):
        return SimpleNamespace(alias="", role="")

    monkeypatch.setattr(OrchestratorInteractAction, "get_model_action", _gma)
    monkeypatch.setattr(OrchestratorInteractAction, "get_agent", _agent)
    await ex._run_model(MagicMock(), "hi", [], [], [])
    assert emitted == ["step-by-step…"]


# --- Phase 3: budgets ------------------------------------------------------


async def test_agentic_default_budget_and_tokens():
    ex = OrchestratorInteractAction()
    assert ex.activation_budget == 24  # room for multistep tool work
    assert ex.model_max_tokens == 4096  # headroom for agentic reasoning + compose


async def test_finalize_clause_added_to_prompt(monkeypatch):
    captured = {}
    model = MagicMock()

    async def _qm(**kwargs):
        captured["system"] = kwargs["system"]
        return SimpleNamespace(response='{"action":"final","answer":"x"}')

    model.query_messages = _qm

    async def _gma(self, required=False):
        return model

    async def _agent(self):
        return SimpleNamespace(alias="", role="")

    monkeypatch.setattr(OrchestratorInteractAction, "get_model_action", _gma)
    monkeypatch.setattr(OrchestratorInteractAction, "get_agent", _agent)
    ex = OrchestratorInteractAction()
    await ex._run_model(MagicMock(), "hi", [], [], [], finalize=True)
    assert "STEP LIMIT REACHED" in captured["system"]


async def test_partial_compose_on_budget_exhaustion(make_orchestrator, make_visitor):
    """When the loop runs out of budget mid-task, force one compose so the user
    gets a partial answer instead of the generic clarify fallback."""
    ex = make_orchestrator(
        activation_budget=2,
        decisions=[
            {"action": "tool", "tool": "noop", "args": {}},
            {"action": "tool", "tool": "noop", "args": {}},
            # consumed by the forced finalize call after the budget is spent
            {"action": "final", "answer": "Here's what I gathered so far."},
        ],
    )
    v = make_visitor(utterance="do a big multistep research task")
    await ex.execute(v)
    assert v.interaction.response == "Here's what I gathered so far."


async def test_single_no_decision_recovers_with_tools(make_orchestrator, make_visitor):
    """A single transient unparseable decision is nudged and retried (tools kept
    visible) — it does NOT abort the turn into a tools=[] finalize."""
    ex = make_orchestrator(
        decisions=[
            None,  # transient no_decision (e.g. truncated thinking output)
            {"action": "final", "answer": "Recovered answer."},
        ],
    )
    v = make_visitor(utterance="research X")
    await ex.execute(v)
    assert v.interaction.response == "Recovered answer."


async def test_no_decision_streak_finalizes(make_orchestrator, make_visitor):
    """A persistent streak of unparseable decisions (work gathered but can't emit
    JSON) falls through to the partial-compose finalize."""
    ex = make_orchestrator(
        decisions=[
            {"action": "tool", "tool": "noop", "args": {}},  # gather an observation
            None,
            None,
            None,  # 3 consecutive → break to finalize
            {"action": "final", "answer": "Done — report saved."},
        ],
    )
    v = make_visitor(utterance="research X and save a report")
    await ex.execute(v)
    assert v.interaction.response == "Done — report saved."


async def test_duration_guard_ends_turn(make_orchestrator, make_visitor):
    # A decision sequence that would loop forever; the wall-clock guard ends it.
    ex = make_orchestrator(
        decisions=[{"action": "tool", "tool": "noop", "args": {}}] * 50
    )
    ex.max_duration_seconds = 1e-9  # deadline already in the past → stop tick 1
    v = make_visitor()
    metrics = []
    v.interaction.observability_metrics = metrics
    v.interaction.save = AsyncMock()
    await ex.execute(v)
    ev = [m for m in metrics if m["event_type"] == "orchestrator_activation"]
    assert ev and ev[-1]["data"]["ended_via"] == "duration"


# --- Model gearing (ADR-0016) --------------------------------------------


async def test_select_gear_logic():
    ex = OrchestratorInteractAction()
    # gearing off (no light_model) → always heavy
    assert ex._select_gear(5, True) == "heavy"
    ex.light_model = "lite"
    ex.escalate_after_tool_calls = 2
    ex.escalate_on_skill = True
    assert ex._select_gear(0, False) == "light"
    assert ex._select_gear(1, False) == "light"
    assert ex._select_gear(2, False) == "heavy"  # tool-count threshold
    assert ex._select_gear(0, True) == "heavy"  # skill active
    ex.escalate_on_skill = False
    assert ex._select_gear(0, True) == "light"  # skill ignored when off


async def test_gearing_on_from_byok_override_without_yaml_light():
    from jvagent.action.model.context import bind_model_override

    ex = OrchestratorInteractAction()
    assert ex._gearing_on() is False
    with bind_model_override(
        {
            "provider": "openai",
            "model": "gpt-4.1",
            "api_key": "sk-byok",
            "light_model": "gpt-4o-mini",
        }
    ):
        assert ex._gearing_on() is True
        assert ex._select_gear(0, False) == "light"


async def test_gear_model_byok_override_heavy_vs_light(monkeypatch):
    from jvagent.action.model.context import bind_model_override

    ex = OrchestratorInteractAction()
    ex.model = "yaml-heavy"
    ex.light_model = "yaml-light"
    heavy = object()

    async def _gma(self, required=False):
        return heavy

    async def _ra(self, action_type, *, profile="heavy"):
        return heavy

    monkeypatch.setattr(OrchestratorInteractAction, "get_model_action", _gma)
    monkeypatch.setattr(OrchestratorInteractAction, "_resolve_model_action", _ra)
    with bind_model_override(
        {
            "provider": "openai",
            "model": "byok-primary",
            "api_key": "sk",
            "light_model": "byok-secondary",
        }
    ):
        _, light_id, _, _, _ = await ex._gear_model("light")
        _, heavy_id, _, _, _ = await ex._gear_model("heavy")
    assert light_id == "byok-secondary"
    assert heavy_id == "byok-primary"


async def test_gearing_escalates_after_one_substantive_tool(
    make_orchestrator, make_visitor, monkeypatch
):
    calls = {"n": 0}
    fake = _fake_capability_action("work", calls)
    ex = make_orchestrator(actions=[fake], decisions=[])
    ex.light_model = "lite"
    ex.escalate_after_tool_calls = 1
    ex.escalate_on_skill = False
    seq = [
        {"action": "tool", "tool": "work", "args": {"i": 1}},
        {"action": "final", "answer": "done"},
    ]
    gears = []

    async def _rm(
        self,
        visitor,
        utterance,
        history,
        tools,
        observations,
        flow_note="",
        skills_section="",
        finalize=False,
        gear="heavy",
        lean=False,
        plan_note="",
        **kwargs,
    ):
        gears.append(gear)
        return seq.pop(0) if seq else {"action": "final", "answer": ""}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _rm)
    await ex.execute(make_visitor(utterance="one tool"))
    assert gears == ["light", "heavy"]


async def test_gear_model_off_uses_heavy(monkeypatch):
    ex = OrchestratorInteractAction()  # light_model="" → gearing off
    heavy = object()

    async def _gma(self, required=False):
        return heavy

    monkeypatch.setattr(OrchestratorInteractAction, "get_model_action", _gma)
    action, model_id, temp, mt, reasoning = await ex._gear_model("light")
    assert action is heavy and reasoning is True  # off → heavy even for "light"


async def test_gear_model_light_profile(monkeypatch):
    ex = OrchestratorInteractAction()
    ex.light_model = "lite"
    ex.light_model_action_type = "LiteAction"
    ex.light_model_temperature = 0.1
    ex.light_model_max_tokens = 512
    lite, heavy = object(), object()

    async def _ga(self, name):
        return lite if name == "LiteAction" else None

    async def _gma(self, required=False):
        return heavy

    monkeypatch.setattr(OrchestratorInteractAction, "get_action", _ga)
    monkeypatch.setattr(OrchestratorInteractAction, "get_model_action", _gma)
    a, m, t, mt, r = await ex._gear_model("light")
    assert a is lite and m == "lite" and t == 0.1 and mt == 512 and r is False
    a2, m2, _, _, r2 = await ex._gear_model("heavy")
    assert a2 is heavy and r2 is True


async def test_run_model_threads_gear(monkeypatch):
    ex = OrchestratorInteractAction()
    ex.light_model = "lite"
    ex.reasoning_effort = "high"
    captured = {}
    model = MagicMock()

    async def _qm(**k):
        captured.clear()
        captured.update(k)
        return SimpleNamespace(response='{"action":"final"}')

    model.query_messages = _qm

    async def _ga(self, name):
        return model

    async def _gma(self, required=False):
        return model

    async def _agent(self):
        return SimpleNamespace(alias="", role="")

    monkeypatch.setattr(OrchestratorInteractAction, "get_action", _ga)
    monkeypatch.setattr(OrchestratorInteractAction, "get_model_action", _gma)
    monkeypatch.setattr(OrchestratorInteractAction, "get_agent", _agent)

    await ex._run_model(MagicMock(), "hi", [], [], [], gear="light")
    assert captured["model"] == "lite"
    assert "reasoning_effort" not in captured  # light gear → no reasoning

    await ex._run_model(MagicMock(), "hi", [], [], [], gear="heavy")
    assert captured["model"] == "gpt-4o-mini"
    assert captured.get("reasoning_effort") == "high"  # heavy gear → reasoning


async def test_run_model_history_is_structured_not_text(monkeypatch):
    """Conversation history rides in the structured messages/history channel —
    NOT dumped as text into the user turn."""
    ex = OrchestratorInteractAction()
    captured = {}
    model = MagicMock()

    async def _qm(**k):
        captured.clear()
        captured.update(k)
        return SimpleNamespace(response='{"action":"final"}')

    model.query_messages = _qm

    async def _gma(self, required=False):
        return model

    async def _agent(self):
        return SimpleNamespace(alias="", role="")

    monkeypatch.setattr(OrchestratorInteractAction, "get_model_action", _gma)
    monkeypatch.setattr(OrchestratorInteractAction, "get_agent", _agent)

    history = [
        {"role": "user", "content": "please tell me the time"},
        {"role": "assistant", "content": "The current time is 10:18."},
    ]
    await ex._run_model(MagicMock(), "Sign me up for training", history, [], [])

    messages = captured["messages"]
    assert messages[0]["role"] == "system"
    # history spliced in as real prior turns, before the current user message
    assert messages[1] == history[0]
    assert messages[2] == history[1]
    assert messages[-1]["role"] == "user"
    # the user turn carries the CURRENT message + steps, not the history text
    assert "Sign me up for training" in messages[-1]["content"]
    assert "please tell me the time" not in messages[-1]["content"]
    assert "Conversation so far" not in messages[-1]["content"]
    # peak-attention safeguards reminder rides in the user turn
    assert "OPERATING RULES" in messages[-1]["content"]
    # and history is also passed structurally (observability parity with respond)
    assert captured["history"] == history


async def test_gearing_escalates_across_loop(
    make_orchestrator, make_visitor, monkeypatch
):
    calls = {"n": 0}
    fake = _fake_capability_action("work", calls)
    ex = make_orchestrator(actions=[fake], decisions=[])
    ex.light_model = "lite"
    ex.escalate_after_tool_calls = 2
    ex.escalate_on_skill = False
    seq = [
        {"action": "tool", "tool": "work", "args": {"i": 1}},
        {"action": "tool", "tool": "work", "args": {"i": 2}},
        {"action": "tool", "tool": "work", "args": {"i": 3}},
        {"action": "final", "answer": "done"},
    ]
    gears = []

    async def _rm(
        self,
        visitor,
        utterance,
        history,
        tools,
        observations,
        flow_note="",
        skills_section="",
        finalize=False,
        gear="heavy",
        lean=False,
        plan_note="",
        **kwargs,
    ):
        gears.append(gear)
        return seq.pop(0) if seq else {"action": "final", "answer": ""}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _rm)
    await ex.execute(make_visitor(utterance="multi-step"))
    # light, light (count 0,1), then heavy once 2 substantive calls reached.
    assert gears[:4] == ["light", "light", "heavy", "heavy"]


async def test_light_model_no_main_falls_back_to_light(monkeypatch):
    """A light model with no main model → the light model becomes the sole model
    (fallback): gearing off, and every gear resolves to the light profile."""
    ex = OrchestratorInteractAction()
    ex.model = ""  # no main model
    ex.model_action_type = ""
    ex.light_model = "lite"
    ex.light_model_action_type = "OpenAILanguageModelAction"
    ex.light_model_temperature = 0.5
    ex.light_model_max_tokens = 777

    lite = object()

    async def _ra(self, action_type, *, profile="heavy"):
        return lite

    async def _gma(self, required=False):
        return object()  # should NOT be used — main model is empty

    monkeypatch.setattr(OrchestratorInteractAction, "_resolve_model_action", _ra)
    monkeypatch.setattr(OrchestratorInteractAction, "get_model_action", _gma)

    assert ex._gearing_on() is False  # only one effective tier
    assert ex._select_gear(5, True) == "heavy"  # no escalation distinction

    for gear in ("light", "heavy"):
        action, model_id, temp, mt, reasoning = await ex._gear_model(gear)
        assert action is lite
        assert model_id == "lite"
        assert temp == 0.5 and mt == 777
        assert reasoning is False  # light model is a completion model


async def test_no_light_model_runs_everything_heavy(
    make_orchestrator, make_visitor, monkeypatch
):
    """With no light_model configured, the main (heavy) model handles every tick
    — including the finalize call, which passes gear='light'."""
    calls = {"n": 0}
    fake = _fake_capability_action("work", calls)
    ex = make_orchestrator(actions=[fake], decisions=[])
    assert ex.light_model == ""  # gearing off
    seq = [
        {"action": "tool", "tool": "work", "args": {"i": 1}},
        {"action": "tool", "tool": "work", "args": {"i": 2}},
        {"action": "final", "answer": "done"},
    ]
    gears = []

    async def _rm(self, *a, gear="heavy", **k):
        gears.append(gear)
        return seq.pop(0) if seq else {"action": "final", "answer": ""}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _rm)
    await ex.execute(make_visitor(utterance="multi-step"))
    assert gears and all(g == "heavy" for g in gears)


async def test_progress_stream_fires_on_both_gears(
    make_orchestrator, make_visitor, monkeypatch
):
    """stream_internal_progress emits a reasoning thought on EVERY tick (both
    gears) so single-step light turns still populate the UI's REASONING
    disclosure (orchestrator-stream-emission-spec §B)."""
    thoughts = []

    async def _emit(self, visitor, text):
        thoughts.append(text)

    monkeypatch.setattr(OrchestratorInteractAction, "_emit_thought", _emit)

    calls = {"n": 0}
    fake = _fake_capability_action("work", calls)
    ex = make_orchestrator(actions=[fake], decisions=[])
    ex.stream_internal_progress = True
    ex.light_model = "lite"
    ex.escalate_after_tool_calls = 2
    ex.escalate_on_skill = False
    seq = [
        {"action": "tool", "tool": "work", "args": {"i": 1}},  # light tick
        {"action": "tool", "tool": "work", "args": {"i": 2}},  # light tick
        {"action": "tool", "tool": "work", "args": {"i": 3}},  # heavy tick
        {"action": "final", "answer": "done"},  # heavy tick
    ]

    async def _rm(self, *a, gear="heavy", **k):
        return seq.pop(0) if seq else {"action": "final", "answer": ""}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _rm)
    await ex.execute(make_visitor(utterance="multi-step"))
    # Every tick emits a progress/reasoning line now (no gear gate).
    assert len(thoughts) == 4


async def test_transient_ack_only_on_complex_turns(
    make_orchestrator, make_visitor, monkeypatch
):
    """The ack arms only once a turn is COMPLEX (multiple substantive tool calls,
    or a skill). Simple turns — including single-tool and reply-only on a
    single-model agent — never surface a 'working on it' line, so it can't trail
    after a fast reply."""
    sched = {"n": 0}

    def _sched(self, visitor):
        sched["n"] += 1
        return None

    monkeypatch.setattr(OrchestratorInteractAction, "_schedule_first_emit_ack", _sched)

    async def _run(decisions, *, single_model: bool):
        sched["n"] = 0
        fake = _fake_capability_action("work", {"n": 0})
        ex = make_orchestrator(actions=[fake], decisions=[])
        if not single_model:
            ex.light_model = "lite"  # gearing on
        ex.escalate_after_tool_calls = 2
        ex.escalate_on_skill = False
        seq = list(decisions)

        async def _rm(self, *a, gear="heavy", **k):
            return seq.pop(0) if seq else {"action": "final", "answer": ""}

        monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _rm)
        await ex.execute(make_visitor(utterance="x"))
        return sched["n"]

    # Single-model agent, simple single-tool turn → NO ack (the reported bug:
    # single-model used to arm on tick 1 and trail "One moment…" after the reply).
    n = await _run(
        [
            {"action": "tool", "tool": "work", "args": {"i": 1}},
            {"action": "final", "answer": "done"},
        ],
        single_model=True,
    )
    assert n == 0

    # Reply-only turn (no tools) → no ack.
    assert await _run([{"action": "final", "answer": "hi"}], single_model=True) == 0

    # Multi-tool turn crossing the threshold → armed exactly once.
    n = await _run(
        [
            {"action": "tool", "tool": "work", "args": {"i": 1}},
            {"action": "tool", "tool": "work", "args": {"i": 2}},
            {"action": "tool", "tool": "work", "args": {"i": 3}},
            {"action": "final", "answer": "done"},
        ],
        single_model=True,
    )
    assert n == 1


# --- Transient ack -------------------------------------------------------


async def test_transient_ack_disabled_by_default():
    # Off by default, and the master switch alone gates it.
    ex = OrchestratorInteractAction()
    assert ex.enable_transient_ack is False
    assert ex._schedule_first_emit_ack(MagicMock()) is None
    # Enabling without a bus still no-ops gracefully.
    ex.enable_transient_ack = True
    v = MagicMock()
    v.response_bus = None
    v.session_id = None
    assert ex._schedule_first_emit_ack(v) is None


async def test_transient_ack_emits_configured_statements():
    ex = OrchestratorInteractAction()
    ex.enable_transient_ack = True
    ex.first_emit_timeout_ms = 1  # 1ms before first
    ex.ack_interval_ms = 1  # 1ms between subsequent
    ex.ack_statements = ["Working on it…", "Almost there…"]
    bus = MagicMock()
    bus.publish = AsyncMock()
    v = MagicMock()
    v.response_bus = bus
    v.session_id = "sess"
    v.channel = "web"
    v.interaction = SimpleNamespace(id="i", user_id="u")
    task = ex._schedule_first_emit_ack(v)
    await task
    emitted = [c.kwargs["content"] for c in bus.publish.await_args_list]
    assert emitted == ["Working on it…", "Almost there…"]
    assert all(c.kwargs["transient"] for c in bus.publish.await_args_list)


async def test_transient_ack_first_delay_vs_interval(monkeypatch):
    """The first ack uses first_emit_timeout_ms; subsequent ones use the longer
    ack_interval_ms (so later lines don't show up too soon)."""
    import asyncio as _asyncio

    ex = OrchestratorInteractAction()
    ex.enable_transient_ack = True
    ex.first_emit_timeout_ms = 1500
    ex.ack_interval_ms = 8000
    ex.ack_statements = ["a", "b", "c"]
    sleeps = []
    real_sleep = _asyncio.sleep

    async def _sleep(d):
        sleeps.append(d)
        await real_sleep(0)

    monkeypatch.setattr(_asyncio, "sleep", _sleep)
    bus = MagicMock()
    bus.publish = AsyncMock()
    v = MagicMock()
    v.response_bus = bus
    v.session_id = "s"
    v.channel = "web"
    v.interaction = SimpleNamespace(id="i", user_id="u")
    await ex._schedule_first_emit_ack(v)
    assert sleeps == [1.5, 8.0, 8.0]  # first short, then the generous interval


# --- Phase 4: tooling / UX -------------------------------------------------


async def test_core_tools_tier_gating():
    ex = OrchestratorInteractAction()
    assert [t.name for t in build_core_tools(ex, "minimal")] == []
    assert "get_current_datetime" in [t.name for t in build_core_tools(ex, "standard")]
    assert "get_current_datetime" in [t.name for t in build_core_tools(ex, "full")]


async def test_block_raw_tool_policy_in_prompt_only_when_enabled(monkeypatch):
    captured = {}

    model = MagicMock()

    async def _qm(**kwargs):
        captured["system"] = kwargs["system"]
        return SimpleNamespace(response='{"action":"final"}')

    model.query_messages = _qm

    async def _gma(self, required=False):
        return model

    async def _agent(self):
        return SimpleNamespace(alias="", role="")

    monkeypatch.setattr(OrchestratorInteractAction, "get_model_action", _gma)
    monkeypatch.setattr(OrchestratorInteractAction, "get_agent", _agent)

    ex = OrchestratorInteractAction()
    await ex._run_model(MagicMock(), "hi", [], [], [])
    assert "TOOL-USE POLICY" not in captured["system"]  # off by default

    ex.block_raw_tool_invocation = True
    await ex._run_model(MagicMock(), "hi", [], [], [])
    assert "TOOL-USE POLICY" in captured["system"]
    assert "yours to select" in captured["system"]


async def test_block_raw_tool_invocation_gates_hidden(make_orchestrator, make_visitor):
    ex = make_orchestrator(
        decisions=[
            {"action": "tool", "tool": "hidden_tool", "args": {}},
            {"action": "final", "answer": "done"},
        ]
    )
    ex.block_raw_tool_invocation = True
    v = make_visitor()
    # hidden_tool is not in the visible surface → gated, loop continues to final.
    await ex.execute(v)
    assert v.interaction.response == "done"


async def test_user_named_tools_detection():
    f = OrchestratorInteractAction._user_named_tools
    names = {"web_search__search", "mcp_filesystem__write_file", "reply", "do_thing"}
    assert "do_thing" in f("please run do_thing", names)  # full name
    assert "mcp_filesystem__write_file" in f("use write_file now", names)  # mcp suffix
    assert "reply" not in f("reply to me", names)  # egress exempt
    assert f("hello there", names) == frozenset()  # no mention


def _fake_capability_action(name, calls):
    from jvagent.tooling.tool import Tool
    from jvagent.tooling.tool_result import ToolResult

    async def _run(**k):
        calls["n"] += 1
        return ToolResult(content="ran")

    class _FakeAction:
        def get_class_name(self):
            return "FakeAction"

        async def get_tools(self):
            return [
                Tool(
                    name=name,
                    description="Does a thing.",
                    parameters_schema={"type": "object", "properties": {}},
                    execute=_run,
                )
            ]

    return _FakeAction()


async def test_steering_guard_deflects_named_tool_once(make_orchestrator, make_visitor):
    calls = {"n": 0}
    ex = make_orchestrator(
        actions=[_fake_capability_action("do_thing", calls)],
        decisions=[
            {"action": "tool", "tool": "do_thing", "args": {}},
            {"action": "final", "answer": "handled"},
        ],
    )
    ex.block_raw_tool_invocation = True
    v = make_visitor(utterance="please run do_thing for me")
    await ex.execute(v)
    assert calls["n"] == 0  # the user-named tool was deflected, never dispatched
    assert v.interaction.response == "handled"


async def test_steering_guard_allows_after_one_deflection(
    make_orchestrator, make_visitor
):
    calls = {"n": 0}
    ex = make_orchestrator(
        actions=[_fake_capability_action("do_thing", calls)],
        decisions=[
            {"action": "tool", "tool": "do_thing", "args": {}},  # deflected
            {"action": "tool", "tool": "do_thing", "args": {}},  # now allowed
            {"action": "final", "answer": "ok"},
        ],
    )
    ex.block_raw_tool_invocation = True
    v = make_visitor(utterance="run do_thing")
    await ex.execute(v)
    assert calls["n"] == 1  # re-plan re-issued it → genuine choice, allowed once


async def test_steering_guard_off_when_flag_disabled(make_orchestrator, make_visitor):
    calls = {"n": 0}
    ex = make_orchestrator(
        actions=[_fake_capability_action("do_thing", calls)],
        decisions=[
            {"action": "tool", "tool": "do_thing", "args": {}},
            {"action": "final", "answer": "ok"},
        ],
    )
    # block_raw_tool_invocation defaults False → no guard, tool dispatches.
    v = make_visitor(utterance="run do_thing")
    await ex.execute(v)
    assert calls["n"] == 1


async def test_hidden_real_tool_named_directly_is_auto_promoted_and_run(
    make_orchestrator, make_visitor
):
    """A REAL tool that lean surfacing hid, named directly by the model (not by
    the user), must auto-promote and RUN — not dead-end on a find_tool demand."""
    calls = {"n": 0}
    ex = make_orchestrator(
        actions=[
            _fake_capability_action("alpha", calls),
            _fake_capability_action("beta", {"n": 0}),
        ],
        decisions=[
            {"action": "tool", "tool": "alpha", "args": {}},
            {"action": "final", "answer": "ok"},
        ],
    )
    ex.block_raw_tool_invocation = True
    ex.lean_tool_threshold = 1  # 2 capability tools > 1 → lean engages
    ex.lean_presurface_k = 0  # nothing pre-surfaced → both hidden
    # utterance shares no token with the tool names → not user-named, not surfaced
    v = make_visitor(utterance="please handle this for me")
    await ex.execute(v)
    assert calls["n"] == 1  # hidden-but-real tool ran instead of being bounced


async def test_unknown_tool_name_is_bounced_to_find_tool(
    make_orchestrator, make_visitor
):
    """An unknown/hallucinated tool name is NOT run — the loop continues and the
    observation points the model at find_tool."""
    ex = make_orchestrator(
        decisions=[
            {"action": "tool", "tool": "nonexistent_tool", "args": {}},
            {"action": "final", "answer": "done"},
        ]
    )
    ex.block_raw_tool_invocation = True
    v = make_visitor()
    await ex.execute(v)
    assert v.interaction.response == "done"  # bounced, then finalized cleanly


async def test_select_mcp_actions_empty_without_servers():
    assert OrchestratorInteractAction()._select_mcp_actions([]) == []


async def test_select_mcp_actions_all_and_finite(monkeypatch):
    import sys
    import types

    class _FakeMCP:
        def get_class_name(self):
            return "MCPAction"

    fake = _FakeMCP()

    # The helper imports MCPAction lazily; inject a fake module so isinstance
    # matches our stub.

    fake_module = types.ModuleType("jvagent.action.mcp.mcp_action")
    fake_module.MCPAction = _FakeMCP
    monkeypatch.setitem(sys.modules, "jvagent.action.mcp.mcp_action", fake_module)

    ex = OrchestratorInteractAction()
    ex.tool_servers = "-all"
    assert ex._select_mcp_actions([fake]) == [fake]
    ex.tool_servers = ["MCPAction"]
    assert ex._select_mcp_actions([fake]) == [fake]
    ex.tool_servers = ["other"]
    assert ex._select_mcp_actions([fake]) == []


async def test_mcp_filesystem_read_write_roundtrip(
    make_orchestrator, make_visitor, monkeypatch
):
    """End-to-end: an MCP filesystem gateway's write/read tools are surfaced to
    the executive, dispatched, and run with the per-user dispatch context bound
    (so real MCP servers route to the caller's sandbox)."""
    import sys
    import types

    from jvagent.tooling.tool import Tool
    from jvagent.tooling.tool_executor import get_dispatch_context
    from jvagent.tooling.tool_result import ToolResult

    class _MCPBase:
        pass

    fake_mod = types.ModuleType("jvagent.action.mcp.mcp_action")
    fake_mod.MCPAction = _MCPBase
    monkeypatch.setitem(sys.modules, "jvagent.action.mcp.mcp_action", fake_mod)

    store: dict = {}
    seen = {}

    class FakeFsMcp(_MCPBase):
        def get_class_name(self):
            return "FakeFsMcp"

        async def get_tools(self):
            # A real MCP tool forwards **kwargs to the server, so the executive
            # must NOT inject a visitor kwarg (it would be serialized and fail).
            async def _write(path="", content="", **k):
                seen["write_ctx"] = get_dispatch_context()
                seen["write_kwargs"] = dict(k)
                store[path] = content
                return ToolResult(content=f"wrote {path}")

            async def _read(path="", **k):
                seen["read"] = True
                store["read_kwargs"] = dict(k)
                return ToolResult(content=store.get(path, "(missing)"))

            schema = {"type": "object", "properties": {}}
            return [
                Tool(
                    name="mcp_filesystem__write_file",
                    description="Write a file in the sandbox.",
                    parameters_schema=schema,
                    execute=_write,
                ),
                Tool(
                    name="mcp_filesystem__read_file",
                    description="Read a file from the sandbox.",
                    parameters_schema=schema,
                    execute=_read,
                ),
            ]

    fake = FakeFsMcp()
    ex = make_orchestrator(
        actions=[fake],
        decisions=[
            {
                "action": "tool",
                "tool": "mcp_filesystem__write_file",
                "args": {"path": "notes.txt", "content": "hello sandbox"},
            },
            {
                "action": "tool",
                "tool": "mcp_filesystem__read_file",
                "args": {"path": "notes.txt"},
            },
            {"action": "final", "answer": "done"},
        ],
    )
    v = make_visitor(user_id="alice")
    await ex.execute(v)

    assert store["notes.txt"] == "hello sandbox"  # write routed through
    assert seen.get("read") is True  # read routed through
    # The visitor (which holds the non-serializable ResponseBus) must NOT be
    # forwarded to the MCP tool — only the model's own args.
    assert "visitor" not in seen.get("write_kwargs", {})
    # Per-user routing context was bound for the dispatch (real MCP uses it to
    # pick the caller's sandbox subprocess).
    ctx = seen.get("write_ctx")
    assert ctx is not None and ctx.user_id == "alice"


# --- Runtime knobs in execute() --------------------------------------------


async def test_tool_call_timeout_surfaces_observation(
    make_orchestrator, make_visitor, monkeypatch
):
    """Slow tools return a timeout observation and the loop continues."""
    import asyncio

    from jvagent.action.orchestrator.tools import SkillTool

    async def slow_run(_args):
        await asyncio.sleep(0.05)
        return "done"

    slow_tool = SkillTool(
        name="slow_tool",
        description="slow",
        run=slow_run,
    )

    ex = make_orchestrator(
        actions=[],
        decisions=[
            {"action": "tool", "tool": "slow_tool", "args": {}},
            {"action": "final", "answer": "ok"},
        ],
    )
    ex.tool_call_timeout = 0.01

    async def _assemble(
        self,
        visitor,
        activated,
        visible,
        flow_owner,
        utterance,
        skill_docs,
        surface_meta=None,
    ):
        return {"slow_tool": slow_tool}

    monkeypatch.setattr(OrchestratorInteractAction, "_assemble_tools", _assemble)

    v = make_visitor(utterance="run slow tool")
    await ex.execute(v)
    # Turn completes via final after timeout observation (no exception raised).


async def test_stream_internal_progress_emits_during_execute(
    make_orchestrator, make_visitor, monkeypatch
):
    """stream_internal_progress emits a thought bubble per tool tick."""
    from jvagent.action.orchestrator.tools import SkillTool

    async def noop_run(_args):
        return "ok"

    tool = SkillTool(
        name="demo_tool",
        description="demo",
        run=noop_run,
        terminal=True,
    )
    emitted = []

    async def _emit(self, visitor, text):
        emitted.append(text)

    monkeypatch.setattr(OrchestratorInteractAction, "_emit_thought", _emit)

    async def _assemble(
        self,
        visitor,
        activated,
        visible,
        flow_owner,
        utterance,
        skill_docs,
        surface_meta=None,
    ):
        return {"demo_tool": tool}

    monkeypatch.setattr(OrchestratorInteractAction, "_assemble_tools", _assemble)

    ex = make_orchestrator(
        actions=[],
        decisions=[{"action": "tool", "tool": "demo_tool", "args": {}}],
    )
    ex.stream_internal_progress = True
    v = make_visitor(utterance="go")
    await ex.execute(v)
    assert emitted  # at least one progress line for the tool tick


async def test_max_concurrent_tools_default_is_unbounded():
    ex = OrchestratorInteractAction()
    assert ex.max_concurrent_tools == 0
