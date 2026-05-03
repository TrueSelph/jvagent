"""Tests for parse_routing_response and RoutingResult.from_dict."""

import json

from jvagent.action.router.routing_result import (
    POSTURE_RESPOND,
    RoutingResult,
    parse_routing_response,
)


def test_parse_routing_response_accepts_actions_field():
    """Legacy InteractRouter prompt format using `actions` parses correctly."""
    response = json.dumps(
        {
            "posture": "RESPOND",
            "interpretation": "User asks about docs.",
            "intent_type": "INFORMATIONAL",
            "actions": ["PageIndexRetrievalInteractAction"],
            "confidence": 0.9,
        }
    )

    result = parse_routing_response(response)

    assert result.posture == POSTURE_RESPOND
    assert result.actions == ["PageIndexRetrievalInteractAction"]
    assert result.intent_type == "INFORMATIONAL"


def test_parse_routing_response_accepts_skills_alias():
    """SkillRouter prompt outputs `skills`; parser maps it onto `actions`."""
    response = json.dumps(
        {
            "posture": "RESPOND",
            "interpretation": "User asks for info that needs a web search.",
            "intent_type": "INFORMATIONAL",
            "skills": ["web_search"],
            "confidence": 0.9,
            "canned_response": "Looking into that now",
        }
    )

    result = parse_routing_response(response)

    assert result.posture == POSTURE_RESPOND
    assert result.actions == ["web_search"]
    assert result.intent_type == "INFORMATIONAL"
    assert result.canned_response == "Looking into that now"


def test_parse_routing_response_actions_wins_over_skills():
    """When both keys are present, `actions` takes precedence."""
    response = json.dumps(
        {
            "posture": "RESPOND",
            "interpretation": "Both keys provided.",
            "intent_type": "INFORMATIONAL",
            "actions": ["primary_skill"],
            "skills": ["secondary_skill"],
            "confidence": 0.8,
        }
    )

    result = parse_routing_response(response)

    assert result.actions == ["primary_skill"]


def test_from_dict_skills_alias_round_trips_via_to_dict():
    """A SkillRouter-style payload survives from_dict -> to_dict as `actions`."""
    raw = {
        "posture": "RESPOND",
        "interpretation": "Test.",
        "intent_type": "INFORMATIONAL",
        "skills": ["web_search"],
        "confidence": 0.7,
    }

    parsed = RoutingResult.from_dict(raw)
    serialized = parsed.to_dict()

    assert parsed.actions == ["web_search"]
    assert serialized["actions"] == ["web_search"]
    assert "skills" not in serialized
