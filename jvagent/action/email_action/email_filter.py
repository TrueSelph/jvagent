"""Email channel filter (lightweight formatting before send)."""

import html
import re
from typing import List, Optional

from jvagent.action.response.channel_filter import ChannelFilter
from jvagent.action.response.message import ResponseMessage

# Match http(s) URLs in escaped plain text (conservative end chars).
_URL_RE = re.compile(r"(https?://[^\s<]+[^\s<.,;:!?)\"\'\]>)])")


def _autolink_escaped_line(s: str) -> str:
    """Wrap raw URLs in <a href>; input must already be html-escaped."""

    def repl(m: re.Match[str]) -> str:
        u = m.group(0)
        he = html.escape(u, quote=True)
        return f'<a href="{he}">{u}</a>'

    return _URL_RE.sub(repl, s)


def _segment_to_html(s: str) -> str:
    """Escape, linkify, then apply *bold* and _italic_ (channel directive style)."""
    s = html.escape(s, quote=False)
    s = _autolink_escaped_line(s)
    s = re.sub(r"\*([^*]+)\*", r"<strong>\1</strong>", s)
    s = re.sub(r"_([^_]+)_", r"<em>\1</em>", s)
    return s


def plain_text_to_email_html(text: str) -> str:
    """Turn channel-style plain text into a small HTML fragment (paragraphs, lists, quotes)."""
    text = text.replace("**", "*")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?b>", "*", text, flags=re.IGNORECASE)
    lines = text.split("\n")
    out: List[str] = []
    i = 0
    while i < len(lines):
        raw = lines[i]
        if re.match(r"^\s*>\s?", raw):
            qparts: List[str] = []
            while i < len(lines) and re.match(r"^\s*>\s?", lines[i]):
                inner = re.sub(r"^\s*>\s?", "", lines[i], count=1)
                qparts.append(_segment_to_html(inner))
                i += 1
            out.append("<blockquote>" + "<br/>\n".join(qparts) + "</blockquote>")
            continue
        if re.match(r"^\s*[\*\-]\s+", raw):
            items: List[str] = []
            while i < len(lines) and re.match(r"^\s*[\*\-]\s+", lines[i]):
                item_text = re.sub(r"^\s*[\*\-]\s+", "", lines[i], count=1)
                items.append("<li>" + _segment_to_html(item_text) + "</li>")
                i += 1
            out.append("<ul>\n" + "\n".join(items) + "\n</ul>")
            continue
        if not raw.strip():
            i += 1
            continue
        para: List[str] = []
        while i < len(lines):
            ln = lines[i]
            if not ln.strip():
                i += 1
                break
            if re.match(r"^\s*>\s?", ln) or re.match(r"^\s*[\*\-]\s+", ln):
                break
            para.append(ln)
            i += 1
        if para:
            rendered = "<br/>\n".join(_segment_to_html(p) for p in para)
            out.append("<p>" + rendered + "</p>")
    return "\n".join(out) if out else ""


class EmailFilter(ChannelFilter):
    """Normalize content for HTML email bodies (structure + safe emphasis)."""

    def __init__(
        self, channels: Optional[List[str]] = None, priority: int = 100
    ) -> None:
        if channels is None:
            channels = ["email"]
        super().__init__(channels=channels, priority=priority)

    async def filter(self, message: ResponseMessage) -> None:
        if not message.content:
            return
        if message.metadata.get("email_html") or message.metadata.get("html_content"):
            return
        text = str(message.content)
        html_fragment = plain_text_to_email_html(text)
        if not html_fragment:
            return
        message.content = html_fragment
        message.metadata = dict(message.metadata or {})
        message.metadata.setdefault("email_wrapped_html", True)
