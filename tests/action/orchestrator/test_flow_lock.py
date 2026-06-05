"""lock_active_flow (ADR-0013): when on, an active flow control-task restricts
the loop's callable surface to the owning IA's tool, which is dispatched
immediately (mechanistic turn-lock — no model round-trip, even for an off-topic
utterance). When off, continuation is model-mediated through the loop.
``active_flow_owner`` is stubbed and ``_run_model`` is spied so each test asserts
the routing decision without a live TaskStore or model."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import jvagent.action.orchestrator.orchestrator_interact_action as sei
from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)

pytestmark = pytest.mark.asyncio


def _capture_visitor(make_visitor, **kw):
    """A visitor whose interaction collects observability_metrics in a real list."""
    v = make_visitor(**kw)
    v.interaction.observability_metrics = []
    v.interaction.save = AsyncMock()
    return v


def _activation(v):
    return next(
        (
            e
            for e in v.interaction.observability_metrics
            if e.get("event_type") == "orchestrator_activation"
        ),
        None,
    )


def _signup(flow_stub_cls, on_exec=None):
    class SignupIA(flow_stub_cls):
        anchors = ["sign up for training"]
        description = "Signup interview."

        async def execute(self, visitor):
            if on_exec:
                on_exec(visitor)

    return SignupIA()


def _spy_model(monkeypatch):
    """Count model round-trips; each returns a no-op 'final' decision."""
    calls = {"n": 0}

    async def _m(
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
        calls["n"] += 1
        return {"action": "final", "answer": ""}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _m)
    return calls


async def test_lock_on_restricts_surface_to_owning_ia(
    make_orchestrator, make_visitor, flow_stub_cls, monkeypatch
):
    ran = {"n": 0}
    ia = _signup(flow_stub_cls, on_exec=lambda v: ran.__setitem__("n", ran["n"] + 1))
    ex = make_orchestrator(actions=[ia], action_registry={"SignupIA": ia})
    assert ex.lock_active_flow is True  # default

    monkeypatch.setattr(sei, "active_flow_owner", lambda v, **kw: "SignupIA")
    calls = _spy_model(monkeypatch)

    # Off-topic utterance mid-flow: the surface is restricted to the IA's tool,
    # which is dispatched directly — no model round-trip.
    await ex.execute(make_visitor(utterance="Who is Eldon Marks?"))

    assert ran["n"] == 1  # owning IA's tool dispatched (forwarded to execute)
    assert calls["n"] == 0  # restricted surface → loop never calls the model


async def test_lock_off_is_model_mediated(
    make_orchestrator, make_visitor, flow_stub_cls, monkeypatch
):
    ran = {"n": 0}
    ia = _signup(flow_stub_cls, on_exec=lambda v: ran.__setitem__("n", ran["n"] + 1))
    ex = make_orchestrator(actions=[ia], action_registry={"SignupIA": ia})
    ex.lock_active_flow = False

    monkeypatch.setattr(sei, "active_flow_owner", lambda v, **kw: "SignupIA")
    calls = _spy_model(monkeypatch)

    await ex.execute(make_visitor(utterance="Who is Eldon Marks?"))

    assert ran["n"] == 0  # IA not auto-dispatched
    assert calls["n"] >= 1  # continuation is model-mediated via the loop


async def test_lock_on_no_active_task_runs_loop(
    make_orchestrator, make_visitor, flow_stub_cls, monkeypatch
):
    ran = {"n": 0}
    ia = _signup(flow_stub_cls, on_exec=lambda v: ran.__setitem__("n", ran["n"] + 1))
    ex = make_orchestrator(actions=[ia], action_registry={"SignupIA": ia})

    monkeypatch.setattr(sei, "active_flow_owner", lambda v, **kw: None)
    calls = _spy_model(monkeypatch)

    await ex.execute(make_visitor(utterance="Hello there"))

    assert ran["n"] == 0  # nothing to lock onto
    assert calls["n"] >= 1  # normal loop runs the model


async def test_ia_emitted_detects_response_or_queued_directive():
    """The locked path treats a directive-publishing IA as having emitted, so it
    won't echo the IA-as-tool status sentinel."""
    from types import SimpleNamespace

    A = OrchestratorInteractAction
    assert A._ia_emitted(None) is False
    assert (
        A._ia_emitted(
            SimpleNamespace(response="hi there", get_unexecuted_directives=lambda: [])
        )
        is True
    )
    # Published via a queued directive (the interview pattern), response still "".
    assert (
        A._ia_emitted(
            SimpleNamespace(
                response="", get_unexecuted_directives=lambda: [{"directive": "Name?"}]
            )
        )
        is True
    )
    # Truly silent: no response, no directives.
    assert (
        A._ia_emitted(
            SimpleNamespace(response="", get_unexecuted_directives=lambda: [])
        )
        is False
    )


