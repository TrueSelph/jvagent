"""Built-in helper tool for triage skill bundles."""

from __future__ import annotations

from typing import Any, Dict, List


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "prioritize_findings",
        "description": "Sort findings by severity in descending order.",
        "parameters": {
            "type": "object",
            "properties": {
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "severity": {"type": "integer"},
                        },
                        "required": ["title", "severity"],
                    },
                }
            },
            "required": ["findings"],
        },
    }


async def execute(arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings = list(arguments.get("findings") or [])
    findings.sort(key=lambda item: int(item.get("severity", 0)), reverse=True)
    return findings
