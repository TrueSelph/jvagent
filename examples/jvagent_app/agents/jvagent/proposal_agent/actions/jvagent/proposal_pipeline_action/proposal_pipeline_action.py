"""ProposalPipelineAction — proposal-generation pipeline configuration carrier.

Plain :class:`Action` subclass that holds proposal-specific runtime
configuration (specimen corpus, output dir, LaTeX command, branding,
Google Docs template). The cockpit's agentic loop drives the actual
pipeline; this action exposes the configuration both as Python attributes
(read by skill scripts that look up peer actions on the agent) and as
cockpit tools (so the model can inspect the active pipeline configuration
on demand).
"""

import json
import logging
from typing import Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.tooling.tool import Tool

logger = logging.getLogger(__name__)


class ProposalPipelineAction(Action):
    """Holds runtime configuration for the proposal-generation pipeline.

    Skill scripts resolve this action via ``Action.get_action(
    "ProposalPipelineAction")`` and read attributes such as
    ``specimens_path`` directly. The cockpit also surfaces the
    configuration through ``proposal_pipeline__get_config`` so the model
    can introspect it.
    """

    description: str = attribute(
        default=(
            "Proposal-generation pipeline configuration. Owns specimen path, "
            "output directory, LaTeX command, branding, and Google Docs "
            "template ID. The cockpit drives the pipeline; this action is "
            "queried by skill scripts and cockpit tools."
        ),
        description="Action description",
    )

    specimens_path: Optional[str] = attribute(
        default=None,
        description="Path to the specimen proposal corpus directory",
    )

    output_dir: Optional[str] = attribute(
        default=None,
        description="Output directory for generated artifacts (Markdown files, PDFs)",
    )

    google_docs_template_id: Optional[str] = attribute(
        default=None,
        description="Optional Google Docs template ID used for branded proposal authoring",
    )

    drive_output_folder_id: Optional[str] = attribute(
        default=None,
        description="Optional Google Drive folder for proposal artifacts",
    )

    brand_logo_path: Optional[str] = attribute(
        default=None,
        description="Optional path/URL to brand logo for rendered outputs",
    )

    brand_primary_color: str = attribute(
        default="#1a237e",
        description="Primary brand color for proposal rendering",
    )

    brand_accent_color: str = attribute(
        default="#0d47a1",
        description="Accent brand color for proposal rendering",
    )

    company_letterhead: Optional[str] = attribute(
        default=None,
        description="Optional letterhead text block for cover pages",
    )

    latex_command: str = attribute(
        default="xelatex",
        description=(
            "LaTeX command for PDF generation "
            "(xelatex, pdflatex, lualatex)."
        ),
    )

    def _config_payload(self) -> Dict[str, Any]:
        return {
            "specimens_path": self.specimens_path,
            "output_dir": self.output_dir,
            "google_docs_template_id": self.google_docs_template_id,
            "drive_output_folder_id": self.drive_output_folder_id,
            "brand_logo_path": self.brand_logo_path,
            "brand_primary_color": self.brand_primary_color,
            "brand_accent_color": self.brand_accent_color,
            "company_letterhead": self.company_letterhead,
            "latex_command": self.latex_command,
        }

    async def get_tools(self) -> List[Tool]:
        return [
            Tool(
                name="proposal_pipeline__get_config",
                description=(
                    "Return the active proposal pipeline configuration "
                    "(specimens path, output directory, branding, LaTeX "
                    "command, Google Docs template ID)."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
                execute=self._execute_get_config,
            ),
        ]

    async def _execute_get_config(self) -> str:
        return json.dumps(self._config_payload(), indent=2)
