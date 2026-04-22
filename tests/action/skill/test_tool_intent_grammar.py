"""Guards the grammatical contract between MONOLOGUE_OPENERS and tool intent phrases.

Each opener in ``MONOLOGUE_OPENERS`` ("Let me", "I'll", "Next I'll", "Now I'll")
requires a bare-infinitive verb. ``_extract_tool_intent`` must therefore emit
phrases that begin with an infinitive so the composed announcement reads
naturally, e.g. "I'll search for V75 Inc. with search" - never
"I'll searching for V75 Inc. with search".
"""

from __future__ import annotations

import json
import re

from jvagent.action.skill.prompts import (
    MONOLOGUE_OPENERS,
    TOOL_CALL_ANNOUNCE_TEMPLATE,
)
from jvagent.action.skill.skill_action import SkillAction

# Any word ending in "-ing" at the start of the intent phrase would produce
# ungrammatical output after "I'll" / "Let me" / "Next I'll" / "Now I'll".
_GERUND_START = re.compile(r"^[A-Za-z]+ing\b")


def _assert_composes_grammatically(intent: str) -> None:
    assert not _GERUND_START.match(
        intent
    ), f"intent phrase must begin with an infinitive, got gerund-style: {intent!r}"
    for opener in MONOLOGUE_OPENERS:
        announcement = TOOL_CALL_ANNOUNCE_TEMPLATE.format(
            opener=opener, intent=intent, tool_name="tool"
        )
        assert not re.search(
            rf"{re.escape(opener)}\s+[A-Za-z]+ing\b",
            announcement,
        ), f"ungrammatical announcement for opener {opener!r}: {announcement!r}"


def test_query_arg_produces_infinitive_intent():
    intent = SkillAction._extract_tool_intent(json.dumps({"query": "V75 Inc. founded"}))
    assert intent.startswith("search for ")
    _assert_composes_grammatically(intent)


def test_search_arg_produces_infinitive_intent():
    intent = SkillAction._extract_tool_intent(json.dumps({"search": "2017"}))
    assert intent.startswith("search for ")
    _assert_composes_grammatically(intent)


def test_skill_name_arg_produces_activate_intent():
    intent = SkillAction._extract_tool_intent(
        json.dumps({"skill_name": "pageindex_search"})
    )
    assert intent.startswith("activate ")
    _assert_composes_grammatically(intent)


def test_message_arg_produces_send_intent():
    intent = SkillAction._extract_tool_intent(json.dumps({"message": "hi"}))
    assert intent.startswith("send ")
    _assert_composes_grammatically(intent)


def test_command_arg_produces_run_intent():
    intent = SkillAction._extract_tool_intent(json.dumps({"command": "ls -la"}))
    assert intent.startswith("run ")
    _assert_composes_grammatically(intent)


def test_url_arg_produces_fetch_intent():
    intent = SkillAction._extract_tool_intent(
        json.dumps({"url": "https://example.com"})
    )
    assert intent.startswith("fetch ")
    _assert_composes_grammatically(intent)


def test_target_key_fallback_uses_infinitive():
    intent = SkillAction._extract_tool_intent(json.dumps({"file_path": "/tmp/x.txt"}))
    assert intent.startswith("work to ") or intent.startswith("work ")
    _assert_composes_grammatically(intent)


def test_generic_fallback_uses_infinitive():
    intent = SkillAction._extract_tool_intent(json.dumps({"weirdkey": "value"}))
    assert intent.startswith("work with ")
    _assert_composes_grammatically(intent)


def test_empty_args_uses_infinitive_default():
    intent = SkillAction._extract_tool_intent("")
    assert intent == "figure out the next step"
    _assert_composes_grammatically(intent)


def test_malformed_json_uses_infinitive_fallback():
    intent = SkillAction._extract_tool_intent("{not json")
    assert intent.startswith("work with ")
    _assert_composes_grammatically(intent)


def test_sample_failure_cases_now_compose_cleanly():
    """Regression: the exact phrasings the user reported must no longer occur."""
    announcements = []
    for args in (
        {"skill_name": "pageindex_search"},
        {"query": "V75 Inc. founded"},
        {"query": "Founded V75 Inc."},
        {"search": "2017"},
    ):
        intent = SkillAction._extract_tool_intent(json.dumps(args))
        for opener in MONOLOGUE_OPENERS:
            announcements.append(
                TOOL_CALL_ANNOUNCE_TEMPLATE.format(
                    opener=opener, intent=intent, tool_name="search"
                )
            )

    for a in announcements:
        assert "I'll activating" not in a, a
        assert "I'll searching" not in a, a
        assert "Let me searching" not in a, a
        assert "Let me activating" not in a, a
        assert "Next I'll searching" not in a, a
        assert "Now I'll searching" not in a, a
