"""Authored wire fixtures for the provider conformance suite.

Each scenario is ONE logical exchange (``SCENARIOS``: request + normalised
expectation) and each provider supplies the wire body its endpoint would return
for it (``BODIES``), written against the provider's documented format. A
recording under ``fixtures/<provider>/<scenario>.json`` replaces the authored
body when present (see ``_transport.py``).

Groq and OpenRouter speak the OpenAI wire format and reuse the ``openai``
bodies (``WIRE_FOR``); the test still asserts their adapters label the
response with their own provider name.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

# --------------------------------------------------------------------------- #
# Shared request material
# --------------------------------------------------------------------------- #

WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}

_SYSTEM = {"role": "system", "content": "You are a terse assistant."}
_ASK_HELLO = [_SYSTEM, {"role": "user", "content": "Say hello."}]
_ASK_WEATHER = [_SYSTEM, {"role": "user", "content": "Weather in Paris?"}]
_ASK_TWO = [_SYSTEM, {"role": "user", "content": "Weather in Paris and Berlin?"}]
_ROUNDTRIP = [
    _SYSTEM,
    {"role": "user", "content": "Weather in Paris?"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
            }
        ],
    },
    {"role": "tool", "tool_call_id": "call_1", "name": "get_weather", "content": "18C"},
]

# scenario → {request: {messages, tools, stream}, expect: {...}, action: {...}}
SCENARIOS: Dict[str, Dict[str, Any]] = {
    "text": {
        "request": {"messages": _ASK_HELLO},
        "expect": {
            "text": "Hello there!",
            "finish_reason": "stop",
            "tool_calls": [],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
        },
    },
    "tool_call": {
        "request": {"messages": _ASK_WEATHER, "tools": [WEATHER_TOOL]},
        "expect": {
            "finish_reason": "tool_calls",
            "tool_calls": [{"name": "get_weather", "arguments": {"city": "Paris"}}],
        },
    },
    "parallel_tool_calls": {
        "request": {"messages": _ASK_TWO, "tools": [WEATHER_TOOL]},
        "expect": {
            "finish_reason": "tool_calls",
            "tool_calls": [
                {"name": "get_weather", "arguments": {"city": "Paris"}},
                {"name": "get_weather", "arguments": {"city": "Berlin"}},
            ],
        },
    },
    "tool_result_roundtrip": {
        "request": {"messages": _ROUNDTRIP, "tools": [WEATHER_TOOL]},
        "expect": {
            "text": "It is 18C in Paris.",
            "finish_reason": "stop",
            "tool_calls": [],
            # The adapter must have sent the tool result back in the provider's
            # own shape, correlated to call_1.
            "request_has_tool_result": "call_1",
        },
    },
    "stream_text": {
        "request": {"messages": _ASK_HELLO, "stream": True},
        "expect": {"text": "Hello there!", "finish_reason": "stop", "tool_calls": []},
    },
    "stream_tool_call": {
        "request": {"messages": _ASK_WEATHER, "tools": [WEATHER_TOOL], "stream": True},
        "expect": {
            "finish_reason": "tool_calls",
            "tool_calls": [{"name": "get_weather", "arguments": {"city": "Paris"}}],
        },
    },
    "truncation": {
        "request": {"messages": _ASK_HELLO, "max_tokens": 3},
        "expect": {"text": "The quick brown", "finish_reason": "length"},
    },
    "cached_usage": {
        "request": {"messages": _ASK_HELLO},
        "expect": {
            "text": "Hello there!",
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 1000, "completion_tokens": 3},
            # Per-provider: openai/anthropic report 800 cached; ollama has no cache.
            "cached_read_tokens": {"openai": 800, "anthropic": 800, "ollama": 0},
        },
    },
    "thinking": {
        "request": {"messages": [_SYSTEM, {"role": "user", "content": "6 times 7?"}]},
        "expect": {"text": "42", "finish_reason": "stop", "thinking": True},
    },
    "retry_429": {
        "request": {"messages": _ASK_HELLO},
        "action": {"max_retries": 1},
        "expect": {"text": "Hello there!", "finish_reason": "stop", "requests": 2},
    },
    "error_500": {
        "request": {"messages": _ASK_HELLO},
        "action": {"max_retries": 0},
        "expect": {"error": "HTTPStatusError"},
    },
    "malformed_body": {
        "request": {"messages": _ASK_HELLO},
        "action": {"max_retries": 0},
        "expect": {"error": "Exception"},
    },
}

# Which authored wire body a provider replays.
WIRE_FOR = {
    "openai": "openai",
    "groq": "openai",
    "openrouter": "openai",
    "anthropic": "anthropic",
    "ollama": "ollama",
}

# --------------------------------------------------------------------------- #
# Wire bodies
# --------------------------------------------------------------------------- #


def _resp(body: Any, status: int = 200, headers: Dict[str, str] | None = None) -> Dict:
    return {
        "status": status,
        "headers": headers or {"content-type": "application/json"},
        "body": body if isinstance(body, str) else json.dumps(body),
    }


def _sse(events: List[Any], done: bool = True) -> str:
    lines = [f"data: {json.dumps(e)}\n\n" for e in events]
    if done:
        lines.append("data: [DONE]\n\n")
    return "".join(lines)


def _sse_headers() -> Dict[str, str]:
    return {"content-type": "text/event-stream"}


# ---- OpenAI ----------------------------------------------------------------


def _oai(message: Dict[str, Any], finish: str, usage: Dict[str, Any] | None = None) -> Dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": "gpt-4o-mini",
        "choices": [{"index": 0, "message": {"role": "assistant", **message}, "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
    }


def _oai_call(cid: str, city: str) -> Dict[str, Any]:
    return {
        "id": cid,
        "type": "function",
        "function": {"name": "get_weather", "arguments": json.dumps({"city": city})},
    }


def _oai_chunk(delta: Dict[str, Any], finish: Any = None, usage: Any = None) -> Dict:
    chunk: Dict[str, Any] = {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "model": "gpt-4o-mini",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if usage is not None:
        chunk["usage"] = usage
    return chunk


_OPENAI: Dict[str, List[Dict[str, Any]]] = {
    "text": [_resp(_oai({"content": "Hello there!"}, "stop"))],
    "tool_call": [_resp(_oai({"content": None, "tool_calls": [_oai_call("call_1", "Paris")]}, "tool_calls"))],
    "parallel_tool_calls": [
        _resp(
            _oai(
                {"content": None, "tool_calls": [_oai_call("call_1", "Paris"), _oai_call("call_2", "Berlin")]},
                "tool_calls",
            )
        )
    ],
    "tool_result_roundtrip": [_resp(_oai({"content": "It is 18C in Paris."}, "stop"))],
    "stream_text": [
        _resp(
            _sse(
                [
                    _oai_chunk({"role": "assistant", "content": ""}),
                    _oai_chunk({"content": "Hello "}),
                    _oai_chunk({"content": "there!"}),
                    _oai_chunk({}, finish="stop"),
                ]
            ),
            headers=_sse_headers(),
        )
    ],
    "stream_tool_call": [
        _resp(
            _sse(
                [
                    _oai_chunk({"role": "assistant", "content": None}),
                    _oai_chunk(
                        {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "get_weather", "arguments": ""},
                                }
                            ]
                        }
                    ),
                    _oai_chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"city": '}}]}),
                    _oai_chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"Paris"}'}}]}),
                    _oai_chunk({}, finish="tool_calls"),
                ]
            ),
            headers=_sse_headers(),
        )
    ],
    "truncation": [_resp(_oai({"content": "The quick brown"}, "length"))],
    "cached_usage": [
        _resp(
            _oai(
                {"content": "Hello there!"},
                "stop",
                usage={
                    "prompt_tokens": 1000,
                    "completion_tokens": 3,
                    "total_tokens": 1003,
                    "prompt_tokens_details": {"cached_tokens": 800},
                },
            )
        )
    ],
    "thinking": [_resp(_oai({"content": "42", "reasoning_content": "6*7=42"}, "stop"))],
    "retry_429": [
        _resp({"error": {"message": "rate limited"}}, status=429, headers={"content-type": "application/json", "retry-after": "0"}),
        _resp(_oai({"content": "Hello there!"}, "stop")),
    ],
    "error_500": [_resp({"error": {"message": "boom"}}, status=500)],
    "malformed_body": [_resp("<html>not json</html>", headers={"content-type": "text/html"})],
}

# ---- Anthropic -------------------------------------------------------------


def _anth(content: List[Dict[str, Any]], stop: str, usage: Dict[str, Any] | None = None) -> Dict:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-3-5-sonnet",
        "content": content,
        "stop_reason": stop,
        "usage": usage or {"input_tokens": 12, "output_tokens": 3},
    }


def _anth_use(tid: str, city: str) -> Dict[str, Any]:
    return {"type": "tool_use", "id": tid, "name": "get_weather", "input": {"city": city}}


def _anth_sse(events: List[Dict[str, Any]]) -> str:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


_ANTHROPIC: Dict[str, List[Dict[str, Any]]] = {
    "text": [_resp(_anth([{"type": "text", "text": "Hello there!"}], "end_turn"))],
    "tool_call": [_resp(_anth([_anth_use("toolu_1", "Paris")], "tool_use"))],
    "parallel_tool_calls": [_resp(_anth([_anth_use("toolu_1", "Paris"), _anth_use("toolu_2", "Berlin")], "tool_use"))],
    "tool_result_roundtrip": [_resp(_anth([{"type": "text", "text": "It is 18C in Paris."}], "end_turn"))],
    "stream_text": [
        _resp(
            _anth_sse(
                [
                    {"type": "message_start", "message": {"id": "msg_1", "usage": {"input_tokens": 12, "output_tokens": 0}}},
                    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello "}},
                    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "there!"}},
                    {"type": "content_block_stop", "index": 0},
                    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}},
                    {"type": "message_stop"},
                ]
            ),
            headers=_sse_headers(),
        )
    ],
    "stream_tool_call": [
        _resp(
            _anth_sse(
                [
                    {"type": "message_start", "message": {"id": "msg_1", "usage": {"input_tokens": 12, "output_tokens": 0}}},
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {}},
                    },
                    {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"city": '}},
                    {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '"Paris"}'}},
                    {"type": "content_block_stop", "index": 0},
                    {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 9}},
                    {"type": "message_stop"},
                ]
            ),
            headers=_sse_headers(),
        )
    ],
    "truncation": [_resp(_anth([{"type": "text", "text": "The quick brown"}], "max_tokens"))],
    "cached_usage": [
        _resp(
            _anth(
                [{"type": "text", "text": "Hello there!"}],
                "end_turn",
                usage={
                    "input_tokens": 200,
                    "output_tokens": 3,
                    "cache_read_input_tokens": 800,
                    "cache_creation_input_tokens": 0,
                },
            )
        )
    ],
    "thinking": [
        _resp(
            _anth(
                [
                    {"type": "thinking", "thinking": "6*7=42", "signature": "sig"},
                    {"type": "text", "text": "42"},
                ],
                "end_turn",
            )
        )
    ],
    "retry_429": [
        _resp({"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}, status=429, headers={"content-type": "application/json", "retry-after": "0"}),
        _resp(_anth([{"type": "text", "text": "Hello there!"}], "end_turn")),
    ],
    "error_500": [_resp({"type": "error", "error": {"type": "api_error", "message": "boom"}}, status=500)],
    "malformed_body": [_resp("<html>not json</html>", headers={"content-type": "text/html"})],
}

# ---- Ollama ----------------------------------------------------------------


def _oll(message: Dict[str, Any], done_reason: str = "stop", prompt: int = 12, eval_count: int = 3) -> Dict:
    return {
        "model": "llama3.1",
        "created_at": "2026-09-05T00:00:00Z",
        "message": {"role": "assistant", **message},
        "done": True,
        "done_reason": done_reason,
        "prompt_eval_count": prompt,
        "eval_count": eval_count,
    }


def _oll_call(city: str) -> Dict[str, Any]:
    return {"function": {"name": "get_weather", "arguments": {"city": city}}}


def _ndjson(chunks: List[Dict[str, Any]]) -> str:
    return "".join(json.dumps(c) + "\n" for c in chunks)


_OLLAMA: Dict[str, List[Dict[str, Any]]] = {
    "text": [_resp(_oll({"content": "Hello there!"}))],
    "tool_call": [_resp(_oll({"content": "", "tool_calls": [_oll_call("Paris")]}))],
    "parallel_tool_calls": [_resp(_oll({"content": "", "tool_calls": [_oll_call("Paris"), _oll_call("Berlin")]}))],
    "tool_result_roundtrip": [_resp(_oll({"content": "It is 18C in Paris."}))],
    "stream_text": [
        _resp(
            _ndjson(
                [
                    {"model": "llama3.1", "message": {"role": "assistant", "content": "Hello "}, "done": False},
                    {"model": "llama3.1", "message": {"role": "assistant", "content": "there!"}, "done": False},
                    {
                        "model": "llama3.1",
                        "message": {"role": "assistant", "content": ""},
                        "done": True,
                        "done_reason": "stop",
                        "prompt_eval_count": 12,
                        "eval_count": 3,
                    },
                ]
            ),
            headers={"content-type": "application/x-ndjson"},
        )
    ],
    "stream_tool_call": [
        _resp(
            _ndjson(
                [
                    {
                        "model": "llama3.1",
                        "message": {"role": "assistant", "content": "", "tool_calls": [_oll_call("Paris")]},
                        "done": False,
                    },
                    {
                        "model": "llama3.1",
                        "message": {"role": "assistant", "content": ""},
                        "done": True,
                        "done_reason": "stop",
                        "prompt_eval_count": 12,
                        "eval_count": 9,
                    },
                ]
            ),
            headers={"content-type": "application/x-ndjson"},
        )
    ],
    "truncation": [_resp(_oll({"content": "The quick brown"}, done_reason="length"))],
    "cached_usage": [_resp(_oll({"content": "Hello there!"}, prompt=1000))],
    "thinking": [_resp(_oll({"content": "42", "thinking": "6*7=42"}))],
    "retry_429": [
        _resp({"error": "rate limited"}, status=429, headers={"content-type": "application/json", "retry-after": "0"}),
        _resp(_oll({"content": "Hello there!"})),
    ],
    "error_500": [_resp({"error": "boom"}, status=500)],
    "malformed_body": [_resp("<html>not json</html>", headers={"content-type": "text/html"})],
}

BODIES: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
    "openai": _OPENAI,
    "anthropic": _ANTHROPIC,
    "ollama": _OLLAMA,
}


def authored_fixture(provider: str, scenario: str) -> Dict[str, Any]:
    wire = WIRE_FOR[provider]
    return {
        "source": "authored",
        "provider": provider,
        "scenario": scenario,
        "responses": [dict(r) for r in BODIES[wire][scenario]],
    }


__all__ = ["SCENARIOS", "WIRE_FOR", "BODIES", "WEATHER_TOOL", "authored_fixture"]
