"""Generate a structured proposal draft from transcript analysis and specimens."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List


REQUIRED_SECTIONS: List[str] = [
    "executive_summary",
    "understanding_of_needs",
    "core_deliverables",
    "operational_costs",
    "technical_approach",
    "client_responsibilities",
    "value_summary",
    "next_steps",
    "requirements_annex",
]


def _extract_section(markdown: str, heading: str) -> str:
    lines = markdown.splitlines()
    capture = False
    out: List[str] = []
    needle = heading.strip().lower()
    for line in lines:
        normalized = line.strip().lower()
        if normalized.startswith("#") and needle in normalized:
            capture = True
            continue
        if capture and normalized.startswith("#"):
            break
        if capture:
            out.append(line)
    return "\n".join(out).strip()


def _render_fallback_draft(
    client_name: str,
    project_title: str,
    company_name: str,
    analysis: Dict[str, Any],
    version: str,
    validity_days: int,
) -> str:
    needs = analysis.get("needs", [])
    scope = analysis.get("scope", "Scope to be confirmed in workshop.")
    timeline = analysis.get("timeline", "[REVIEW: confirm timeline with client]")
    budget = analysis.get("budget", "[REVIEW: budget not specified]")
    win_themes = analysis.get("win_themes", [])
    uncertainties = analysis.get("uncertainties", [])
    today = datetime.now().strftime("%B %d, %Y")
    needs_list = "\n".join(f"- {n}" for n in needs) or "- [REVIEW: key needs to confirm]"
    win_themes_list = "\n".join(f"- {w}" for w in win_themes) or "- Delivery reliability\n- Domain expertise"
    uncertainty_markers = "\n".join(f"- [REVIEW: {u}]" for u in uncertainties)
    if not uncertainty_markers:
        uncertainty_markers = "- [REVIEW: confirm assumptions and acceptance criteria]"
    return (
        f"# {project_title}\n\n"
        f"**Prepared for:** {client_name}  \n"
        f"**Prepared by:** {company_name}  \n"
        f"**Date:** {today} | **Version:** {version}  \n"
        f"**Proposal validity:** {validity_days} days from the date above.\n\n"
        "## I. Executive Summary\n\n"
        f"{client_name} needs a unified delivery approach to eliminate operational friction and improve execution confidence. "
        "This proposal presents an implementation framework that aligns process, platform, and governance while preserving flexibility for staged rollout.\n\n"
        "## II. Understanding of Your Needs\n\n"
        f"### Stated Priorities\n{needs_list}\n\n"
        f"### Scope Context\n{scope}\n\n"
        "## III. Core Deliverables, Hours, Timeframes & Cost\n\n"
        "| Deliverable | Description | Hrs | Est. Time (weeks) | Cost |\n"
        "| :---- | :---- | :---- | :---- | :---- |\n"
        "| Discovery and architecture | Workshops, architecture, implementation plan | [REVIEW: hrs] | [REVIEW: weeks] | [REVIEW: cost] |\n"
        "| Build and integration | Core product delivery and system integration | [REVIEW: hrs] | [REVIEW: weeks] | [REVIEW: cost] |\n"
        "| Testing and launch readiness | QA, deployment readiness, training | [REVIEW: hrs] | [REVIEW: weeks] | [REVIEW: cost] |\n\n"
        "## IV. Recommended Operational Costs\n\n"
        "| Item | Description | Monthly Cost |\n"
        "| :---- | :---- | :---- |\n"
        "| Hosting & infrastructure | Cloud, storage, monitoring | [REVIEW: monthly cost] |\n"
        "| AI and third-party services | Model/API usage and managed integrations | [REVIEW: monthly cost] |\n\n"
        "## V. Technical Approach & Rationale\n\n"
        "- Single source of truth with auditable workflows.\n"
        "- Role-based operations and measurable service levels.\n"
        f"- Delivery timeline target: {timeline}.\n"
        f"- Budget context: {budget}.\n\n"
        "## VI. Client Responsibilities\n\n"
        "- Assign decision-maker and review team.\n"
        "- Provide access, policy, and content artifacts.\n"
        "- Complete review cycles within agreed turnaround windows.\n\n"
        "## VII. Value Summary\n\n"
        f"{company_name} brings execution speed, accountability, and long-term maintainability to this engagement.\n\n"
        "### Win Themes\n"
        f"{win_themes_list}\n\n"
        "## VIII. Next Steps\n\n"
        "1. Review proposal and confirm scope options.\n"
        "2. Hold kickoff workshop and finalize execution plan.\n"
        "3. Approve investment and start implementation.\n\n"
        "## IX. Requirements Analysis Reference (Annex)\n\n"
        "| Stated Need / Pain Point | Proposed Solution | How It Addresses the Need |\n"
        "| :---- | :---- | :---- |\n"
        "| [REVIEW: add need] | [REVIEW: add deliverable] | [REVIEW: add mapping] |\n\n"
        "## X. Investment\n\n"
        "[PRICING PLACEHOLDER]\n\n"
        "## XI. Open Review Items\n\n"
        f"{uncertainty_markers}\n"
    )


def _quality_checks(proposal_markdown: str) -> Dict[str, Any]:
    missing = [name for name in REQUIRED_SECTIONS if not _section_for_name(name, proposal_markdown)]
    has_pricing_placeholder = "[PRICING PLACEHOLDER]" in proposal_markdown
    unresolved_review_markers = proposal_markdown.count("[REVIEW:")
    return {
        "missing_sections": missing,
        "has_pricing_placeholder": has_pricing_placeholder,
        "unresolved_review_markers": unresolved_review_markers,
        "ready_for_pricing": has_pricing_placeholder and not missing,
    }


def _section_for_name(name: str, markdown: str) -> str:
    title_lookup = {
        "executive_summary": "executive summary",
        "understanding_of_needs": "understanding of your needs",
        "core_deliverables": "core deliverables",
        "operational_costs": "operational costs",
        "technical_approach": "technical approach",
        "client_responsibilities": "client responsibilities",
        "value_summary": "value summary",
        "next_steps": "next steps",
        "requirements_annex": "requirements analysis",
    }
    return _extract_section(markdown, title_lookup[name])


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "proposal_draft__generate_draft",
        "description": (
            "Generate a structured proposal package using transcript analysis, "
            "specimens, and writing guidance. Returns both rendered markdown and "
            "a machine-readable proposal_state object for downstream pricing, "
            "authoring, revisions, and PDF workflows."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "client_name": {"type": "string", "description": "Client company name."},
                "project_title": {"type": "string", "description": "Proposal/project title."},
                "company_name": {
                    "type": "string",
                    "description": "Author organization name.",
                },
                "proposal_version": {
                    "type": "string",
                    "description": "Version label (default: 1.0 Draft).",
                },
                "validity_days": {
                    "type": "integer",
                    "description": "Commercial validity window in days (default: 30).",
                },
                "transcript_analysis": {
                    "type": "object",
                    "description": "Structured analysis extracted from transcript/RFP.",
                    "properties": {
                        "needs": {"type": "array", "items": {"type": "string"}},
                        "scope": {"type": "string"},
                        "timeline": {"type": "string"},
                        "budget": {"type": "string"},
                        "decision_makers": {"type": "array", "items": {"type": "string"}},
                        "competitors_mentioned": {"type": "array", "items": {"type": "string"}},
                        "win_themes": {"type": "array", "items": {"type": "string"}},
                        "uncertainties": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["needs", "scope"],
                },
                "template": {"type": "string"},
                "guide": {"type": "string"},
                "specimens": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Selected specimen proposal full texts.",
                },
            },
            "required": ["client_name", "project_title", "transcript_analysis"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Generate draft content and a structured proposal state payload."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    client_name = arguments.get("client_name", "Client")
    project_title = arguments.get("project_title", "Project Proposal")
    company_name = arguments.get("company_name", "Proposal Team")
    analysis = arguments.get("transcript_analysis", {})
    template = arguments.get("template", "")
    guide = arguments.get("guide", "")
    specimens = arguments.get("specimens", [])
    proposal_version = arguments.get("proposal_version", "1.0 Draft")
    validity_days = int(arguments.get("validity_days", 30))

    model_action = await resolver.resolve("LanguageModelAction")
    if model_action is None:
        for model_type in (
            "OpenAILanguageModelAction",
            "AnthropicLanguageModelAction",
            "OllamaLanguageModelAction",
        ):
            model_action = await resolver.resolve(model_type)
            if model_action:
                break

    rendered = ""
    model_used = False
    if model_action is not None:
        system_prompt = (
            "You are a principal proposal strategist. Produce enterprise-grade proposals "
            "with strong formatting, quantified detail, clear assumptions, and explicit "
            "[REVIEW: ...] markers for uncertain points."
        )
        if template:
            system_prompt += f"\n\n=== TEMPLATE ===\n{template}"
        if guide:
            system_prompt += f"\n\n=== WRITING GUIDE ===\n{guide}"
        if specimens:
            system_prompt += "\n\n=== SPECIMENS (style reference only) ===\n"
            for idx, specimen in enumerate(specimens, 1):
                system_prompt += f"\n--- Specimen {idx} ---\n{specimen}\n"
        user_prompt = (
            f"Draft a complete proposal for {client_name} titled '{project_title}'.\n"
            f"Author organization: {company_name}\n"
            f"Version: {proposal_version}\n"
            f"Validity days: {validity_days}\n"
            f"Needs: {analysis.get('needs', [])}\n"
            f"Scope: {analysis.get('scope', 'Not specified')}\n"
            f"Timeline: {analysis.get('timeline', 'Not specified')}\n"
            f"Budget: {analysis.get('budget', 'Not specified')}\n"
            f"Decision-makers: {analysis.get('decision_makers', [])}\n"
            f"Win themes: {analysis.get('win_themes', [])}\n"
            f"Uncertainties: {analysis.get('uncertainties', [])}\n\n"
            "Required sections:\n"
            "I. Executive Summary\n"
            "II. Understanding of Your Needs\n"
            "III. Core Deliverables, Hours, Timeframes & Cost\n"
            "IV. Recommended Operational Costs\n"
            "V. Technical Approach & Rationale\n"
            "VI. Client Responsibilities\n"
            "VII. Value Summary\n"
            "VIII. Next Steps\n"
            "IX. Requirements Analysis Reference (Annex)\n"
            "X. Investment\n\n"
            "Keep [PRICING PLACEHOLDER] as the full content of Investment."
        )
        try:
            response = await model_action.generate(
                prompt=user_prompt,
                system_prompt=system_prompt,
                max_tokens=8192,
            )
            rendered = response.get("text", "") if isinstance(response, dict) else str(response)
            model_used = bool(rendered.strip())
        except Exception:
            rendered = ""

    if not rendered.strip():
        rendered = _render_fallback_draft(
            client_name=client_name,
            project_title=project_title,
            company_name=company_name,
            analysis=analysis,
            version=proposal_version,
            validity_days=validity_days,
        )

    quality = _quality_checks(rendered)
    proposal_state = {
        "client": client_name,
        "project_title": project_title,
        "prepared_for": client_name,
        "prepared_by": company_name,
        "version": proposal_version,
        "validity_days": validity_days,
        "executive_summary": _section_for_name("executive_summary", rendered),
        "understanding_of_needs": _section_for_name("understanding_of_needs", rendered),
        "core_deliverables": _section_for_name("core_deliverables", rendered),
        "operational_costs": _section_for_name("operational_costs", rendered),
        "technical_approach": _section_for_name("technical_approach", rendered),
        "client_responsibilities": _section_for_name("client_responsibilities", rendered),
        "value_summary": _section_for_name("value_summary", rendered),
        "next_steps": _section_for_name("next_steps", rendered),
        "annexes": _section_for_name("requirements_annex", rendered),
        "pricing_tables": [],
        "revision_markers": analysis.get("uncertainties", []),
        "source_artifact": "markdown_draft",
    }

    return {
        "client_name": client_name,
        "project_title": project_title,
        "draft_text": rendered,
        "rendered_markdown": rendered,
        "proposal_state": proposal_state,
        "quality": quality,
        "specimens_used": len(specimens),
        "status": "draft",
        "model_used": model_used,
        "next_step": "Run quality review and pricing insertion before authoring.",
    }