async def test_locked_directive_publish_never_echoes_sentinel(
    make_orchestrator, make_visitor, flow_stub_cls, monkeypatch
):
    """Regression: a locked IA that publishes via add_directive must NOT make the
    orchestrator echo the IA-as-tool status sentinel '(ran <Class>)' as a reply
    (the old voiced-check looked only at interaction.response and missed the
    directive publish path)."""
    ia = _signup(flow_stub_cls)
    ex = make_orchestrator(actions=[ia], action_registry={"SignupIA": ia})
    monkeypatch.setattr(sei, "active_flow_owner", lambda v, **kw: "SignupIA")
    _spy_model(monkeypatch)

    emitted: list = []

    async def _cap(_self, _v, text):
        emitted.append(text)

    monkeypatch.setattr(OrchestratorInteractAction, "_emit_reply", _cap)

    v = make_visitor(utterance="x")
    v.interaction.response = ""
    v.interaction.get_unexecuted_directives = lambda: [
        {"directive": "What's your name?"}
    ]

    await ex.execute(v)

    # The internal status sentinel must never reach the user.
    assert all("(ran" not in t for t in emitted), emitted
    assert all("SignupIA" not in t for t in emitted), emitted


async def test_locked_silent_ia_emits_clarify_not_sentinel(
    make_orchestrator, make_visitor, flow_stub_cls, monkeypatch
):
    """When a locked IA produces nothing (no response, no directive), the
    orchestrator surfaces the clean clarify fallback — never the '(ran X)'
    sentinel."""
    ia = _signup(flow_stub_cls)
    ex = make_orchestrator(actions=[ia], action_registry={"SignupIA": ia})
    monkeypatch.setattr(sei, "active_flow_owner", lambda v, **kw: "SignupIA")
    _spy_model(monkeypatch)

    emitted: list = []

    async def _cap(_self, _v, text):
        emitted.append(text)

    monkeypatch.setattr(OrchestratorInteractAction, "_emit_reply", _cap)

    v = make_visitor(utterance="x")
    v.interaction.response = ""
    v.interaction.get_unexecuted_directives = lambda: []

    await ex.execute(v)

    assert all("(ran" not in t for t in emitted), emitted
    assert ex.clarify_text in emitted


async def test_orchestrator_activation_event_recorded_per_mode(
    make_orchestrator, make_visitor, flow_stub_cls, monkeypatch
):
    ia = _signup(flow_stub_cls)

    # locked: surface restricted to the IA tool
    ex = make_orchestrator(actions=[ia], action_registry={"SignupIA": ia})
    monkeypatch.setattr(sei, "active_flow_owner", lambda v, **kw: "SignupIA")
    _spy_model(monkeypatch)
    v = _capture_visitor(make_visitor, utterance="x")
    await ex.execute(v)
    ev = _activation(v)
    assert ev is not None
    assert ev["data"]["continuation_mode"] == "locked"
    assert ev["data"]["flow_owner"] == "SignupIA"
    assert ev["data"]["ended_via"] == "locked"
    assert ev["data"]["tools_invoked"] == ["SignupIA"]

    # model-mediated: flow active but lock off
    ex2 = make_orchestrator(actions=[ia], action_registry={"SignupIA": ia})
    ex2.lock_active_flow = False
    _spy_model(monkeypatch)
    v2 = _capture_visitor(make_visitor, utterance="x")
    await ex2.execute(v2)
    ev2 = _activation(v2)
    assert ev2 is not None and ev2["data"]["continuation_mode"] == "model_mediated"

    # none: no active flow
    ex3 = make_orchestrator(actions=[ia], action_registry={"SignupIA": ia})
    monkeypatch.setattr(sei, "active_flow_owner", lambda v, **kw: None)
    _spy_model(monkeypatch)
    v3 = _capture_visitor(make_visitor, utterance="x")
    await ex3.execute(v3)
    ev3 = _activation(v3)
    assert ev3 is not None and ev3["data"]["continuation_mode"] == "none"


