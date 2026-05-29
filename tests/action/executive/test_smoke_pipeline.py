"""M9 — end-to-end pipeline smoke (ADR-0010).

Drives the FULL real pipeline through ``ExecutiveInteractAction.execute`` with
every component live — deterministic reflex, real Executive cognition, real
Skills/IA centers, real Persona-center egress — and only the leaf model calls
mocked. This is the integration "smoke" (no live provider): it proves the
pieces compose, not model quality.
"""

from __future__ import annotations

import pytest

from jvagent.action.executive.centers.ia_center import IACenter
from jvagent.action.executive.centers.persona_center import PersonaCenter
from jvagent.action.executive.centers.skills_center import SkillsCenter, SkillTool
from jvagent.action.executive.contracts import WORKING_MEMORY_VISITOR_ATTR
from jvagent.action.executive.registry import Capability, CapabilityRegistry
from jvagent.action.manifest import Manifest

pytestmark = pytest.mark.asyncio


def _capture_persona_egress(monkeypatch):
    """Make the Persona center record what it voices (the egress sink)."""
    voiced = []

    async def _voice(self, visitor, *, content, verbatim=False, meta=None):
        if (content or "").strip():
            voiced.append(content)
            return True
        return False

    monkeypatch.setattr(PersonaCenter, "voice", _voice)
    return voiced


class _FakeIA:
    def __init__(self, *, publishes=None, turn_lock=False, locking=True):
        self.publishes = publishes
        self.turn_lock = turn_lock
        self.locking = locking
        self.ran = 0

    async def execute(self, visitor):
        self.ran += 1
        if self.publishes is not None:
            visitor.interaction.response = self.publishes

    def get_manifest(self):
        return Manifest(turn_lock=self.turn_lock)

    async def is_actively_locking_turn(self, visitor):
        return self.locking


async def test_smoke_trivial_chat(make_executive, make_visitor, monkeypatch):
    voiced = _capture_persona_egress(monkeypatch)
    ex = make_executive(
        centers={"PersonaCenter": PersonaCenter()},
        router_responses=['{"action":"respond","content":"Hey — how can I help?"}'],
    )
    ex.persona_center = "PersonaCenter"
    await ex.execute(make_visitor(utterance="hi"))
    assert voiced == ["Hey — how can I help?"]


async def test_smoke_skill_turn(make_executive, make_visitor, monkeypatch):
    voiced = _capture_persona_egress(monkeypatch)

    async def _calc(args):
        return "42"

    seq = [
        {"action": "tool", "tool": "calc", "args": {"a": 6, "b": 7}},
        {"action": "final", "answer": "It's 42."},
    ]

    async def _skill_call(self, ctx, task, tools, observations):
        ctx.use_model()
        return seq.pop(0)

    monkeypatch.setattr(SkillsCenter, "_call_skill_model", _skill_call)

    skills = SkillsCenter()
    skills.set_tools([SkillTool(name="calc", description="adds", run=_calc)])
    ex = make_executive(
        centers={"SkillsCenter": skills, "PersonaCenter": PersonaCenter()},
        router_responses=[
            '{"action":"activate","center":"SkillsCenter",'
            '"intent":"compute 6x7","on_done":"voice"}'
        ],
    )
    ex.persona_center = "PersonaCenter"
    await ex.execute(make_visitor(utterance="what is 6 times 7?"))
    assert voiced == ["It's 42."]


async def test_smoke_anchored_ia_via_reflex(make_executive, make_visitor, monkeypatch):
    voiced = _capture_persona_egress(monkeypatch)
    fake = _FakeIA(publishes="It's sunny.")

    async def _get_action(self, name):
        return fake if name == "WeatherIA" else None

    monkeypatch.setattr(IACenter, "get_action", _get_action)

    registry = CapabilityRegistry(
        [
            Capability(
                id="WeatherIA",
                kind="ia",
                center="IACenter",
                anchors=("weather",),
                handle="WeatherIA",
            )
        ]
    )
    ex = make_executive(
        centers={"IACenter": IACenter(), "PersonaCenter": PersonaCenter()},
        executive_script=[],  # must NOT be consulted — reflex short-circuits
        registry=registry,
    )
    ex.persona_center = "PersonaCenter"
    visitor = make_visitor(utterance="what's the weather?")
    visitor.interaction.directives = []
    visitor.interaction.response = ""
    await ex.execute(visitor)
    assert fake.ran == 1
    # The rails IA owns its own output channel; persona egress not used.
    assert voiced == []
    assert visitor.interaction.response == "It's sunny."


async def test_smoke_turn_lock_persist_then_resume(
    make_executive, make_visitor, monkeypatch
):
    _capture_persona_egress(monkeypatch)
    fake = _FakeIA(publishes="Question 1?", turn_lock=True, locking=True)

    async def _get_action(self, name):
        return fake if name == "InterviewIA" else None

    monkeypatch.setattr(IACenter, "get_action", _get_action)

    registry = CapabilityRegistry(
        [
            Capability(
                id="InterviewIA",
                kind="ia",
                center="IACenter",
                anchors=("start interview",),
                handle="InterviewIA",
            )
        ]
    )
    ex = make_executive(
        centers={"IACenter": IACenter(), "PersonaCenter": PersonaCenter()},
        executive_script=[],
        registry=registry,
    )
    ex.persona_center = "PersonaCenter"

    from jvagent.action.executive.sustained import SUSTAINED_TASK_TYPE
    from jvagent.memory.task_store import TaskStore

    def _active_sustained(conv):
        return [
            t
            for t in TaskStore(conv).list(status="active")
            if t.task_type == SUSTAINED_TASK_TYPE
        ]

    # Turn 1: anchor starts the interview; it turn-locks → sustained as a task.
    v1 = make_visitor(utterance="start interview")
    v1.interaction.directives = []
    v1.interaction.response = ""
    await ex.execute(v1)
    assert fake.ran == 1
    sustained = _active_sustained(v1.conversation)
    assert len(sustained) == 1 and sustained[0].data["center"] == "IACenter"
    # Capture the ledger AFTER the turn (TaskStore replaces the list on write).
    shared_tasks = v1.conversation.tasks

    # Turn 2: a fresh turn sharing the same task ledger resumes via the reflex —
    # no anchor needed in the utterance.
    v2 = make_visitor(utterance="my answer is blue")
    v2.interaction.directives = []
    v2.interaction.response = ""
    v2.conversation.tasks = shared_tasks  # share the durable task ledger
    # Releasing the lock this turn so it completes.
    fake.locking = False
    await ex.execute(v2)
    assert fake.ran == 2  # resumed without an anchor in the utterance
    assert _active_sustained(v2.conversation) == []  # cancelled on completion
