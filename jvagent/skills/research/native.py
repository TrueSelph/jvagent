"""Research-only output policy for native Pydantic execution."""

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
        description="An exact quote from that source's observed excerpt supporting the claim.",
    )


class GroundedBrief(ResearchBrief):
    """Research output schema with required grounding fields."""

    findings: tuple[GroundedFinding, ...] = Field(min_length=1, max_length=50)


class ResearchOutputPolicy:
    """Observe host tool receipts and finish through a typed output function."""

    def __init__(self) -> None:
        self.evidence = ResearchEvidenceCollector()

    async def observe(
        self, name: str, arguments: dict[str, Any], result: ToolResult
    ) -> str:
        """Preserve host metadata for validation and annotate model-visible sources."""
        if result.is_error:
            return str(result.content)
        content = ToolResultText(str(result.content), result.metadata)
        await self.evidence.observe(None, name, arguments, content)
        return self.evidence.annotate_result(name, arguments, content)

    def finish(self, brief: GroundedBrief) -> str:
        """Validate a structured research answer and render host-resolved citations."""
        try:
            self.evidence.validate(brief)
            return render_research(brief, self.evidence.snapshot())
        except ResearchEvidenceError as exc:
            raise ModelRetry(str(exc)) from exc