async def test_skill_turn_lock_restricts_surface_when_locked_in_is_true(
    make_orchestrator, make_visitor, monkeypatch
):
    from jvagent.action.orchestrator.skills import SkillDoc
    from jvagent.tooling.tool import Tool

    # Define a skill with locked_in = True
    skill = SkillDoc(
        name="ResearchSkill",
        description="Search and summarize.",
        body="SOP: Search using web search, then reply.",
        requires_tools=("web_search__search",),
        locked_in=True,
    )

    # We need a mock tool for web_search__search so get_tools() returns it
    class SearchIA:
        async def get_tools(self):
            return [
                Tool(
                    name="web_search__search",
                    description="Search the web.",
                    parameters_schema={"type": "object", "properties": {}},
                    execute=lambda *args, **kw: None
                )
            ]

    # Mock ReplyIA since reply is required
    class ReplyIA:
        async def get_tools(self):
            return [
                Tool(
                    name="reply",
                    description="Reply to the user.",
                    parameters_schema={"type": "object", "properties": {}},
                    execute=lambda *args, **kw: None
                )
            ]

    search_ia = SearchIA()
    reply_ia = ReplyIA()
    ex = make_orchestrator(actions=[search_ia, reply_ia], action_registry={"SearchIA": search_ia, "ReplyIA": reply_ia})

    # Mock skill discovery to return our skill
    monkeypatch.setattr(OrchestratorInteractAction, "_discover_skills", lambda self, agent: [skill])
    # Mock active_flow_owner to return None since there is no active IA flow
    monkeypatch.setattr(sei, "active_flow_owner", lambda v, **kw: None)

    # Mock TaskStore to return an active task for ResearchSkill
    class MockTask:
        def __init__(self, owner_action):
            self.owner_action = owner_action
            self.task_type = "INTERVIEW"
            self.updated_at = "2026-06-04T10:00:00Z"

    class MockTaskStore:
        def __init__(self, conversation):
            pass
        def list(self, status="active"):
            return [MockTask("ResearchSkill")]

    monkeypatch.setattr("jvagent.memory.task_store.TaskStore", MockTaskStore)

    # Spy on run_model to check the arguments it gets
    spied_calls = []
    async def _m(
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
        spied_calls.append({
            "tools": [t.name for t in tools],
            "skills_section": skills_section,
        })
        return {"action": "final", "answer": "done"}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _m)

    v = _capture_visitor(make_visitor, utterance="some user query")
    await ex.execute(v)

    # Verify model was called once and with restricted tools
    assert len(spied_calls) == 1
    # Only allowed skill tools + reply/respond should be visible/callable
    assert set(spied_calls[0]["tools"]) == {"web_search__search", "reply"}
    # Skills section should contain the active skill SOP/procedure
    assert "ACTIVE SKILL IN PROGRESS: ResearchSkill" in spied_calls[0]["skills_section"]
    assert "SOP: Search using web search" in spied_calls[0]["skills_section"]

    # Verify metrics logged continuation_mode="locked" and flow_owner="ResearchSkill"
    ev = _activation(v)
    assert ev is not None
    assert ev["data"]["continuation_mode"] == "locked"
    assert ev["data"]["flow_owner"] == "ResearchSkill"
    assert "ResearchSkill" in ev["data"]["skills_used"]


async def test_skill_lock_not_locked_when_locked_in_is_false(
    make_orchestrator, make_visitor, monkeypatch
):
    from jvagent.action.orchestrator.skills import SkillDoc
    from jvagent.tooling.tool import Tool

    # Define a skill with locked_in = False
    skill = SkillDoc(
        name="ResearchSkill",
        description="Search and summarize.",
        body="SOP: Search using web search, then reply.",
        requires_tools=("web_search__search",),
        locked_in=False,
    )

    class SearchIA:
        async def get_tools(self):
            return [
                Tool(
                    name="web_search__search",
                    description="Search the web.",
                    parameters_schema={"type": "object", "properties": {}},
                    execute=lambda *args, **kw: None
                )
            ]

    class ReplyIA:
        async def get_tools(self):
            return [
                Tool(
                    name="reply",
                    description="Reply to the user.",
                    parameters_schema={"type": "object", "properties": {}},
                    execute=lambda *args, **kw: None
                )
            ]

    search_ia = SearchIA()
    reply_ia = ReplyIA()
    ex = make_orchestrator(actions=[search_ia, reply_ia], action_registry={"SearchIA": search_ia, "ReplyIA": reply_ia})

    monkeypatch.setattr(OrchestratorInteractAction, "_discover_skills", lambda self, agent: [skill])
    # Mock active_flow_owner to return None since there is no active IA flow
    monkeypatch.setattr(sei, "active_flow_owner", lambda v, **kw: None)

    class MockTask:
        def __init__(self, owner_action):
            self.owner_action = owner_action
            self.task_type = "INTERVIEW"
            self.updated_at = "2026-06-04T10:00:00Z"

    class MockTaskStore:
        def __init__(self, conversation):
            pass
        def list(self, status="active"):
            return [MockTask("ResearchSkill")]

    monkeypatch.setattr("jvagent.memory.task_store.TaskStore", MockTaskStore)

    spied_calls = []
    async def _m(
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
        spied_calls.append({
            "tools": [t.name for t in tools],
            "skills_section": skills_section,
        })
        return {"action": "final", "answer": "done"}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _m)

    v = _capture_visitor(make_visitor, utterance="some user query")
    await ex.execute(v)

    assert len(spied_calls) == 1
    # Since locked_in is False, we should see all tools (including find_skill, find_tool, etc.)
    assert len(spied_calls[0]["tools"]) > 2
    assert "find_skill" in spied_calls[0]["tools"]
    # Skills section should NOT contain the procedure but rather the standard description list
    assert "ACTIVE SKILL IN PROGRESS" not in spied_calls[0]["skills_section"]
    assert "- ResearchSkill: Search and summarize." in spied_calls[0]["skills_section"]

    ev = _activation(v)
    assert ev is not None
    assert ev["data"]["continuation_mode"] == "none"


