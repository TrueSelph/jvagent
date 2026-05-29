"""Provider-specific message format helpers.

Internal message format is OpenAI-compatible (tool_calls at message level,
``tool`` role for results). Anthropic and similar providers need conversion
to content-block form (``tool_use`` / ``tool_result`` blocks).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List


def parse_tool_arguments(arguments: Any) -> Dict[str, Any]:
    """Parse tool-call arguments from a JSON string or pre-parsed dict.

    Returns an empty dict if *arguments* is malformed or of an unexpected type.
    """
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def convert_messages_for_provider(
    messages: List[Dict[str, Any]], provider: str
) -> List[Dict[str, Any]]:
    """Convert internal message format to provider-specific format.

    The agentic loop maintains messages in OpenAI-compatible format
    (``tool_calls`` at message level, ``tool`` role for results). Anthropic
    requires:
        - ``tool_calls`` become ``tool_use`` content blocks on the assistant
          message.
        - ``tool`` role messages become ``user`` messages with
          ``tool_result`` content blocks (consecutive results merged).

    Other providers receive the input unchanged.
    """
    if provider != "anthropic":
        return messages

    converted: List[Dict[str, Any]] = []
    pending_tool_results: List[Dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "")
        if role != "tool" and pending_tool_results:
            converted.append({"role": "user", "content": list(pending_tool_results)})
            pending_tool_results = []

        if role == "tool":
            pending_tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": msg.get("content", ""),
                }
            )
        elif role == "assistant" and msg.get("tool_calls"):
            content_blocks: List[Dict[str, Any]] = []
            text = msg.get("content")
            if text:
                content_blocks.append({"type": "text", "text": text})
            for tc in msg["tool_calls"]:
                func = tc.get("function", {})
                content_blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "input": parse_tool_arguments(func.get("arguments", "{}")),
                    }
                )
            converted.append({"role": "assistant", "content": content_blocks})
        else:
            converted.append(msg)

    if pending_tool_results:
        converted.append({"role": "user", "content": list(pending_tool_results)})

    return converted
