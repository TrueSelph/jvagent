"""Tests for the agent-driven UI component emitter.

The load-bearing guarantees: a component never breaks a turn, never reaches a
non-streaming channel as a blank message, and never trips ADR-0024's egress
latch (which is why the flush publishes empty content).
"""

import pytest

from jvagent.action.ui.ui_action import UI_CATALOG, UiAction, build_envelope


# ── build_envelope ─────────────────────────────────────────────────────────


def test_builds_a_valid_envelope():
    env = build_envelope("card", {"title": "T"}, "T — plain text")
    assert env["v"] == 1
    assert env["component"] == "card"
    assert env["props"] == {"title": "T"}
    assert env["fallback"] == "T — plain text"
    assert env["id"].startswith("ui_")


def test_component_name_is_normalized():
    assert build_envelope(" Card ", {"title": "T"}, "x")["component"] == "card"


@pytest.mark.parametrize("comp", ["hologram", "", "cardd"])
def test_rejects_components_outside_the_catalog(comp):
    with pytest.raises(ValueError, match="unknown component"):
        build_envelope(comp, {"title": "T"}, "x")


def test_every_catalog_entry_is_accepted():
    for comp in UI_CATALOG:
        assert (
            build_envelope(comp, {"title": "T", "options": [{"label": "a"}]}, "x")[
                "component"
            ]
            == comp
        )


def test_fallback_is_required():
    # Without it the component is invisible off-web, in transcripts, and to
    # screen readers.
    with pytest.raises(ValueError, match="fallback"):
        build_envelope("card", {"title": "T"}, "   ")


def test_rejects_an_empty_choices_shell():
    # Empty props render as fallback text, which is no better than replying.
    with pytest.raises(ValueError, match="options"):
        build_envelope("choices", {}, "x")


def test_rejects_a_card_with_no_content():
    with pytest.raises(ValueError, match="title"):
        build_envelope("card", {}, "x")


def test_accepts_a_card_with_any_one_content_key():
    for key in ("title", "body", "fields", "image"):
        assert build_envelope("card", {key: "v"}, "x")["component"] == "card"


def test_rejects_non_object_props():
    with pytest.raises(ValueError, match="props"):
        build_envelope("card", "nope", "x")  # type: ignore[arg-type]


def test_rejects_an_oversized_payload():
    with pytest.raises(ValueError, match="too large"):
        build_envelope("card", {"body": "x" * 40_000}, "x")


def test_ids_are_unique():
    a = build_envelope("card", {"title": "T"}, "x")["id"]
    b = build_envelope("card", {"title": "T"}, "x")["id"]
    assert a != b


# ── flush ──────────────────────────────────────────────────────────────────


class _Visitor:
    def __init__(self, stream=True, staged=None):
        self.stream = stream
        self.interaction = object()
        if staged is not None:
            setattr(self, "_jvagent_pending_ui", staged)
        self.unrecorded = False

    async def unrecord_action_execution(self):
        self.unrecorded = True


def _flusher():
    return UiAction.model_construct()


@pytest.mark.asyncio
async def test_flush_publishes_empty_metadata_only_message(monkeypatch):
    env = build_envelope("card", {"title": "T"}, "T")
    published = []

    async def fake_publish(self, visitor, content, **kw):
        published.append({"content": content, **kw})

    monkeypatch.setattr(UiAction, "publish", fake_publish)
    await _flusher().execute(_Visitor(staged=[env]))

    assert len(published) == 1
    p = published[0]
    # Empty content is what keeps ADR-0024's latch untripped.
    assert p["content"] == ""
    assert p["allow_empty"] is True
    assert p["category"] == "user"
    assert p["metadata"] == {"ui": env}


@pytest.mark.asyncio
async def test_flush_is_a_noop_without_staged_components(monkeypatch):
    called = {"n": 0}

    async def fake_publish(self, visitor, content, **kw):
        called["n"] += 1

    monkeypatch.setattr(UiAction, "publish", fake_publish)
    await _flusher().execute(_Visitor())
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_flush_skips_non_streaming_channels(monkeypatch):
    """An empty publish would reach a channel adapter as a blank message."""
    called = {"n": 0}

    async def fake_publish(self, visitor, content, **kw):
        called["n"] += 1

    monkeypatch.setattr(UiAction, "publish", fake_publish)
    env = build_envelope("card", {"title": "T"}, "x")
    await _flusher().execute(_Visitor(stream=False, staged=[env]))
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_flush_clears_the_queue_so_it_cannot_double_publish(monkeypatch):
    async def fake_publish(self, visitor, content, **kw):
        return None

    monkeypatch.setattr(UiAction, "publish", fake_publish)
    visitor = _Visitor(staged=[build_envelope("card", {"title": "T"}, "x")])
    action = _flusher()
    await action.execute(visitor)
    assert getattr(visitor, "_jvagent_pending_ui") == []
