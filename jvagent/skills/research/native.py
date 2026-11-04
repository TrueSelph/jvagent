"""Research-only output policy for native Pydantic execution."""

from typing import Any

from pydantic_ai import ModelRetry

from jvagent.skills.research.contracts import ResearchBrief, render_research
from jvagent.skills.research.evidence import (
    ResearchEvidenceCollector,
    ResearchEvidenceError,
)
from jvagent.tooling.tool_result import ToolResult, ToolResultText


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

    def finish(self, brief: ResearchBrief) -> str:
        """Validate a structured research answer and render host-resolved citations."""
        try:
            self.evidence.validate(brief)
            return render_research(brief, self.evidence.snapshot())
        except ResearchEvidenceError as exc:
            raise ModelRetry(str(exc)) from exc
