"""Tests for ``ReflexHelm`` JSON-classifier dispatch (BRIDGE-ROADMAP §E).

The classifier itself is exercised end-to-end via a mocked
``model_action`` that returns scripted JSON strings. Each test scripts
ONE response and asserts the resulting helm verb.

Coverage:

- Verb output per parse path (EMIT / SHIFT / DELEGATE / YIELD).
- SHIFT target validated against installed peer helms; falls back to
  ``default_shift_target`` when target unknown.
- DELEGATE target validated against installed peer actions; falls back
  to safe-default-shift when unknown.
- ``can_emit_directly=False`` forces SHIFT even on EMIT verbs.
- Unknown / malformed model output → safe-default-shift.
- Empty utterance short-circuits to YIELD (no LM call).
- Peer-discovery filters: only BaseHelm instances enumerated; rails
  IAs without declared manifest excluded.
- ``_parse_json_verb`` tolerates surrounding prose.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.helm.contracts import DELEGATE, EMIT, SHIFT, YIELD
from jvagent.action.helm.reflex.reflex_helm import ReflexHelm, _parse_json_verb

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_visitor(utterance: str = "hello") -> MagicMock:
    interaction = MagicMock()
    interaction.id = "int_1"
    interaction.utterance = utterance
    interaction.response = ""

    conversation = MagicMock()
    conversation.get_interaction_history = AsyncMock(return_value=[])

    visitor = MagicMock()
    visitor.utterance = utterance
    visitor.interaction = interaction
    visitor.conversation = conversation
    visitor.user_id = "u_test"
    visitor.channel = "default"
    visitor.session_id = "sess_test"
    return visitor


def _make_bridge_state() -> MagicMock:
    s = MagicMock()
    s.helm_states = {}
    return s


def _make_peer_helm(
    name: str, *, purpose: str, latency: str = "deliberate"
) -> MagicMock:
    """A BaseHelm-typed peer with a declared manifest."""
    from jvagent.action.helm.base import BaseHelm
    from jvagent.action.manifest import Manifest

    peer = MagicMock(spec=BaseHelm)
    peer.helm_name = MagicMock(return_value=name)
    peer.get_manifest = MagicMock(
        return_value=Manifest.from_payload(
            {"purpose": purpose, "latency_class": latency}
        )
    )
    peer.__class__ = type(name, (BaseHelm,), {})
    return peer


def _make_peer_ia(name: str, *, purpose: str) -> MagicMock:
    """A rails InteractAction-typed peer with a declared manifest."""
    from jvagent.action.interact.base import InteractAction
    from jvagent.action.manifest import Manifest

    ia = MagicMock(spec=InteractAction)
    ia.__class__ = type(name, (InteractAction,), {})
    ia.get_manifest = MagicMock(
        return_value=Manifest.from_payload({"purpose": purpose})
    )
    return ia


def _make_agent(*, helms=None, ias=None) -> MagicMock:
    """Build an agent whose actions manager enumerates the given peers."""
    actions_mgr = MagicMock()
    actions_mgr.get_all_actions = AsyncMock(
        return_value=list(helms or []) + list(ias or [])
    )
    agent = MagicMock()
    agent.get_actions_manager = AsyncMock(return_value=actions_mgr)
    return agent


def _patch_helm(
    monkeypatch,
    helm: ReflexHelm,
    *,
    agent: MagicMock,
    model_response: str,
) -> AsyncMock:
    """Patch helm.get_agent + helm.get_model_action so step() runs offline.

    Returns the ``query_messages`` AsyncMock so tests can assert the
    payload that was sent to the model.
    """

    async def _get_agent(self):
        return agent

    monkeypatch.setattr(ReflexHelm, "get_agent", _get_agent)

    model_action = MagicMock()
    result = MagicMock()
    result.response = model_response
    query = AsyncMock(return_value=result)
    model_action.query_messages = query

    async def _get_model_action(self, required=False, **kwargs):
        return model_action

    monkeypatch.setattr(ReflexHelm, "get_model_action", _get_model_action)
    return query


# ---------------------------------------------------------------------------
# JSON parse helper (async so pytest-asyncio auto-mode doesn't complain)
# ---------------------------------------------------------------------------


async def test_parse_json_verb_clean_object():
    obj = _parse_json_verb('{"verb": "EMIT", "text": "hi"}')
    assert obj == {"verb": "EMIT", "text": "hi"}


async def test_parse_json_verb_with_surrounding_prose():
    raw = 'Sure. Here is the verb:\n{"verb": "SHIFT", "target": "X"}\nThanks!'
    obj = _parse_json_verb(raw)
    assert obj == {"verb": "SHIFT", "target": "X"}


async def test_parse_json_verb_returns_none_on_garbage():
    assert _parse_json_verb("not json at all") is None
    assert _parse_json_verb("") is None
    assert _parse_json_verb("{broken json") is None


async def test_parse_json_verb_returns_none_on_non_object_root():
    assert _parse_json_verb('["array", "not", "object"]') is None


# ---------------------------------------------------------------------------
# step() verb dispatch
# ---------------------------------------------------------------------------


async def test_step_emit_when_classifier_picks_emit(monkeypatch):
    helm = ReflexHelm()
    visitor = _make_visitor("Hi")
    state = _make_bridge_state()
    agent = _make_agent()

    _patch_helm(
        monkeypatch,
        helm,
        agent=agent,
        model_response=json.dumps({"verb": "EMIT", "text": "Hey there!"}),
    )

    result = await helm.step(visitor, state)
    assert isinstance(result, EMIT)
    assert result.text == "Hey there!"
    assert result.finalize is True


async def test_step_shift_to_known_peer(monkeypatch):
    helm = ReflexHelm()
    visitor = _make_visitor("Tell me about Eldon Marks")
    state = _make_bridge_state()
    peer = _make_peer_helm("ReasoningHelm", purpose="Deep reasoning")
    agent = _make_agent(helms=[peer])

    _patch_helm(
        monkeypatch,
        helm,
        agent=agent,
        model_response=json.dumps(
            {
                "verb": "SHIFT",
                "target": "ReasoningHelm",
                "reason": "needs lookup",
                "transient_ack": "Looking that up…",
            }
        ),
    )

    result = await helm.step(visitor, state)
    assert isinstance(result, SHIFT)
    assert result.target == "ReasoningHelm"
    assert result.reason == "needs lookup"
    assert result.transient_ack == "Looking that up…"


async def test_step_shift_unknown_target_falls_back_to_default(monkeypatch):
    helm = ReflexHelm()
    visitor = _make_visitor("anything")
    state = _make_bridge_state()
    peer = _make_peer_helm("ReasoningHelm", purpose="Deep reasoning")
    agent = _make_agent(helms=[peer])

    _patch_helm(
        monkeypatch,
        helm,
        agent=agent,
        model_response=json.dumps(
            {"verb": "SHIFT", "target": "NoSuchHelm", "reason": "..."}
        ),
    )

    result = await helm.step(visitor, state)
    assert isinstance(result, SHIFT)
    assert result.target == "ReasoningHelm"  # default_shift_target


async def test_step_shift_unknown_target_and_no_default_emits_fallback(monkeypatch):
    helm = ReflexHelm()
    helm.default_shift_target = "NotInstalledHelm"
    helm.fallback_text = "Acknowledged."
    visitor = _make_visitor("anything")
    state = _make_bridge_state()
    agent = _make_agent(helms=[])

    _patch_helm(
        monkeypatch,
        helm,
        agent=agent,
        model_response=json.dumps(
            {"verb": "SHIFT", "target": "NoSuchHelm", "reason": "..."}
        ),
    )

    result = await helm.step(visitor, state)
    assert isinstance(result, EMIT)
    assert result.text == "Acknowledged."


async def test_step_delegate_to_known_ia(monkeypatch):
    helm = ReflexHelm()
    visitor = _make_visitor("I'd like to give feedback")
    state = _make_bridge_state()
    ia = _make_peer_ia("InterviewInteractAction", purpose="Conduct interviews")
    agent = _make_agent(ias=[ia])

    _patch_helm(
        monkeypatch,
        helm,
        agent=agent,
        model_response=json.dumps(
            {"verb": "DELEGATE", "interact_action": "InterviewInteractAction"}
        ),
    )

    result = await helm.step(visitor, state)
    assert isinstance(result, DELEGATE)
    assert result.interact_action == "InterviewInteractAction"


async def test_step_delegate_unknown_target_falls_back(monkeypatch):
    helm = ReflexHelm()
    visitor = _make_visitor("...")
    state = _make_bridge_state()
    peer = _make_peer_helm("ReasoningHelm", purpose="Deep reasoning")
    agent = _make_agent(helms=[peer])  # no IAs

    _patch_helm(
        monkeypatch,
        helm,
        agent=agent,
        model_response=json.dumps(
            {"verb": "DELEGATE", "interact_action": "MissingAction"}
        ),
    )

    result = await helm.step(visitor, state)
    assert isinstance(result, SHIFT)
    assert result.target == "ReasoningHelm"


async def test_step_yield_downgraded_to_shift_on_nonempty_utterance(monkeypatch):
    """A classifier YIELD on a non-empty utterance gets downgraded to
    SHIFT(default_shift_target) so the reasoning helm handles it.

    Without this, YIELD on "ok" would yield Bridge out of the turn
    entirely and the user would see no response (walker continues past
    Bridge but no other IA publishes). Defensive — the prompt forbids
    YIELD on non-empty input, but real classifiers occasionally ignore.
    """
    helm = ReflexHelm()
    visitor = _make_visitor("ok")
    state = _make_bridge_state()
    peer = _make_peer_helm("ReasoningHelm", purpose="Deep reasoning")
    agent = _make_agent(helms=[peer])

    _patch_helm(
        monkeypatch,
        helm,
        agent=agent,
        model_response=json.dumps({"verb": "YIELD", "reason": "ambiguous"}),
    )

    result = await helm.step(visitor, state)
    assert isinstance(result, SHIFT)
    assert result.target == "ReasoningHelm"


async def test_step_yield_preserved_on_empty_utterance(monkeypatch):
    """Empty utterance still yields — no point burning an LM call or shift."""
    helm = ReflexHelm()
    visitor = _make_visitor("   ")  # whitespace-only
    state = _make_bridge_state()
    agent = _make_agent()

    _patch_helm(monkeypatch, helm, agent=agent, model_response="")

    result = await helm.step(visitor, state)
    assert isinstance(result, YIELD)


async def test_step_unknown_verb_falls_back(monkeypatch):
    helm = ReflexHelm()
    visitor = _make_visitor("...")
    state = _make_bridge_state()
    peer = _make_peer_helm("ReasoningHelm", purpose="Deep reasoning")
    agent = _make_agent(helms=[peer])

    _patch_helm(
        monkeypatch,
        helm,
        agent=agent,
        model_response=json.dumps({"verb": "MEDITATE"}),
    )

    result = await helm.step(visitor, state)
    assert isinstance(result, SHIFT)
    assert result.target == "ReasoningHelm"


async def test_step_garbage_response_falls_back(monkeypatch):
    helm = ReflexHelm()
    visitor = _make_visitor("...")
    state = _make_bridge_state()
    peer = _make_peer_helm("ReasoningHelm", purpose="Deep reasoning")
    agent = _make_agent(helms=[peer])

    _patch_helm(
        monkeypatch,
        helm,
        agent=agent,
        model_response="this is not json at all",
    )

    result = await helm.step(visitor, state)
    assert isinstance(result, SHIFT)
    assert result.target == "ReasoningHelm"


# ---------------------------------------------------------------------------
# can_emit_directly enforcement
# ---------------------------------------------------------------------------


async def test_step_emit_blocked_when_cannot_emit_directly(monkeypatch):
    helm = ReflexHelm()
    helm.can_emit_directly = False
    visitor = _make_visitor("Hi")
    state = _make_bridge_state()
    peer = _make_peer_helm("ReasoningHelm", purpose="Deep reasoning")
    agent = _make_agent(helms=[peer])

    _patch_helm(
        monkeypatch,
        helm,
        agent=agent,
        model_response=json.dumps({"verb": "EMIT", "text": "Hey!"}),
    )

    result = await helm.step(visitor, state)
    assert isinstance(result, SHIFT)
    assert result.target == "ReasoningHelm"


async def test_step_emit_with_empty_text_falls_back(monkeypatch):
    helm = ReflexHelm()
    visitor = _make_visitor("?")
    state = _make_bridge_state()
    peer = _make_peer_helm("ReasoningHelm", purpose="Deep reasoning")
    agent = _make_agent(helms=[peer])

    _patch_helm(
        monkeypatch,
        helm,
        agent=agent,
        model_response=json.dumps({"verb": "EMIT", "text": ""}),
    )

    result = await helm.step(visitor, state)
    assert isinstance(result, SHIFT)


# ---------------------------------------------------------------------------
# Empty utterance short-circuit
# ---------------------------------------------------------------------------


async def test_step_empty_utterance_yields_without_lm_call(monkeypatch):
    helm = ReflexHelm()
    visitor = _make_visitor("")
    state = _make_bridge_state()

    # No model patch — verify the LM was never even reached.
    monkeypatch.setattr(ReflexHelm, "get_agent", AsyncMock(return_value=MagicMock()))
    failing_model = MagicMock()
    failing_model.query_messages = AsyncMock(
        side_effect=AssertionError("model should not be called on empty utterance")
    )

    async def _get_model(self, required=False, **kwargs):
        return failing_model

    monkeypatch.setattr(ReflexHelm, "get_model_action", _get_model)

    result = await helm.step(visitor, state)
    assert isinstance(result, YIELD)
    failing_model.query_messages.assert_not_called()


# ---------------------------------------------------------------------------
# Peer-discovery filtering
# ---------------------------------------------------------------------------


async def test_peer_actions_filter_excludes_missing_purpose(monkeypatch):
    helm = ReflexHelm()
    visitor = _make_visitor("test")
    state = _make_bridge_state()

    from jvagent.action.interact.base import InteractAction
    from jvagent.action.manifest import Manifest

    # IA with purpose — included
    good_ia = MagicMock(spec=InteractAction)
    good_ia.__class__ = type("GoodIA", (InteractAction,), {})
    good_ia.get_manifest = MagicMock(
        return_value=Manifest.from_payload({"purpose": "Do good things"})
    )

    # IA without purpose — excluded
    bare_ia = MagicMock(spec=InteractAction)
    bare_ia.__class__ = type("BareIA", (InteractAction,), {})
    bare_ia.get_manifest = MagicMock(return_value=Manifest.from_payload({}))

    agent = _make_agent(ias=[good_ia, bare_ia])

    captured: dict = {}

    async def _capture_query(messages, **kwargs):
        captured["system"] = messages[0]["content"]
        result = MagicMock()
        result.response = json.dumps({"verb": "EMIT", "text": "ok"})
        return result

    model_action = MagicMock()
    model_action.query_messages = AsyncMock(side_effect=_capture_query)

    async def _get_agent(self):
        return agent

    async def _get_model(self, required=False, **kwargs):
        return model_action

    monkeypatch.setattr(ReflexHelm, "get_agent", _get_agent)
    monkeypatch.setattr(ReflexHelm, "get_model_action", _get_model)

    await helm.step(visitor, state)

    assert "GoodIA" in captured["system"]
    assert "BareIA" not in captured["system"]


async def test_peer_helms_exclude_self(monkeypatch):
    helm = ReflexHelm()
    visitor = _make_visitor("test")
    state = _make_bridge_state()

    # The helm under test should never appear in its own peer list.
    self_peer = _make_peer_helm("ReflexHelm", purpose="Self")
    other = _make_peer_helm("ReasoningHelm", purpose="Other")
    agent = _make_agent(helms=[self_peer, other])

    captured: dict = {}

    async def _capture_query(messages, **kwargs):
        captured["system"] = messages[0]["content"]
        result = MagicMock()
        result.response = json.dumps({"verb": "SHIFT", "target": "ReasoningHelm"})
        return result

    model_action = MagicMock()
    model_action.query_messages = AsyncMock(side_effect=_capture_query)

    async def _get_agent(self):
        return agent

    async def _get_model(self, required=False, **kwargs):
        return model_action

    monkeypatch.setattr(ReflexHelm, "get_agent", _get_agent)
    monkeypatch.setattr(ReflexHelm, "get_model_action", _get_model)

    await helm.step(visitor, state)

    # "ReflexHelm" should NOT appear as a peer entry. Match the marker
    # line format from prompts.py:render_peer_helm_line.
    assert "- ReflexHelm:" not in captured["system"]
    assert "- ReasoningHelm:" in captured["system"]


# ---------------------------------------------------------------------------
# Model-action unavailable
# ---------------------------------------------------------------------------


async def test_step_no_model_action_falls_back(monkeypatch):
    helm = ReflexHelm()
    visitor = _make_visitor("test")
    state = _make_bridge_state()
    peer = _make_peer_helm("ReasoningHelm", purpose="Deep reasoning")
    agent = _make_agent(helms=[peer])

    async def _get_agent(self):
        return agent

    async def _get_model(self, required=False, **kwargs):
        return None

    monkeypatch.setattr(ReflexHelm, "get_agent", _get_agent)
    monkeypatch.setattr(ReflexHelm, "get_model_action", _get_model)

    result = await helm.step(visitor, state)
    assert isinstance(result, SHIFT)
    assert result.target == "ReasoningHelm"


async def test_step_model_raises_falls_back(monkeypatch):
    helm = ReflexHelm()
    visitor = _make_visitor("test")
    state = _make_bridge_state()
    peer = _make_peer_helm("ReasoningHelm", purpose="Deep reasoning")
    agent = _make_agent(helms=[peer])

    model_action = MagicMock()
    model_action.query_messages = AsyncMock(side_effect=RuntimeError("timeout"))

    async def _get_agent(self):
        return agent

    async def _get_model(self, required=False, **kwargs):
        return model_action

    monkeypatch.setattr(ReflexHelm, "get_agent", _get_agent)
    monkeypatch.setattr(ReflexHelm, "get_model_action", _get_model)

    result = await helm.step(visitor, state)
    assert isinstance(result, SHIFT)
    assert result.target == "ReasoningHelm"


# ---------------------------------------------------------------------------
# JSON mode (Wave 9i.2)
# ---------------------------------------------------------------------------


async def test_step_passes_response_format_when_json_mode_enabled(monkeypatch):
    """``enforce_json_mode=True`` (default) → ``response_format`` reaches the model."""
    helm = ReflexHelm()
    assert helm.enforce_json_mode is True  # contract: default-on

    visitor = _make_visitor("hi")
    state = _make_bridge_state()
    peer = _make_peer_helm("ReasoningHelm", purpose="Deep reasoning")
    agent = _make_agent(helms=[peer])

    captured: dict = {}

    async def _capture_query(**kwargs):
        captured.update(kwargs)
        result = MagicMock()
        result.response = json.dumps({"verb": "EMIT", "text": "hi"})
        return result

    model_action = MagicMock()
    model_action.query_messages = AsyncMock(side_effect=_capture_query)

    async def _get_agent(self):
        return agent

    async def _get_model(self, required=False, **kwargs):
        return model_action

    monkeypatch.setattr(ReflexHelm, "get_agent", _get_agent)
    monkeypatch.setattr(ReflexHelm, "get_model_action", _get_model)

    await helm.step(visitor, state)

    assert captured.get("response_format") == {"type": "json_object"}


async def test_step_omits_response_format_when_json_mode_disabled(monkeypatch):
    """``enforce_json_mode=False`` → no ``response_format`` kwarg sent."""
    helm = ReflexHelm()
    object.__setattr__(helm, "enforce_json_mode", False)

    visitor = _make_visitor("hi")
    state = _make_bridge_state()
    peer = _make_peer_helm("ReasoningHelm", purpose="Deep reasoning")
    agent = _make_agent(helms=[peer])

    captured: dict = {}

    async def _capture_query(**kwargs):
        captured.update(kwargs)
        result = MagicMock()
        result.response = json.dumps({"verb": "EMIT", "text": "hi"})
        return result

    model_action = MagicMock()
    model_action.query_messages = AsyncMock(side_effect=_capture_query)

    async def _get_agent(self):
        return agent

    async def _get_model(self, required=False, **kwargs):
        return model_action

    monkeypatch.setattr(ReflexHelm, "get_agent", _get_agent)
    monkeypatch.setattr(ReflexHelm, "get_model_action", _get_model)

    await helm.step(visitor, state)

    assert "response_format" not in captured