def _interview_tool_action(calls: dict):
    from jvagent.tooling.tool import Tool
    from jvagent.tooling.tool_result import ToolResult

    async def _validate(**kwargs):
        calls["n"] += 1
        return ToolResult(content='{"valid": true}')

    class InterviewToolsAction:
        def get_class_name(self):
            return "InterviewToolsAction"

        async def get_tools(self):
            return [
                Tool(
                    name="interview__validate_phone",
                    description="Validate phone numbers for onboarding.",
                    parameters_schema={
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                    },
                    execute=_validate,
                )
            ]

    return InterviewToolsAction()


def _filler_tools_action(count: int = 20):
    from jvagent.tooling.tool import Tool
    from jvagent.tooling.tool_result import ToolResult

    class FillerAction:
        def get_class_name(self):
            return "FillerAction"

        async def get_tools(self):
            tools = []
            for i in range(count):

                def _make_run(idx: int):
                    async def _run(**kwargs):
                        return ToolResult(content=f"ok{idx}")

                    return _run

                tools.append(
                    Tool(
                        name=f"filler__tool_{i:02d}",
                        description=f"Unrelated capability {i}.",
                        parameters_schema={"type": "object", "properties": {}},
                        execute=_make_run(i),
                    )
                )
            return tools

    return FillerAction()


def _reply_action():
    from jvagent.tooling.tool import Tool

    class ReplyIA:
        async def get_tools(self):
            return [
                Tool(
                    name="reply",
                    description="Reply to the user.",
                    parameters_schema={"type": "object", "properties": {}},
                    execute=lambda *args, **kw: None,
                )
            ]

    return ReplyIA()


async def test_locked_in_pins_visible_so_blocked_tools_dispatch(
    make_orchestrator, make_visitor, monkeypatch
):
    """locked_in must pin allowed-tools into visible; lean + block_raw must not stub."""
    from jvagent.action.orchestrator.skills import SkillDoc

    calls = {"n": 0}
    skill = SkillDoc(
        name="OnboardingSkill",
        description="Customer onboarding interview.",
        body="SOP: validate phone with interview__validate_phone.",
        requires_tools=("interview__validate_phone",),
        locked_in=True,
    )
    interview_ia = _interview_tool_action(calls)
    filler_ia = _filler_tools_action(20)
    reply_ia = _reply_action()
    ex = make_orchestrator(
        actions=[interview_ia, filler_ia, reply_ia],
        action_registry={
            "InterviewToolsAction": interview_ia,
            "FillerAction": filler_ia,
            "ReplyIA": reply_ia,
        },
        decisions=[
            {
                "action": "tool",
                "tool": "interview__validate_phone",
                "args": {"value": "5926431531"},
            },
            {"action": "final", "answer": "done"},
        ],
    )
    ex.block_raw_tool_invocation = True
    ex.lean_tool_threshold = 1
    ex.lean_presurface_k = 2
    ex.lock_active_flow = True

    monkeypatch.setattr(
        OrchestratorInteractAction, "_discover_skills", lambda self, agent: [skill]
    )
    monkeypatch.setattr(sei, "active_flow_owner", lambda v, **kw: None)

    class MockTask:
        def __init__(self, owner_action, data=None):
            self.owner_action = owner_action
            self.task_type = "INTERVIEW"
            self.data = data or {}
            self.updated_at = "2026-06-04T10:00:00Z"

    class MockTaskStore:
        def __init__(self, conversation):
            pass

        def list(self, status="active"):
            return [MockTask("OnboardingSkill")]

    monkeypatch.setattr("jvagent.memory.task_store.TaskStore", MockTaskStore)

    v = _capture_visitor(make_visitor, utterance="5926431531")
    await ex.execute(v)

    assert calls["n"] == 1
    ev = _activation(v)
    assert ev is not None
    assert ev["data"]["continuation_mode"] == "locked"
    assert ev["data"]["flow_owner"] == "OnboardingSkill"


