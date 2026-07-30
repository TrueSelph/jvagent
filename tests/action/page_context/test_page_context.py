"""Tests for the host-page context action.

The renderer is the interesting part: it must stay purely factual (no advice,
no next step) or it becomes turn-prep steering, which the thin-harness contract
forbids.
"""

import pytest

from jvagent.action.page_context.page_context_interact_action import (
    PageContextInteractAction,
    format_page_context,
)


def test_renders_title_and_path():
    line = format_page_context({"title": "Pricing", "path": "/pricing"})
    assert line == 'The visitor is currently on the page "Pricing" (/pricing).'


def test_path_only_still_renders():
    assert format_page_context({"path": "/docs"}) == (
        "The visitor is currently on the page /docs."
    )


def test_returns_none_without_a_page():
    assert format_page_context({}) is None
    assert format_page_context({"secondsOnPage": 90}) is None
    assert format_page_context(None) is None
    assert format_page_context("nonsense") is None


def test_includes_meaningful_behaviour_only():
    # Short dwell and shallow scroll are noise — omitted.
    line = format_page_context(
        {"title": "T", "path": "/p", "secondsOnPage": 5, "scrollDepth": 10}
    )
    assert "been there" not in line
    assert "scrolled" not in line


def test_includes_dwell_scroll_returning_and_referrer():
    line = format_page_context(
        {
            "title": "Pricing",
            "path": "/pricing",
            "secondsOnPage": 95,
            "scrollDepth": 80,
            "returning": True,
            "visitCount": 3,
            "referrer": "https://google.com",
        }
    )
    assert "about 95s" in line
    assert "~80%" in line
    assert "visit 3" in line
    assert "google.com" in line


def test_states_facts_without_advising():
    """Guard the thin-harness boundary: context, never a recommendation."""
    line = format_page_context(
        {"title": "Pricing", "path": "/pricing", "secondsOnPage": 120}
    )
    lowered = line.lower()
    for banned in ("should", "offer", "suggest", "ask them", "recommend", "demo"):
        assert banned not in lowered


# ── execute ────────────────────────────────────────────────────────────────


class _Interaction:
    pass


class _Visitor:
    def __init__(self, data):
        self.data = data
        self.interaction = _Interaction()
        self.parameters = []
        self.unrecorded = False

    async def add_parameter(self, param):
        self.parameters.append(param)

    async def unrecord_action_execution(self):
        self.unrecorded = True


def _action():
    return PageContextInteractAction.model_construct()


@pytest.mark.asyncio
async def test_execute_adds_a_context_parameter():
    action = _action()
    visitor = _Visitor({"page_context": {"title": "Pricing", "path": "/pricing"}})
    await action.execute(visitor)
    assert len(visitor.parameters) == 1
    param = visitor.parameters[0]
    # Parameters are conditional response rules — an unconditional blob reads as
    # a style note and is ignored (render_parameters emits "When <cond>: <rule>").
    assert set(param) == {"scope", "condition", "response"}
    # Orchestration scope: response-scoped params never reach the model
    # while it reasons, and the literal `reply` path skips compose.
    assert param["scope"] == "orchestration"
    assert param["condition"]
    assert param["response"] == (
        'The visitor is currently on the page "Pricing" (/pricing).'
    )


@pytest.mark.asyncio
async def test_execute_noops_without_page_context():
    action = _action()
    visitor = _Visitor({})
    await action.execute(visitor)
    assert visitor.parameters == []
    assert visitor.unrecorded is True


@pytest.mark.asyncio
async def test_execute_noops_on_unusable_context():
    action = _action()
    visitor = _Visitor({"page_context": {"secondsOnPage": 12}})
    await action.execute(visitor)
    assert visitor.parameters == []
