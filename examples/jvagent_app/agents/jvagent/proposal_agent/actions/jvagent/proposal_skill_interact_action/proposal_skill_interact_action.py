"""ProposalSkillInteractAction — pipeline-hub for proposal generation.

Extends SkillInteractAction with proposal-specific configuration:
specimen corpus path, output directory, and LaTeX command.

All agentic loop behavior (think-act-observe, tool execution, skill loading,
streaming, grounding, stuck detection) is inherited from SkillInteractAction.

Pipeline config attributes are read at runtime by skill tools via
``visitor._current_action`` (set by InteractWalker before execution).
"""

import logging
from typing import Optional

from jvspatial.core.annotations import attribute

from jvagent.action.skill.skill_interact_action import SkillInteractAction

logger = logging.getLogger(__name__)


class ProposalSkillInteractAction(SkillInteractAction):
    """Interact-subsystem adapter for the proposal generation pipeline.

    Extends ``SkillInteractAction`` with proposal-specific pipeline
    configuration.  All agentic loop logic is inherited.

    Attributes:
        specimens_path: Path to the specimen proposal corpus directory.
        output_dir: Output directory for generated artifacts (e.g. Markdown
            files, PDFs).
        latex_command: LaTeX command to use for PDF generation (e.g.
            ``xelatex``, ``pdflatex``, ``lualatex``).
    """

    description: str = attribute(
        default=(
            "Proposal generation pipeline: draft, price, author, review, and "
            "produce a final PDF. Uses specimen-based RAG, configurable pricing "
            "rubrics, Google Docs or Markdown authoring, and LaTeX PDF generation."
        ),
        description="Action description",
    )

    specimens_path: Optional[str] = attribute(
        default=None,
        description="Path to the specimen proposal corpus directory",
    )

    output_dir: Optional[str] = attribute(
        default=None,
        description="Output directory for generated artifacts (e.g. Markdown files, PDFs)",
    )

    latex_command: str = attribute(
        default="xelatex",
        description=(
            "LaTeX command for PDF generation. "
            "One of: xelatex, pdflatex, lualatex."
        ),
    )
