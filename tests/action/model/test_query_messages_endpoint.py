"""``POST /actions/{id}/query`` with ``messages`` hits ``query_messages``.

Debug Interactions replays a later tick by sending the full chat array
(user prompt plus earlier tool results). The endpoint must not rebuild
that into ``query(prompt, history)``, which would drop post-user replay.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.model.language.base import ModelActionResult
from jvagent.action.model.language.endpoints import query_model_action

pytestmark = pytest.mark.asyncio

_MESSAGES = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "details for R381235"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_lookup_1",
                "type": "function",
                "function": {
                    "name": "lookup_report__get_issue",
                    "arguments": '{"reference_number":"R381235"}',
                },
            }
        ],
    },
    {
        "role": "tool",
        "tool_call_id": "call_lookup_1",
        "name": "lookup_report__get_issue",
        "content": "Title: damaged road.",
    },
]
_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "reply",
            "description": "",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]


async def test_messages_payload_calls_query_messages_not_query():
    action = MagicMock()
    action.provider = "openai"
    result = ModelActionResult(response="", model="gpt-4.1", provider="openai")
    action.query_messages = AsyncMock(return_value=result)
    action.query = AsyncMock()

    with patch(
        "jvagent.action.model.language.endpoints.LanguageModelAction.get",
        new=AsyncMock(return_value=action),
    ):
        await query_model_action(
            action_id="abc",
            messages=_MESSAGES,
            tools=_TOOLS,
            tool_choice="auto",
            parallel_tool_calls=False,
            model="openai/gpt-4.1",
            provider="openai",
        )

    action.query.assert_not_called()
    action.query_messages.assert_awaited_once()
    kwargs = action.query_messages.await_args.kwargs
    assert kwargs["messages"] == _MESSAGES
    assert kwargs["tools"] == _TOOLS
    assert kwargs["tool_choice"] == "auto"
    assert kwargs["parallel_tool_calls"] is False
    assert kwargs["model"] == "openai/gpt-4.1"
    assert kwargs["stream"] is False
