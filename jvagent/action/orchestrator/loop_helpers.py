"""Pure helpers used by the orchestrator think-act-observe loop."""

from __future__ import annotations

from typing import Any, Dict

from jvagent.action.orchestrator.constants import TEXT_KEYS


def text_candidate(decision: Dict[str, Any]) -> str:
    """Extract user-facing text from a model decision dict."""
    for key in TEXT_KEYS:
        val = decision.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""