async def test_locked_in_via_interview_action_task_owner(
    make_orchestrator, make_visitor, monkeypatch
):
    """InterviewAction tasks with interview_type adopt the matching locked_in skill."""
    from jvagent.action.orchestrator.skills import SkillDoc

    calls = {"n": 0}
    skill = SkillDoc(
        name="OnboardingSkill",
        description="Customer onboarding interview.",
        body="SOP: validate phone.",
        requires_tools=("interview__validate_phone",),
        locked_in=True,
    )
    interview_ia = _interview_tool_action(calls)
    filler_ia = _filler_tools_action(20)
    reply_ia = _reply_action()
    ex = make_orchestrator(
        actions=[interview_ia, filler_ia, reply_ia],
        action_registry={
            "InterviewToolsAction": interview_ia,
            "FillerAction": filler_ia,
            "ReplyIA": reply_ia,
        },
        decisions=[
            {
                "action": "tool",
                "tool": "interview__validate_phone",
                "args": {"value": "5926431531"},
            },
            {"action": "final", "answer": "done"},
        ],
    )
    ex.block_raw_tool_invocation = True
    ex.lean_tool_threshold = 1
    ex.lock_active_flow = True

    monkeypatch.setattr(
        OrchestratorInteractAction, "_discover_skills", lambda self, agent: [skill]
    )
    monkeypatch.setattr(sei, "active_flow_owner", lambda v, **kw: None)

    class MockTask:
        def __init__(self, owner_action, data=None):
            self.owner_action = owner_action
            self.task_type = "INTERVIEW"
            self.data = data or {}
            self.updated_at = "2026-06-04T10:00:00Z"

    class MockTaskStore:
        def __init__(self, conversation):
            pass

        def list(self, status="active"):
            return [
                MockTask(
                    "InterviewAction",
                    data={"interview_type": "OnboardingSkill", "state": "active"},
                )
            ]

    monkeypatch.setattr("jvagent.memory.task_store.TaskStore", MockTaskStore)

    v = _capture_visitor(make_visitor, utterance="5926431531")
    await ex.execute(v)

    assert calls["n"] == 1
    ev = _activation(v)
    assert ev is not None
    assert ev["data"]["continuation_mode"] == "locked"
    assert ev["data"]["flow_owner"] == "OnboardingSkill"


def test_find_active_locked_skill_doc_prefers_active_session(monkeypatch):
    """Active interview session wins over stale tasks from another skill."""
    from jvagent.action.orchestrator.skills import SkillDoc

    onboarding = SkillDoc(
        name="onboarding_interview",
        description="Onboarding",
        body="SOP",
        requires_tools=("interview__init",),
        locked_in=True,
    )
    pre_alert = SkillDoc(
        name="pre_alert_interview",
        description="Pre-alert",
        body="SOP",
        requires_tools=("interview__init",),
        locked_in=True,
    )

    class MockTask:
        def __init__(self, owner_action, data=None, updated_at="2026-06-04T12:00:00Z"):
            self.owner_action = owner_action
            self.data = data or {}
            self.updated_at = updated_at

    class MockTaskStore:
        def __init__(self, conversation):
            pass

        def list(self, status="active"):
            return [
                MockTask(
                    "onboarding_interview",
                    updated_at="2026-06-04T12:00:00Z",
                ),
                MockTask(
                    "InterviewAction",
                    data={"interview_type": "onboarding_interview"},
                    updated_at="2026-06-04T11:00:00Z",
                ),
            ]

    monkeypatch.setattr("jvagent.memory.task_store.TaskStore", MockTaskStore)

    visitor = type(
        "V",
        (),
        {
            "conversation": type(
                "C",
                (),
                {
                    "context": {
                        "interview": {
                            "interview_type": "pre_alert_interview",
                            "status": "active",
                        }
                    }
                },
            )()
        },
    )()

    ex = OrchestratorInteractAction()
    ex.lock_active_flow = True
    doc = ex._find_active_locked_skill_doc(visitor, [onboarding, pre_alert])
    assert doc is not None
    assert doc.name == "pre_alert_interview"

