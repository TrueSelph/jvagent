"""JSON cleanup behavior aligned with UserLongMemoryInteractAction parsing."""

import json


def _strip_json_fence(raw: str) -> str:
    response_clean = raw.strip()
    if response_clean.startswith("```json"):
        response_clean = response_clean[7:]
    if response_clean.startswith("```"):
        response_clean = response_clean[3:]
    if response_clean.endswith("```"):
        response_clean = response_clean[:-3]
    return response_clean.strip()


def test_strip_json_fence_and_parse() -> None:
    wrapped = '```json\n{"interests": {"content": "- x", "keywords": ["a"]}}\n```'
    data = json.loads(_strip_json_fence(wrapped))
    assert "interests" in data
