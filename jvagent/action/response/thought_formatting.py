"""Thought text formatting contract and flush-time normalization.

**Server (jvagent)**

- Emit plain text with intentional newlines between distinct reasoning fragments
  (e.g. list items returned as separate API parts should be joined with newlines,
  not glued with ``""``).
- On ``ResponseBus`` flush for ``category="thought"``, run
  :func:`normalize_thought_text_for_publish` to apply *conservative* whitespace
  cleanup only (no inventing markdown or restructuring model output).

**Client (jvchat)**

- Owns readability: typography, paragraph spacing, monospace vs markdown.
- Must not depend on the server adding structures the model did not output.
"""

from __future__ import annotations

import re

# Allow at most one blank line between paragraphs (i.e. two consecutive newlines).
_MAX_BLANK_RUN = 2


def normalize_thought_text_for_publish(text: str) -> str:
    """Conservative whitespace normalization for persisted/streamed thought bodies.

    - Normalizes ``\\r\\n`` / ``\\r`` to ``\\n``.
    - Strips trailing spaces/tabs on each line.
    - Collapses runs of more than two consecutive newlines down to two.
    - Strips leading/trailing whitespace on the full string.

    Does not rewrite bullets, numbers, or insert paragraph breaks that were not
    implied by existing newlines.
    """
    if not text:
        return ""
    s = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip(" \t") for line in s.split("\n")]
    s = "\n".join(lines)
    # Collapse 3+ newlines → 2 (paragraph boundary at most)
    s = re.sub(r"\n{3,}", "\n" * _MAX_BLANK_RUN, s)
    return s.strip()
