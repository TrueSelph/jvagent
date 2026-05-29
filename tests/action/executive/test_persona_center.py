"""M3 — Persona center (sole egress) tests (ADR-0010 §2.4).

No real LM: ``PersonaAction`` interaction is mocked via the center's
``respond`` / ``publish``.
"""

from __future__ import annotations

import pytest

from jvagent.action.executive.centers.persona_center import PersonaCenter
from jvagent.action.executive.contracts import RESPOND

pytestmark = pytest.mark.asyncio


async def test_voice_verbatim_publishes_raw(monkeypatch):
    pub = []
    called = {"respond": False}

    async def _pub(self, *, visitor, content, **kw):
        pub.append(content)
        return None

    async def _resp(self, *a, **k):
        called["respond"] = True
        return "styled"

    monkeypatch.setattr(PersonaCenter, "publish", _pub)
    monkeypatch.setattr(PersonaCenter, "respond", _resp)

    pc = PersonaCenter()
    ok = await pc.voice(object(), content="literal text", verbatim=True)
    assert ok is True
    assert pub == ["literal text"]
    assert called["respond"] is False  # verbatim skips stylisation


async def test_voice_stylizes_via_persona(monkeypatch):
    pub = []
    seen = {}

    async def _pub(self, *, visitor, content, **kw):
        pub.append(content)
        return None

    async def _resp(self, visitor, directives=None, **k):
        seen["directives"] = directives
        return "STYLED"

    monkeypatch.setattr(PersonaCenter, "publish", _pub)
    monkeypatch.setattr(PersonaCenter, "respond", _resp)

    pc = PersonaCenter()
    ok = await pc.voice(object(), content="hello")
    assert ok is True
    assert pub == []  # published through persona, not a raw publish
    assert seen["directives"] == ["Tell the user: hello"]


async def test_voice_falls_back_when_no_persona(monkeypatch):
    pub = []

    async def _pub(self, *, visitor, content, **kw):
        pub.append(content)
        return None

    async def _resp(self, *a, **k):
        return None  # no PersonaAction installed

    monkeypatch.setattr(PersonaCenter, "publish", _pub)
    monkeypatch.setattr(PersonaCenter, "respond", _resp)

    pc = PersonaCenter()
    ok = await pc.voice(object(), content="hello")
    assert ok is True
    assert pub == ["hello"]  # raw fallback


async def test_voice_empty_is_noop():
    pc = PersonaCenter()
    assert await pc.voice(object(), content="   ") is False


async def test_executive_egress_routes_through_persona_center(
    make_executive, make_visitor, publish_log, monkeypatch
):
    voiced = []

    async def _voice(self, visitor, *, content, verbatim=False, meta=None):
        voiced.append(content)
        return True

    monkeypatch.setattr(PersonaCenter, "voice", _voice)

    pc = PersonaCenter()
    ex = make_executive(
        centers={"PersonaCenter": pc},
        executive_script=[RESPOND("hi there")],
    )
    ex.persona_center = "PersonaCenter"
    await ex.execute(make_visitor())

    assert voiced == ["hi there"]
    # The Executive itself did not raw-publish — the persona center owns egress.
    assert publish_log == []
