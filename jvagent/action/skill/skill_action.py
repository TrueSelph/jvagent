"""SkillAction: defines a skill (prompt template + tool bindings) for ThinkingInteractAction.

SkillAction is an Action node (not InteractAction) that defines prompt templates
and tool bindings. It is referenced by name from ThinkingInteractAction.skill.
Skills are defined in agent.yaml like any other action and persisted in the graph.
"""

import fnmatch
import logging
from typing import Any, Dict, List, Optional, Set

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.action.model.language.tools import ToolDefinition

logger = logging.getLogger(__name__)


class SkillAction(Action):
    """Action defining a skill (prompt template + tool bindings) for thinking agents.

    Skills define the system prompt, tool requirements, and behavioral parameters
    for a ThinkingInteractAction's agentic loop. They are referenced by skill_name
    from ThinkingInteractAction and used to compose prompts, filter tools, and
    override model parameters.

    Attributes:
        skill_name: Unique skill identifier.
        system_prompt_template: System prompt with {variables}.
        prompt_variables: Default variable values for the template.
        prepend_to_utterance: Optional prefix added before user utterance.
        append_to_utterance: Optional suffix added after user utterance.
        required_tools: Tool names that MUST be available for this skill.
        optional_tools: Tool names that are nice-to-have.
        tool_overrides: Override tool descriptions per-skill context.
        allowed_tool_patterns: Glob patterns for tool names this skill can use.
        denied_tool_patterns: Glob patterns for tools this skill must never use.
        max_iterations: Override ThinkingInteractAction.max_iterations.
        max_duration_seconds: Override ThinkingInteractAction timeout.
        thinking_budget_tokens: Override ThinkingInteractAction thinking budget.
        model: Override model name.
        model_temperature: Override model temperature.
        model_max_tokens: Override max tokens.
    """

    skill_name: str = attribute(
        default="",
        description="Unique skill identifier",
    )
    system_prompt_template: str = attribute(
        default="",
        description="System prompt template with {variables}",
    )
    prompt_variables: Dict[str, str] = attribute(
        default_factory=dict,
        description="Default variable values for the template",
    )
    prepend_to_utterance: str = attribute(
        default="",
        description="Optional prefix added before user utterance",
    )
    append_to_utterance: str = attribute(
        default="",
        description="Optional suffix added after user utterance",
    )
    required_tools: List[str] = attribute(
        default_factory=list,
        description="Tool names that MUST be available for this skill",
    )
    optional_tools: List[str] = attribute(
        default_factory=list,
        description="Tool names that are nice-to-have",
    )
    tool_overrides: Dict[str, Dict[str, str]] = attribute(
        default_factory=dict,
        description="Override tool descriptions per-skill context. Key=tool_name, value={description: ...}",
    )
    allowed_tool_patterns: List[str] = attribute(
        default_factory=list,
        description="Glob patterns for tool names this skill can use",
    )
    denied_tool_patterns: List[str] = attribute(
        default_factory=list,
        description="Glob patterns for tools this skill must never use",
    )
    max_iterations: Optional[int] = attribute(
        default=None,
        description="Override ThinkingInteractAction.max_iterations",
    )
    max_duration_seconds: Optional[float] = attribute(
        default=None,
        description="Override ThinkingInteractAction timeout",
    )
    thinking_budget_tokens: Optional[int] = attribute(
        default=None,
        description="Override ThinkingInteractAction thinking budget",
    )
    model: Optional[str] = attribute(
        default=None,
        description="Override model name",
    )
    model_temperature: Optional[float] = attribute(
        default=None,
        description="Override model temperature",
    )
    model_max_tokens: Optional[int] = attribute(
        default=None,
        description="Override max tokens",
    )

    async def on_register(self) -> None:
        """Set default label from skill_name if label is empty."""
        await super().on_register()
        if not (getattr(self, "label", None) or "").strip() and self.skill_name:
            self.label = f"Skill ({self.skill_name})"

    def compose_system_prompt(self, variables: Optional[Dict[str, str]] = None) -> str:
        """Render system_prompt_template with merged variables.

        Args:
            variables: Optional override variables. Merged with
                self.prompt_variables (caller takes precedence).

        Returns:
            Rendered system prompt string.
        """
        merged = {**self.prompt_variables, **(variables or {})}
        try:
            return self.system_prompt_template.format(**merged)
        except KeyError as e:
            logger.warning(
                "SkillAction: missing variable %s in template for skill '%s'",
                e,
                self.skill_name,
            )
            return self.system_prompt_template

    def compose_utterance(self, raw_utterance: str) -> str:
        """Wrap user utterance with prepend/append and skill context.

        Args:
            raw_utterance: The original user utterance.

        Returns:
            Composed utterance string.
        """
        parts = []
        if self.prepend_to_utterance:
            parts.append(self.prepend_to_utterance)
        parts.append(raw_utterance)
        if self.append_to_utterance:
            parts.append(self.append_to_utterance)
        return "\n".join(parts)

    def get_tool_filter(
        self, available_tools: List[ToolDefinition]
    ) -> List[ToolDefinition]:
        """Filter available tools by allowed/denied patterns and required/optional lists.

        If allowed_tool_patterns is set, only matching tools are kept.
        Then denied_tool_patterns removes any matches.
        If neither is set, all available tools are returned.

        Tool descriptions are overridden per tool_overrides if present.

        Args:
            available_tools: All available ToolDefinition instances.

        Returns:
            Filtered list of ToolDefinition instances.
        """
        filtered = list(available_tools)
        tool_names = {t.name for t in filtered}

        # Apply allowed patterns (if set, only these pass)
        if self.allowed_tool_patterns:
            allowed_names = set()
            for pattern in self.allowed_tool_patterns:
                allowed_names.update(fnmatch.filter(tool_names, pattern))
            filtered = [t for t in filtered if t.name in allowed_names]
            tool_names = {t.name for t in filtered}

        # Apply denied patterns
        if self.denied_tool_patterns:
            denied_names = set()
            for pattern in self.denied_tool_patterns:
                denied_names.update(fnmatch.filter(tool_names, pattern))
            filtered = [t for t in filtered if t.name not in denied_names]

        # Apply description overrides
        for i, tool in enumerate(filtered):
            if tool.name in self.tool_overrides:
                override = self.tool_overrides[tool.name]
                new_desc = override.get("description", tool.description)
                filtered[i] = ToolDefinition(
                    name=tool.name,
                    description=new_desc,
                    parameters=tool.parameters,
                )

        return filtered

    def validate_tools_available(self, available_tool_names: Set[str]) -> List[str]:
        """Check that all required_tools are present.

        Args:
            available_tool_names: Set of tool names currently available.

        Returns:
            List of missing required tool names (empty if all present).
        """
        return [
            name for name in self.required_tools if name not in available_tool_names
        ]

    def get_model_overrides(self) -> Dict[str, Any]:
        """Return dict of non-None overrides for ThinkingInteractAction.

        Returns:
            Dict with any of: model, model_temperature, model_max_tokens,
            max_iterations, max_duration_seconds, thinking_budget_tokens.
        """
        overrides: Dict[str, Any] = {}
        if self.model is not None:
            overrides["model"] = self.model
        if self.model_temperature is not None:
            overrides["model_temperature"] = self.model_temperature
        if self.model_max_tokens is not None:
            overrides["model_max_tokens"] = self.model_max_tokens
        if self.max_iterations is not None:
            overrides["max_iterations"] = self.max_iterations
        if self.max_duration_seconds is not None:
            overrides["max_duration_seconds"] = self.max_duration_seconds
        if self.thinking_budget_tokens is not None:
            overrides["thinking_budget_tokens"] = self.thinking_budget_tokens
        return overrides

    async def healthcheck(self) -> bool:
        """Validate skill configuration."""
        if not self.skill_name:
            return False
        if not self.system_prompt_template:
            return False
        return True
