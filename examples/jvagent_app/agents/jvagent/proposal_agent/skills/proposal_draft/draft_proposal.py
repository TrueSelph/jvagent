"""Generate a structured proposal draft from transcript analysis and specimens."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "proposal_draft__generate_draft",
        "description": (
            "Generate a structured DraftProposal based on transcript analysis, "
            "retrieved specimen proposals, template, and writing guide. "
            "The LLM should call this with the analysis and reference materials, "
            "and the tool resolves a LanguageModelAction for generation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "client_name": {
                    "type": "string",
                    "description": "Client company or individual name",
                },
                "project_title": {
                    "type": "string",
                    "description": "Descriptive project title",
                },
                "transcript_analysis": {
                    "type": "object",
                    "description": "Structured analysis of the client transcript",
                    "properties": {
                        "needs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Key client needs identified",
                        },
                        "scope": {
                            "type": "string",
                            "description": "Description of the project scope",
                        },
                        "timeline": {
                            "type": "string",
                            "description": "Desired timeline or deadline mentioned",
                        },
                        "budget": {
                            "type": "string",
                            "description": "Budget range if mentioned (otherwise 'Not specified')",
                        },
                        "decision_makers": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Key decision-makers identified",
                        },
                        "competitors_mentioned": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Competitors or alternatives mentioned",
                        },
                        "win_themes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Key themes to emphasize to win the deal",
                        },
                        "uncertainties": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Points that are unclear and need REVIEW markers",
                        },
                    },
                    "required": ["needs", "scope"],
                },
                "template": {
                    "type": "string",
                    "description": "The proposal template text (template.md)",
                },
                "guide": {
                    "type": "string",
                    "description": "The proposal writing guide text (guide.md)",
                },
                "specimens": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Full text of selected specimen proposals (up to 3)",
                },
            },
            "required": ["client_name", "project_title", "transcript_analysis"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Generate a draft proposal via an LLM call through LanguageModelAction."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    # Get the language model
    model_action = await resolver.resolve("LanguageModelAction")
    if model_action is None:
        # Fallback: try common model action types
        for model_type in ("OpenAILanguageModelAction", "AnthropicLanguageModelAction"):
            model_action = await resolver.resolve(model_type)
            if model_action:
                break

    if model_action is None:
        return {
            "error": "No LanguageModelAction available. Cannot generate draft.",
            "instruction": "The LLM should generate the draft directly using its own capabilities.",
        }

    client_name = arguments.get("client_name", "Client")
    project_title = arguments.get("project_title", "Project")
    analysis = arguments.get("transcript_analysis", {})
    template = arguments.get("template", "")
    guide = arguments.get("guide", "")
    specimens = arguments.get("specimens", [])

    # Build the generation prompt
    system_prompt = (
        "You are a senior proposal writer for a technology services company. "
        "You write persuasive, clear, and well-structured proposals.\n\n"
    )

    if template:
        system_prompt += f"=== PROPOSAL TEMPLATE ===\n{template}\n\n"
    else:
        system_prompt += (
            "=== PROPOSAL TEMPLATE ===\n"
            "Use this structure:\n"
            "1. Executive Summary (2-3 paragraphs)\n"
            "2. Understanding of Your Needs (restate + validate)\n"
            "3. Our Approach (technical / strategic)\n"
            "4. Scope of Work (deliverables, phases, out-of-scope)\n"
            "5. Timeline (estimated milestones)\n"
            "6. Investment (placeholder — '[PRICING PLACEHOLDER]')\n"
            "7. Why Us / Differentiators\n"
            "8. Next Steps\n\n"
        )

    if guide:
        system_prompt += f"=== WRITING GUIDE ===\n{guide}\n\n"

    if specimens:
        system_prompt += "=== SPECIMEN PROPOSALS (for tone and structure reference) ===\n"
        for i, specimen in enumerate(specimens, 1):
            system_prompt += f"--- Specimen {i} ---\n{specimen}\n\n"
        system_prompt += (
            "Use these specimens to inform tone, structure, and win themes. "
            "Do NOT copy verbatim — adapt to the specific client context.\n\n"
        )

    system_prompt += (
        "=== WRITING GUIDELINES ===\n"
        "- Be specific about the client's stated needs; reference the transcript.\n"
        "- Use confident, forward-looking language ('we will' not 'we can').\n"
        "- Quantify wherever possible.\n"
        "- For uncertain claims or items needing client review, use [REVIEW: ...] markers.\n"
        "- Do NOT fabricate information. If the transcript lacks detail, note it with [REVIEW: ...].\n"
        "- Write the Investment section as '[PRICING PLACEHOLDER]' — pricing is filled later.\n"
    )

    user_prompt = (
        f"Generate a professional proposal for {client_name}.\n\n"
        f"Project: {project_title}\n\n"
        f"=== TRANSCRIPT ANALYSIS ===\n"
        f"Needs: {', '.join(analysis.get('needs', []))}\n"
        f"Scope: {analysis.get('scope', 'Not specified')}\n"
        f"Timeline: {analysis.get('timeline', 'Not specified')}\n"
        f"Budget: {analysis.get('budget', 'Not specified')}\n"
        f"Decision-makers: {', '.join(analysis.get('decision_makers', []))}\n"
        f"Competitors: {', '.join(analysis.get('competitors_mentioned', []))}\n"
        f"Win themes: {', '.join(analysis.get('win_themes', []))}\n"
        f"Uncertainties: {', '.join(analysis.get('uncertainties', []))}\n\n"
        "Return the complete proposal text following the template structure.\n"
        "Use [REVIEW: ...] markers for any uncertain items.\n"
        "Use [PRICING PLACEHOLDER] for the Investment section.\n"
    )

    # Try to use the model action
    try:
        response = await model_action.generate(
            prompt=user_prompt,
            system_prompt=system_prompt,
            max_tokens=4096,
        )
        draft_text = response.get("text", "") if isinstance(response, dict) else str(response)
    except Exception as e:
        return {
            "error": f"LanguageModelAction failed: {e}",
            "instruction": "The LLM should generate the draft directly.",
            "client_name": client_name,
            "project_title": project_title,
            "analysis": analysis,
        }

    return {
        "client_name": client_name,
        "project_title": project_title,
        "draft_text": draft_text,
        "specimens_used": len(specimens),
        "status": "draft",
        "next_step": "Review the draft, then pass to the pricing skill for assessment.",
    }
