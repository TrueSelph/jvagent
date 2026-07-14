"""Shared utilities for cleaning LLM JSON output.

Providers that don't support ``response_format`` (e.g. ollama, Anthropic) can
emit markdown code fences around JSON despite prompt instructions not to.
:func:`strip_json_fences` removes those fences so downstream ``json.loads``
succeeds without a tolerant regex fallback.
"""

from __future__ import annotations

import re

_FENCE_OPEN_RE = re.compile(r"^```(?:json|JSON|js)?\s*\n?", re.MULTILINE)


def strip_json_fences(text: str) -> str:
    r"""Remove a leading `````json`` (or bare ```````) fence and its closer.

    Handles:
    - `````json\n{...}\n`````
    - `````JSON\n{...}\n`````  (case-insensitive language tag)
    - `````js\n{...}\n`````
    - `````\n{...}\n`````       (bare fence, no language tag)

    If no fence is present the original trimmed text is returned unchanged.
    Only the outermost fence pair is stripped — nested fences inside the JSON
    string values are left intact.
    """
    stripped = (text or "").strip()
    if not stripped:
        return stripped

    # Try to match an opening fence at the very start.
    open_match = _FENCE_OPEN_RE.match(stripped)
    if open_match:
        inner = stripped[open_match.end() :]
    elif stripped.startswith("```"):
        # Bare fence that the regex didn't catch (e.g. trailing spaces before
        # newline that the \s* didn't absorb).
        inner = stripped[3:]
        nl = inner.find("\n")
        if nl != -1 and nl <= 30:
            inner = inner[nl + 1 :]
    else:
        # No opening fence — return as-is.
        return stripped

    # Strip the closing fence (last ``` occurrence).
    end_idx = inner.rfind("```")
    if end_idx != -1:
        inner = inner[:end_idx]

    return inner.strip()


__all__ = ["strip_json_fences"]
