"""Tests for ``_extract_obs_fields_from_messages``.

Callers of ``LanguageModelAction.query_messages`` that build their own
messages array (engines, classifiers, helms) often don't pass the
separate ``system`` / ``prompt_for_observability`` / ``history``
arguments. The defensive extraction helper recovers them from the
messages array so the ``model_call`` observability event carries the
structured prompt fields. This module pins down the extraction policy.
"""

from __future__ import annotations

from jvagent.action.model.language.base import _extract_obs_fields_from_messages


class TestExtractObsFields:
    def test_empty_messages_returns_none_triple(self):
        assert _extract_obs_fields_from_messages([]) == (None, None, None)

    def test_system_plus_user_extracts_both(self):
        msgs = [
            {"role": "system", "content": "You are an assistant."},
            {"role": "user", "content": "Hello"},
        ]
        system, user, history = _extract_obs_fields_from_messages(msgs)
        assert system == "You are an assistant."
        assert user == "Hello"
        assert history == []  # empty list, not None — caller had coherent shape

    def test_user_only_no_system_extracts_user(self):
        msgs = [{"role": "user", "content": "Hi"}]
        system, user, history = _extract_obs_fields_from_messages(msgs)
        assert system is None
        assert user == "Hi"
        assert history == []

    def test_system_only_extracts_system(self):
        msgs = [{"role": "system", "content": "instructions"}]
        system, user, history = _extract_obs_fields_from_messages(msgs)
        assert system == "instructions"
        assert user is None
        assert history == []

    def test_multi_turn_with_history(self):
        """Prior turns between system and final user become ``history``."""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "follow-up"},
        ]
        system, user, history = _extract_obs_fields_from_messages(msgs)
        assert system == "sys"
        assert user == "follow-up"
        assert history == [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
        ]

    def test_tool_messages_included_in_history(self):
        """Think-act-observe loops with tool messages — tool turns end up
        in history, not stripped."""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "search for X"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "result data", "tool_call_id": "1"},
            {"role": "user", "content": "now summarize"},
        ]
        system, user, history = _extract_obs_fields_from_messages(msgs)
        assert system == "sys"
        assert user == "now summarize"
        assert len(history) == 3
        assert history[2]["role"] == "tool"

    def test_returns_last_user_message_not_first(self):
        """User-prompt extraction prefers the LAST user message —
        that's the one the model is responding to in a multi-turn chat."""
        msgs = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "..."},
            {"role": "user", "content": "second (current)"},
        ]
        _, user, _ = _extract_obs_fields_from_messages(msgs)
        assert user == "second (current)"

    def test_non_string_content_falls_through(self):
        """Multimodal messages (content as a list) aren't extracted as
        prompt strings — the helper only emits string content."""
        msgs = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "look at this"}],
            }
        ]
        _, user, _ = _extract_obs_fields_from_messages(msgs)
        # Defensive: list content doesn't get coerced to string;
        # the field stays None.
        assert user is None

    def test_malformed_entries_ignored(self):
        """Strings, ints, or other non-dict entries don't crash extraction."""
        msgs = [
            "not a dict",
            {"role": "system", "content": "sys"},
            42,
            {"role": "user", "content": "ok"},
            None,
        ]
        system, user, _ = _extract_obs_fields_from_messages(msgs)
        assert system == "sys"
        assert user == "ok"
