"""Skill-owned output contracts installed by the host composition root."""

from typing import Any

SUPPORTED_OUTPUT_CONTRACTS = ("evidence_required",)


def output_policy(name: str) -> Any:
    """Create a policy whose run_scope isolates state; unknown contracts fail admission."""
    if not name:
        return None
    if name == "evidence_required":
        from jvagent.skills.research.native import ResearchOutputPolicy

        return ResearchOutputPolicy()
    raise ValueError(f"Unsupported output contract: {name}")
