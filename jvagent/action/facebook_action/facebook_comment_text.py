"""Plain-text shaping for Facebook Page comments.

Facebook comments are plain text — no markdown, no HTML. Both the channel
filter (bus pipeline) and the adapter (delivery, for content that never went
through the filter) need the same transformation, and having it written twice
means the two drift: a fix to one silently leaves the other emitting raw
markdown into a public comment.
"""

from __future__ import annotations

import re

# Facebook's documented comment ceiling. Defined once so the filter and the
# adapter cannot disagree about where to cut.
FACEBOOK_COMMENT_MAX_LENGTH = 9000

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"\*(.+?)\*")
_HEADING = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

_HTML_REPLACEMENTS = (
    ("<br/>", "\n"),
    ("<br>", "\n"),
    ("<b>", ""),
    ("</b>", ""),
    ("<i>", ""),
    ("</i>", ""),
    ("<p>", "\n"),
    ("</p>", ""),
)


def _link_to_text(match: "re.Match[str]") -> str:
    """``[label](url)`` -> ``label (url)``, or just ``label`` when redundant.

    A comment reader has no hover target and no way to recover a dropped URL,
    so the destination is kept inline rather than discarded. When the label
    already *is* the URL, repeating it would only add noise.
    """
    label = match.group(1).strip()
    url = match.group(2).strip()
    if not url or label == url:
        return label
    return f"{label} ({url})"


def to_facebook_comment_text(text: str) -> str:
    """Reduce markdown/HTML to plain text and bound it to the comment limit."""
    if not text:
        return ""
    out = _BOLD.sub(r"\1", text)
    out = _ITALIC.sub(r"\1", out)
    out = _HEADING.sub("", out)
    out = _LINK.sub(_link_to_text, out)
    for needle, replacement in _HTML_REPLACEMENTS:
        out = out.replace(needle, replacement)
    out = out.strip()
    if len(out) > FACEBOOK_COMMENT_MAX_LENGTH:
        out = out[: FACEBOOK_COMMENT_MAX_LENGTH - 3].rstrip() + "..."
    return out
