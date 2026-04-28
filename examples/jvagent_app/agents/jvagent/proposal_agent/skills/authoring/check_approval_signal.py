"""Detect explicit approval signals in the user's latest message.

Provides a deterministic, testable gate that must pass before PDF generation
can begin. The model calls this tool at the start of Stage 5 to confirm the
user's current message contains an approval intent before any pdf tool runs.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

# Patterns that constitute an explicit approval to proceed to PDF generation.
_APPROVAL_PATTERNS: List[str] = [
    r"\bapprove[sd]?\b",
    r"\bapproval\b",
    r"\blooks?\s+good\b",
    r"\ball\s+good\b",
    r"\bgo\s+ahead\b",
    r"\bgenerate\s+(the\s+)?pdf\b",
    r"\bfinali[sz]e\b",
    r"\bproceed\b",
    r"\byes[,!.]?\s*(please|go\b|do\b|generate\b)",
    r"\bconfirm[ed]?\b",
    r"\bapprove\s+and\s+generate\b",
    r"\bready\b.*\bpdf\b",
    r"\bpdf\b.*\bready\b",
    r"\bsend\s+(it|the\s+pdf)\b",
    r"\bship\s+it\b",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in _APPROVAL_PATTERNS]


def _detect_approval(text: str) -> bool:
    if not text or not text.strip():
        return False
    for pattern in _COMPILED:
        if pattern.search(text):
            return True
    return False


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "authoring__check_approval_signal",
        "description": (
            "Detect whether the user's latest message contains an explicit approval "
            "signal for PDF generation. Returns approved=true only when a clear "
            "affirmative is found. Call this before activating pdf_generation to "
            "enforce the review gate."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "user_message": {
                    "type": "string",
                    "description": "The user's latest chat message to check for approval.",
                },
            },
            "required": ["user_message"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    user_message = arguments.get("user_message", "")
    approved = _detect_approval(user_message)
    return {
        "approved": approved,
        "user_message": user_message,
        "instruction": (
            "Proceed to pdf_generation only if approved=true. "
            "If approved=false, respond to the user with the current document "
            "URL/path and ask them to confirm approval to generate the PDF."
        ),
    }
