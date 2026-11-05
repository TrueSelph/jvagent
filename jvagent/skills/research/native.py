"""Research-only output policy for native Pydantic execution."""

import contextlib
import contextvars
import json
from collections.abc import Iterator
from typing import Any

from pydantic import Field
from pydantic_ai import ModelRetry

from jvagent.skills.research.contracts import (
    ResearchBrief,
    ResearchFinding,
    render_research,
)
from jvagent.skills.research.evidence import (
    ResearchEvidenceCollector,
    ResearchEvidenceError,
)
from jvagent.tooling.tool_result import ToolResult, ToolResultText


class GroundedFinding(ResearchFinding):
    """Native output requires explicit support; archived pilot defaults remain readable."""

    supporting_source_id: str = Field(
        min_length=1,
        max_length=256,
        description="The observed fetched source ID supporting this claim; must be included in source_ids.",
    )
    supporting_quote: str = Field(
        min_length=1,
        max_length=1000,
        description="Copy a short contiguous substring from the evidence.excerpt field, preserving its punctuation. Do not insert ellipses or paraphrase.",
    )


class GroundedBrief(ResearchBrief):
    """Research output schema with required grounding fields."""

    findings: tuple[GroundedFinding, ...] = Field(min_length=1, max_length=50)


class ResearchOutputPolicy:
    """Observe host tool receipts and finish through a typed output function."""

    def __init__(self) -> None:
        self._evidence: contextvars.ContextVar[ResearchEvidenceCollector | None] = (
            contextvars.ContextVar("native_research_evidence", default=None)
        )

    @contextlib.contextmanager
    def run_scope(self) -> Iterator[None]:
        """Give each native run its own evidence, including reused child agents."""
        token = self._evidence.set(ResearchEvidenceCollector())
        try:
            yield
        finally:
            self._evidence.reset(token)

    @property
    def evidence(self) -> ResearchEvidenceCollector:
        evidence = self._evidence.get()
        if evidence is None:
            raise RuntimeError("Research evidence requires an active native run")
        return evidence

    async def observe(
        self, name: str, arguments: dict[str, Any], result: ToolResult
    ) -> str:
        """Preserve host metadata for validation and annotate model-visible sources."""
        if result.is_error:
            return str(result.content)
        content = ToolResultText(str(result.content), result.metadata)
        reference = await self.evidence.observe(None, name, arguments, content)
        annotated = self.evidence.annotate_result(name, arguments, content)
        if reference is not None:
            return json.dumps(
                {"evidence": reference.model_dump(mode="json"), "content": annotated},
                ensure_ascii=False,
            )
        return annotated

    def finish(self, brief: GroundedBrief) -> str:
        """Validate a structured research answer and render host-resolved citations."""
        try:
            self.evidence.validate(brief)
            return render_research(brief, self.evidence.snapshot())
        except ResearchEvidenceError as exc:
            raise ModelRetry(str(exc)) from exc
