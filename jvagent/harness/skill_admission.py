"""JV skill declaration checks before official Harness skill loading."""

from pathlib import Path
from typing import Any, Sequence

import yaml


def admit_skill(
    directory: Path,
    *,
    available_actions: Sequence[str],
    available_tools: Sequence[str],
    channel: str,
    supported_output_contracts: Sequence[str] = (),
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate JV-owned prerequisites without interpreting the skill procedure."""
    if metadata is None:
        text = (directory / "SKILL.md").read_text()
        sections = text.split("---", 2)
        if not text.startswith("---\n") or len(sections) != 3:
            raise ValueError("Skill requires YAML frontmatter")
        metadata = yaml.safe_load(sections[1])
    if not isinstance(metadata, dict) or not isinstance(metadata.get("name"), str):
        raise ValueError("Skill requires a name")
    if metadata.get("spec", "jv") != "jv":
        raise ValueError("This skill specification requires a compatibility adapter")
    for field in (
        "task_lock",
        "task-lock",
        "requires_tasks",
        "requires-tasks",
        "extends",
        "access-action",
        "allowed-groups",
        "denied-groups",
    ):
        if metadata.get(field):
            raise ValueError(
                f"Skill declaration requires an unqualified adapter: {field}"
            )
    if (
        metadata.get("output-contract")
        and metadata["output-contract"] not in supported_output_contracts
    ):
        raise ValueError("An output-contract requires its qualified capability")

    def names(key: str) -> list[str]:
        value = metadata.get(key, [])
        if not isinstance(value, list) or any(
            not isinstance(v, str) or not v.strip() for v in value
        ):
            raise ValueError(f"{key} must be a list of nonempty names")
        return value

    required_actions = names("requires-actions")
    missing = sorted(set(required_actions) - set(available_actions))
    if missing:
        raise ValueError(f"Required Actions unavailable: {', '.join(missing)}")
    required_tools = names("requires-tools")
    if set(required_tools) - set(available_tools):
        raise ValueError("Required tools unavailable")
    allowed = names("allowed-tools")
    if any("*" in name or "?" in name for name in allowed):
        raise ValueError("Wildcard tool declarations require migration")
    if set(required_tools) - set(allowed):
        raise ValueError("Required tools must be permitted by allowed-tools")
    permitted_channels = names("allowed-channels")
    denied_channels = names("denied-channels")
    if channel in denied_channels or (
        permitted_channels and channel not in permitted_channels
    ):
        raise ValueError("Skill is unavailable on this channel")
    return metadata
