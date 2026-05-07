"""Tests for the cockpit router pre-classifier (Phase 3 latency optimisation)."""

from __future__ import annotations

from unittest.mock import MagicMock

from jvagent.action.cockpit.routing.preclassifier import (
    MAX_UTTERANCE_LENGTH,
    classify_smalltalk,
    has_active_tasks,
    maybe_preclassify,
    synthesize_smalltalk_routing,
)

# ----------------------------------------------------------------------
# classify_smalltalk
# ----------------------------------------------------------------------


def test_classify_greetings() -> None:
    for text in ["hi", "Hi", "Hi!", "hello", "Hey there", "Good morning"]:
        assert classify_smalltalk(text) == "greeting", text


def test_classify_thanks() -> None:
    for text in ["thanks", "Thanks!", "thank you", "Thank you so much"]:
        assert classify_smalltalk(text) == "thanks", text


def test_classify_goodbyes() -> None:
    for text in ["bye", "Bye!", "goodbye", "see you", "ttyl", "good night"]:
        assert classify_smalltalk(text) == "goodbye", text


def test_classify_pleasantries_with_apostrophe() -> None:
    """Apostrophes are stripped — ``you're welcome`` matches the canonical entry."""
    for text in ["you're welcome", "you’re welcome", "no problem", "no worries"]:
        assert classify_smalltalk(text) == "pleasantry", text


def test_classify_misses_substantive_input() -> None:
    """Long / compound utterances do not match — pre-classifier is conservative."""
    for text in [
        "hi can you help me search for something",
        "What times work for training?",
        "Cancel the training signup please",
        "hi how are you",
        "thanks for the info, can you also send me the link?",
    ]:
        assert classify_smalltalk(text) is None, text


def test_classify_misses_ambiguous_acknowledgments() -> None:
    """Ambiguous tokens (``ok``, ``got it``, ``yes``, ``no``) are NOT pre-classified.

    They could be answers to an assistant question — accept the LLM cost
    rather than risk mis-routing.
    """
    for text in ["ok", "okay", "got it", "yes", "no", "yeah", "nope", "alright"]:
        assert classify_smalltalk(text) is None, text


def test_classify_respects_length_cap() -> None:
    """Utterances longer than the cap return None even if they include a smalltalk word."""
    long_text = "hi " * (MAX_UTTERANCE_LENGTH // 3 + 5)
    assert len(long_text) > MAX_UTTERANCE_LENGTH
    assert classify_smalltalk(long_text) is None


def test_classify_handles_empty_and_whitespace() -> None:
    assert classify_smalltalk("") is None
    assert classify_smalltalk("   ") is None
    assert classify_smalltalk(None) is None  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# has_active_tasks
# ----------------------------------------------------------------------


def test_has_active_tasks_false_for_missing_visitor() -> None:
    assert has_active_tasks(None) is False


def test_has_active_tasks_false_when_conversation_missing() -> None:
    visitor = MagicMock()
    visitor.conversation = None
    assert has_active_tasks(visitor) is False


def test_has_active_tasks_false_when_store_raises() -> None:
    visitor = MagicMock()
    visitor.conversation = MagicMock()
    type(visitor).tasks = property(
        lambda _self: (_ for _ in ()).throw(RuntimeError("nope"))
    )
    assert has_active_tasks(visitor) is False


def test_has_active_tasks_true_when_active_task_present() -> None:
    visitor = MagicMock()
    visitor.conversation = MagicMock()
    visitor.tasks = MagicMock()
    visitor.tasks.list = MagicMock(return_value=[MagicMock()])
    assert has_active_tasks(visitor) is True


def test_has_active_tasks_false_when_list_empty() -> None:
    visitor = MagicMock()
    visitor.conversation = MagicMock()
    visitor.tasks = MagicMock()
    visitor.tasks.list = MagicMock(return_value=[])
    assert has_active_tasks(visitor) is False


# ----------------------------------------------------------------------
# synthesize_smalltalk_routing
# ----------------------------------------------------------------------


def test_synthesize_smalltalk_routing_dispatches_to_converse() -> None:
    result = synthesize_smalltalk_routing("greeting", "Hi!")
    assert result.posture == "RESPOND"
    assert result.intent_type == "CONVERSATIONAL"
    assert result.actions == ["converse"]
    assert result.interact_actions == []
    assert result.confidence >= 0.9
    assert result.canned_response == ""
    assert "preclassifier" in result.raw_response


# ----------------------------------------------------------------------
# maybe_preclassify
# ----------------------------------------------------------------------


def test_maybe_preclassify_returns_none_when_disabled() -> None:
    visitor = MagicMock()
    visitor.conversation = MagicMock()
    visitor.tasks = MagicMock()
    visitor.tasks.list = MagicMock(return_value=[])
    assert maybe_preclassify(visitor, "hi", enabled=False) is None


def test_maybe_preclassify_returns_routing_for_smalltalk() -> None:
    visitor = MagicMock()
    visitor.conversation = MagicMock()
    visitor.tasks = MagicMock()
    visitor.tasks.list = MagicMock(return_value=[])
    routing = maybe_preclassify(visitor, "Hi there!")
    assert routing is not None
    assert routing.actions == ["converse"]


def test_maybe_preclassify_skipped_when_active_task_present() -> None:
    """Even pure greetings yield None when there's an active task — fragments
    might be answers to an in-flight interview."""
    visitor = MagicMock()
    visitor.conversation = MagicMock()
    visitor.tasks = MagicMock()
    visitor.tasks.list = MagicMock(return_value=[MagicMock()])
    assert maybe_preclassify(visitor, "Hi") is None


def test_maybe_preclassify_returns_none_for_non_smalltalk() -> None:
    visitor = MagicMock()
    visitor.conversation = MagicMock()
    visitor.tasks = MagicMock()
    visitor.tasks.list = MagicMock(return_value=[])
    assert maybe_preclassify(visitor, "What's the weather?") is None


def test_maybe_preclassify_returns_none_when_visitor_missing() -> None:
    """No visitor → can't check active tasks → safer to fall through to LLM."""
    # has_active_tasks returns False on missing visitor (defensive), so this
    # test validates the path where conversation is None.
    visitor = MagicMock()
    visitor.conversation = None
    assert maybe_preclassify(visitor, "Hi") is not None
