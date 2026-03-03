"""Tests for InteractRouter action resolution (descriptions -> keys)."""

import pytest

from jvagent.action.router.interact_router import InteractRouter


def test_resolve_actions_returns_keys_unchanged():
    """Valid action keys pass through unchanged."""
    anchors_dict = {
        "PageIndexRetrievalInteractAction": [
            "User asks a question about indexed documents"
        ],
        "PersonaAction": ["General conversation"],
    }
    result = InteractRouter._resolve_actions_to_keys(
        ["PageIndexRetrievalInteractAction", "PersonaAction"],
        anchors_dict,
    )
    assert result == ["PageIndexRetrievalInteractAction", "PersonaAction"]


def test_resolve_actions_maps_descriptions_to_keys():
    """Descriptions are mapped back to action keys."""
    anchors_dict = {
        "PageIndexRetrievalInteractAction": [
            "User asks a question about indexed documents"
        ],
    }
    result = InteractRouter._resolve_actions_to_keys(
        ["User asks a question about indexed documents"],
        anchors_dict,
    )
    assert result == ["PageIndexRetrievalInteractAction"]


def test_resolve_actions_empty_input():
    """Empty actions returns empty list."""
    anchors_dict = {"PageIndexRetrievalInteractAction": ["User asks..."]}
    assert InteractRouter._resolve_actions_to_keys([], anchors_dict) == []


def test_resolve_actions_drops_invalid():
    """Invalid/non-matching entries are dropped."""
    anchors_dict = {
        "PageIndexRetrievalInteractAction": ["User asks about docs"],
    }
    result = InteractRouter._resolve_actions_to_keys(
        ["PageIndexRetrievalInteractAction", "MadeUpAction", "Random text"],
        anchors_dict,
    )
    assert result == ["PageIndexRetrievalInteractAction"]


def test_resolve_actions_deduplicates():
    """Duplicate resolutions are deduplicated."""
    anchors_dict = {
        "PageIndexRetrievalInteractAction": ["User asks about docs"],
    }
    result = InteractRouter._resolve_actions_to_keys(
        ["User asks about docs", "PageIndexRetrievalInteractAction"],
        anchors_dict,
    )
    assert result == ["PageIndexRetrievalInteractAction"]
