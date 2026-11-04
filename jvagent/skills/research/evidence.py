"""Host-observed source collection owned by the research skill."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from jvagent.skills.research.contracts import (
    EvidenceReference,
    ResearchBrief,
    normalize_evidence_url,
    render_research,
)


class ResearchEvidenceError(ValueError):
    """Research output or source receipt violates the evidence contract."""

    finish_reason: str | None = None


class ResearchEvidenceCollector:
    """Collect bounded references returned by successful read-tool calls."""

    max_references = 30
    max_excerpt_chars = 1600
    max_structured_result_chars = 256_000

    def __init__(self, references: Sequence[EvidenceReference] = ()) -> None:
        # Prior-run sources are deliberately not eligible in a new objective.
        # A follow-up must retrieve current evidence instead of inheriting a
        # stale, session-wide citation pool.
        self._references: dict[str, EvidenceReference] = {}
        self._fetch_aliases: dict[str, str] = {}
        self._overflow_urls: set[str] = set()
        self._overflow_fetch_requests: set[str] = set()

    @property
    def overflow_count(self) -> int:
        """Number of distinct source URLs withheld by the per-run cap."""
        return len(self._overflow_urls)

    @staticmethod
    def _normalize_url(value: Any) -> str:
        if not isinstance(value, str) or len(value) > 2048:
            return ""
        return normalize_evidence_url(value)

    @staticmethod
    def _source_id(identifier: Any, url: str) -> str:
        action_id = str(identifier or "").strip()
        if (
            action_id
            and len(action_id) <= 160
            and re.fullmatch(r"[A-Za-z0-9._:-]+", action_id)
        ):
            url_digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
            return f"action:{action_id}:{url_digest}"
        digest_input = f"{action_id}\0{url}" if action_id else url
        digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
        return ("result-sha256:" if action_id else "url-sha256:") + digest

    async def observe(
        self,
        _context: Any,
        tool_name: str,
        args: dict[str, Any],
        content: str,
    ) -> None:
        """Record URLs and bounded excerpts from successful search/fetch results."""

        is_search = tool_name.endswith("__search")
        is_fetch = tool_name.endswith(("__fetch", "__get"))
        if not is_search and not is_fetch:
            return

        excerpt = content[: self.max_excerpt_chars]
        try:
            if len(content) > self.max_structured_result_chars:
                raise ValueError("structured result exceeds observation limit")
            decoded = json.loads(content)
        except (TypeError, ValueError):
            decoded = None
        values: list[dict[str, Any]] = []
        if isinstance(decoded, list):
            values = [item for item in decoded if isinstance(item, dict)]
        elif isinstance(decoded, dict):
            values = [decoded]
            for key in ("results", "organic", "organic_results"):
                nested = decoded.get(key)
                if isinstance(nested, list):
                    values = [item for item in nested if isinstance(item, dict)]
                    break

        # A search result's explicit link is a source receipt. URLs that happen
        # to appear in its snippet are not. Likewise, a fetched page proves
        # retrieval only for the requested URL; its body may mention links that
        # were never fetched and must not become citable evidence.
        requested_url = self._normalize_url(args.get("url"))
        for item in values:
            item_url = self._normalize_url(item.get("link") or item.get("url"))
            if not item_url or not is_search:
                continue
            source_id = self._source_id(item.get("id"), item_url)
            snippet = str(item.get("snippet") or item.get("content") or "")
            ref = EvidenceReference(
                source_id=source_id,
                url=item_url,
                title=str(item.get("title") or "")[:512],
                excerpt=(snippet or content)[: self.max_excerpt_chars],
                provenance="search_snippet",
                observed_at=datetime.now(timezone.utc),
            )
            if (
                source_id in self._references
                or len(self._references) < self.max_references
            ):
                self._references[source_id] = ref
            else:
                self._overflow_urls.add(item_url)

        # Fetch provenance comes from the Action's typed receipt, never text
        # markers that could be forged by a page body or another Action.
        tool_metadata = getattr(content, "tool_result_metadata", {})
        fetch_result = (
            tool_metadata.get("web_fetch_result", {})
            if isinstance(tool_metadata, dict)
            else {}
        )
        if is_fetch and isinstance(fetch_result, dict):
            final_url = self._normalize_url(fetch_result.get("final_url"))
            receipt_requested = self._normalize_url(fetch_result.get("requested_url"))
            content_type = str(fetch_result.get("content_type") or "").lower()
            source_line = next(
                (
                    line[len("# Source: ") :].strip()
                    for line in content.splitlines()[:5]
                    if line.startswith("# Source: ")
                ),
                "",
            )
            rendered_source_url = self._normalize_url(source_line)
            supported_content_type = not content_type or content_type in {
                "text/html",
                "application/xhtml",
                "application/xhtml+xml",
                "text/plain",
                "application/json",
                "text/markdown",
            }
            successful = (
                fetch_result.get("outcome") == "success"
                and fetch_result.get("http_status") == 200
                and receipt_requested == requested_url
                and bool(final_url)
                and rendered_source_url == final_url
                and supported_content_type
            )
            if successful:
                title_line = next(
                    (
                        line
                        for line in content.splitlines()[:5]
                        if line.startswith("# Title: ")
                    ),
                    "",
                )
                ref = EvidenceReference(
                    source_id=self._source_id("", final_url),
                    url=final_url,
                    title=title_line.removeprefix("# Title: ")[:512],
                    excerpt=excerpt[: self.max_excerpt_chars],
                    provenance="fetched_page",
                    observed_at=datetime.now(timezone.utc),
                )
                if (
                    ref.source_id in self._references
                    or len(self._references) < self.max_references
                ):
                    self._references[ref.source_id] = ref
                    self._fetch_aliases[requested_url] = ref.source_id
                else:
                    self._overflow_urls.add(final_url)
                    self._overflow_fetch_requests.add(requested_url)

    def snapshot(self) -> tuple[EvidenceReference, ...]:
        """Return bounded references in deterministic order."""

        return tuple(self._references[key] for key in sorted(self._references))

    def annotate_result(
        self,
        tool_name: str,
        args: dict[str, Any],
        content: str,
    ) -> str:
        """Expose host-assigned source IDs alongside full Action results."""
        if tool_name.endswith("__search"):
            if len(content) > self.max_structured_result_chars:
                return content
            try:
                decoded = json.loads(content)
            except (TypeError, ValueError):
                return content
            if isinstance(decoded, list):
                self._annotate_search_items(decoded)
            elif isinstance(decoded, dict):
                for key in ("results", "organic", "organic_results"):
                    nested = decoded.get(key)
                    if isinstance(nested, list):
                        self._annotate_search_items(nested)
                        break
                if self._overflow_urls:
                    decoded["pilot_evidence_limit_reached"] = True
                    decoded["pilot_evidence_omitted_count"] = self.overflow_count
            return json.dumps(decoded, ensure_ascii=False, separators=(",", ":"))
        elif tool_name.endswith(("__fetch", "__get")):
            requested = self._normalize_url(args.get("url"))
            if not requested:
                return self._sanitize_fetch_source_marker(content)
            content = self._sanitize_fetch_source_marker(content)
            reference = next(
                (
                    item
                    for source_id, item in self._references.items()
                    if self._fetch_aliases.get(requested) == source_id
                ),
                None,
            )
            if reference is not None:
                return content + f"\n\nObserved source ID: {reference.source_id}"
            if requested in self._overflow_fetch_requests:
                return (
                    content + "\n\nThis page was fetched but not retained as evidence: "
                    "the per-run source limit has been reached. Do not cite it."
                )
        return content

    @classmethod
    def _sanitize_fetch_source_marker(cls, content: str) -> str:
        """Remove URL credentials from the host-generated source header."""
        lines = content.splitlines()
        for index, line in enumerate(lines[:5]):
            if line.startswith("# Source: "):
                source_url = line[len("# Source: ") :].strip()
                normalized = cls._normalize_url(source_url)
                lines[index] = (
                    f"# Source: {normalized}"
                    if normalized
                    else "# Source: [unsafe URL removed]"
                )
                break
        return "\n".join(lines)

    def _annotate_search_items(self, values: list[Any]) -> None:
        for item in values:
            if not isinstance(item, dict):
                continue
            for key in ("link", "url"):
                raw_url = item.get(key)
                if not isinstance(raw_url, str):
                    continue
                safe_url = self._normalize_url(raw_url)
                if safe_url:
                    item[key] = safe_url
                else:
                    item.pop(key, None)
                    item["pilot_source_unavailable"] = (
                        "unsafe source URL removed; do not cite"
                    )
            url = self._normalize_url(item.get("link") or item.get("url"))
            reference = next(
                (
                    candidate
                    for candidate in self._references.values()
                    if candidate.url == url
                ),
                None,
            )
            if reference is not None:
                item["pilot_source_id"] = reference.source_id
            elif url in self._overflow_urls:
                item["pilot_source_unavailable"] = (
                    "per-run evidence source limit reached; do not cite"
                )

    def validate(self, output: Any) -> None:
        """Require each claim to quote a fresh, successfully fetched source."""

        if isinstance(output, ResearchBrief):
            known = set(self._references)
            requested = {
                source_id
                for finding in output.findings
                for source_id in finding.source_ids
            }
            missing = requested - known
            if missing:
                raise ResearchEvidenceError(
                    "research output cited sources not returned by an Action: "
                    + ", ".join(sorted(missing)[:5])
                )
            unusable = sorted(
                source_id
                for source_id in requested
                if not self._references[source_id].url
            )
            if unusable:
                raise ResearchEvidenceError(
                    "research output cited Action results without a source URL: "
                    + ", ".join(unusable[:5])
                )
            try:
                render_research(output, self.snapshot())
            except ValueError as exc:
                raise ResearchEvidenceError(str(exc)) from exc
        else:
            # The current capability pilot is the evidence-backed research
            # driver. Keep the legacy union only for reading prior snapshots;
            # never accept source-free output from a fresh research run.
            raise ResearchEvidenceError(
                "research pilot requires evidence-backed ResearchBrief output"
            )
