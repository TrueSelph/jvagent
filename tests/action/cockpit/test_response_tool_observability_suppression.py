"""Tests for the response-tool observability suppression.

User-visible tools (``response_publish``, ``response_emit_thought``,
``response_deliver_via_persona``) already produce a visible artifact on
the response bus — emitting tool_call/tool_result/tool_progress thought
envelopes for them duplicates the noise into jvchat's Reasoning panel as
``← ok: response_publish``.

The suppression list lives in :mod:`jvagent.action.cockpit.engine`.
"""

import os
from unittest.mock import patch

from jvagent.action.cockpit.engine import (
    USER_VISIBLE_TOOL_NAMES,
    _suppress_tool_observability,
)


def test_user_visible_tools_suppressed_by_default():
    for name in USER_VISIBLE_TOOL_NAMES:
        assert _suppress_tool_observability(name), name


def test_non_user_visible_tools_not_suppressed():
    for name in [
        "memory_set",
        "task_create_plan",
        "conversation_search",
        "skill_search",
        "artifact_add",
    ]:
        assert not _suppress_tool_observability(name), name


def test_env_override_brings_back_emit():
    with patch.dict(
        os.environ,
        {"JVAGENT_COCKPIT_VERBOSE_RESPONSE_TOOLS": "true"},
        clear=False,
    ):
        for name in USER_VISIBLE_TOOL_NAMES:
            assert not _suppress_tool_observability(name), name


def test_env_override_accepts_various_truthy_values():
    for value in ["1", "yes", "ON", "True"]:
        with patch.dict(
            os.environ,
            {"JVAGENT_COCKPIT_VERBOSE_RESPONSE_TOOLS": value},
            clear=False,
        ):
            assert not _suppress_tool_observability("response_publish"), value


def test_env_override_falsy_keeps_suppression():
    for value in ["false", "0", "no", "off", ""]:
        with patch.dict(
            os.environ,
            {"JVAGENT_COCKPIT_VERBOSE_RESPONSE_TOOLS": value},
            clear=False,
        ):
            assert _suppress_tool_observability("response_publish"), value
