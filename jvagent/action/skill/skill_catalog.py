"""SkillCatalog: manages skill discovery, resolution, rendering, and validation.

Extracted from SkillInteractAction to isolate skill lifecycle concerns
from loop orchestration. Provides a single entry point for skill bundle
discovery, catalog rendering, activation validation, and response mode
resolution.
"""

import logging
from typing import Any, Dict, List, Optional, Set

from jvagent.action.skill.prompts import (
    GROUNDING_INSTRUCTION_TEMPLATE,
    READ_SKILL_RESULT_TEMPLATE,
    SKILL_ACTIVATION_LIMIT_MESSAGE,
    SKILL_INDEX_ENTRY_TEMPLATE,
    SKILL_INDEX_INTRO,
)
from jvagent.core.app_context import get_app_root
from jvagent.scaffold.skill_resolve import (
    apply_skill_selector,
    resolve_agent_skills,
    resolve_builtin_skills,
    resolve_merged_skill_bundles,
)

logger = logging.getLogger(__name__)


class SkillCatalog:
    """Manages skill discovery, catalog rendering, and metadata access.

    Encapsulates the resolved skill bundle dictionary and provides methods
    for rendering the skill index (system prompt and tool responses),
    validating skill activations, and resolving response mode overrides.
    """

    def __init__(self, discovered_skills: Dict[str, Dict[str, Any]]):
        self._skills = discovered_skills

    @property
    def skills(self) -> Dict[str, Dict[str, Any]]:
        """Raw skill data dictionary."""
        return self._skills

    @property
    def is_empty(self) -> bool:
        """Whether any skills are available."""
        return not self._skills

    def format_index_entry(self, skill_name: str, skill_data: Dict[str, Any]) -> str:
        """Format one skill for the system prompt index or tool response.

        Args:
            skill_name: Skill identifier.
            skill_data: Skill metadata dictionary.

        Returns:
            Formatted index entry string.
        """
        description = str(
            skill_data.get("description") or "Standard operating procedure."
        )
        scope_hint = str(skill_data.get("scope_hint") or "").strip()
        if scope_hint and scope_hint != description:
            description = f"{description} (scope: {scope_hint})"
        metadata = skill_data.get("metadata", {}) or {}
        tags = metadata.get("tags") or skill_data.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        tag_suffix = f" [{', '.join(map(str, tags))}]" if tags else ""
        requires_actions = skill_data.get("requires_actions") or []
        requires_suffix = (
            f" | requires={', '.join(map(str, requires_actions))}"
            if requires_actions
            else ""
        )
        tool_count = len(skill_data.get("tool_files", []) or [])
        tools_suffix = f" | tools={tool_count}"
        return SKILL_INDEX_ENTRY_TEMPLATE.format(
            name=skill_name,
            description=description,
            tag_suffix=tag_suffix,
            requires_suffix=requires_suffix,
            tools_suffix=tools_suffix,
        )

    def render_catalog(self) -> str:
        """Render the full skill catalog for list_skills tool."""
        lines = ["Available skills:"]
        for skill_name, skill_data in self._skills.items():
            lines.append(self.format_index_entry(skill_name, skill_data))
        return "\n".join(lines)

    def render_system_prompt_section(self) -> str:
        """Build the skill index section for the system prompt."""
        skill_index = [SKILL_INDEX_INTRO]
        for s_name, s_data in self._skills.items():
            skill_index.append(self.format_index_entry(s_name, s_data))
        return "\n".join(skill_index)

    def check_activation_limit(
        self,
        skill_name: str,
        activated_skills: Set[str],
        max_activations: int,
    ) -> Optional[str]:
        """Check if a skill can be activated given the current activation count.

        Args:
            skill_name: Skill to activate.
            activated_skills: Already-activated skill names.
            max_activations: Maximum allowed activations.

        Returns:
            Error message if the limit is reached, None if activation is allowed.
        """
        if (
            skill_name not in activated_skills
            and len(activated_skills) >= max_activations
        ):
            active_text = (
                ", ".join(sorted(activated_skills)) if activated_skills else "(none)"
            )
            return SKILL_ACTIVATION_LIMIT_MESSAGE.format(
                active_skills=active_text,
                limit=max_activations,
            )
        return None

    async def validate_requirements(
        self,
        skill_name: str,
        action_resolver: Any,
    ) -> Optional[str]:
        """Validate requires-actions for a skill.

        Args:
            skill_name: Skill to validate.
            action_resolver: The ActionResolver to check requirements against.

        Returns:
            Error message if requirements fail, None if the skill can be activated.
        """
        skill_data = self._skills.get(skill_name, {})
        requires_actions = skill_data.get("requires_actions", [])
        if not requires_actions:
            return None

        if not action_resolver:
            return (
                f"Error: Skill '{skill_name}' cannot be activated. "
                f"It requires actions {requires_actions} but no agent "
                f"context is available to resolve them."
            )

        errors = await action_resolver.validate_requirements(requires_actions)
        if errors:
            return (
                f"Error: Skill '{skill_name}' cannot be activated. "
                f"Required actions unavailable: {', '.join(errors)}"
            )
        return None

    def get_response_mode_override(
        self,
        activated_skills: Set[str],
        default_mode: str,
    ) -> str:
        """Resolve the effective response mode for the final response.

        If any activated skill has ``response-mode: respond`` in its frontmatter,
        return ``respond``. Otherwise, return the default_mode.

        Args:
            activated_skills: Set of activated skill names.
            default_mode: Default response mode (typically from action config).

        Returns:
            Effective response mode string.
        """
        for skill_name in activated_skills:
            skill_data = self._skills.get(skill_name, {})
            if skill_data.get("response_mode") == "respond":
                return "respond"
        return default_mode

    def search(self, query: str, top_k: int = 5) -> str:
        """Search skills by metadata-driven token overlap.

        Language-agnostic tokenization: splits on non-alphanumeric,
        lowercases, drops tokens < 2 chars. No synonym expansion,
        stemming, stopwords, or domain biases. Weights reflect
        information density: name (4.0), tags (3.0),
        description+scope (2.0), tool filenames (1.0), requires (0.5).

        Args:
            query: User utterance or search string.
            top_k: Maximum number of results to return.

        Returns:
            Formatted string with matching skills.
        """
        query_tokens = self._normalize_tokens(query)
        if not query_tokens:
            return self.render_catalog()

        query_lower = query.lower()
        scored: List[tuple] = []

        for skill_name, skill_data in self._skills.items():
            score = self._compute_relevance(
                skill_name, skill_data, query_tokens, query_lower
            )
            if score > 0:
                scored.append((skill_name, skill_data, score))

        if not scored:
            return self.render_catalog()

        scored.sort(key=lambda x: x[2], reverse=True)
        top_skills = scored[:top_k]

        lines = [f"Skill matches for '{query}':"]
        for skill_name, skill_data, _ in top_skills:
            lines.append(self.format_index_entry(skill_name, skill_data))
        return "\n".join(lines)

    @staticmethod
    def _normalize_tokens(text: str) -> List[str]:
        """Language-agnostic tokenization: split on non-alphanumeric, lowercase, drop short."""
        import re

        return [
            t
            for t in re.findall(r"[a-zA-Z0-9_\u00C0-\u024F]+", text.lower())
            if len(t) >= 2
        ]

    @staticmethod
    def _compute_relevance(
        skill_name: str,
        skill_data: Dict[str, Any],
        query_tokens: List[str],
        query_lower: str,
    ) -> float:
        """Compute relevance score between a skill and query tokens.

        Uses weighted token overlap with substring bonuses.
        """
        WEIGHT_NAME = 4.0
        WEIGHT_TAGS = 3.0
        WEIGHT_DESC = 2.0
        WEIGHT_TOOLS = 1.0
        WEIGHT_REQUIRES = 0.5
        SUBSTRING_BONUS = 0.5

        def overlap(tokens_a: List[str], tokens_b: List[str]) -> int:
            set_b = set(tokens_b)
            return sum(1 for t in tokens_a if t in set_b)

        score = 0.0

        name_tokens = SkillCatalog._normalize_tokens(skill_name)
        score += overlap(query_tokens, name_tokens) * WEIGHT_NAME

        metadata = skill_data.get("metadata", {}) or {}
        tags = metadata.get("tags") or skill_data.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        tag_tokens = SkillCatalog._normalize_tokens(" ".join(str(t) for t in tags))
        score += overlap(query_tokens, tag_tokens) * WEIGHT_TAGS

        desc_tokens = SkillCatalog._normalize_tokens(
            str(skill_data.get("description") or "")
        )
        scope_tokens = SkillCatalog._normalize_tokens(
            str(skill_data.get("scope_hint") or "")
        )
        score += overlap(query_tokens, desc_tokens + scope_tokens) * WEIGHT_DESC

        tool_files = skill_data.get("tool_files", []) or []
        tool_text = " ".join(str(f) for f in tool_files)
        tool_tokens = SkillCatalog._normalize_tokens(tool_text)
        score += overlap(query_tokens, tool_tokens) * WEIGHT_TOOLS

        requires = skill_data.get("requires_actions", []) or []
        req_text = " ".join(str(r) for r in requires)
        req_tokens = SkillCatalog._normalize_tokens(req_text)
        score += overlap(query_tokens, req_tokens) * WEIGHT_REQUIRES

        # Substring bonus: direct name match in query
        if skill_name.lower() in query_lower:
            score += SUBSTRING_BONUS * WEIGHT_NAME

        return score

    @classmethod
    async def discover(
        cls,
        visitor: Any,
        skills_selector: Any,
        skills_source: str,
        denied_skills: Optional[List[str]] = None,
    ) -> "SkillCatalog":
        """Factory: resolve skill bundles from configured sources.

        Args:
            visitor: The InteractWalker (must have _agent attribute).
            skills_selector: '-all' | list of names/globs | None.
            skills_source: 'builtin' | 'app' | 'both' | 'none'.
            denied_skills: Names/globs to exclude.

        Returns:
            SkillCatalog with resolved skill bundles.
        """
        agent = getattr(visitor, "_agent", None)
        if not agent:
            return cls({})

        source = str(skills_source or "both").strip().lower()
        selector = skills_selector

        if source == "none":
            return cls({})
        if selector is None or selector == [] or selector == "":
            return cls({})

        try:
            app_root = get_app_root()
            if source == "both":
                discovered_skills = resolve_merged_skill_bundles(
                    app_root=app_root,
                    namespace=agent.namespace,
                    agent_name=agent.name,
                    include_builtin=True,
                )
            elif source == "builtin":
                discovered_skills = resolve_builtin_skills()
            elif source == "app":
                discovered_skills = resolve_agent_skills(
                    app_root=app_root,
                    namespace=agent.namespace,
                    agent_name=agent.name,
                )
            else:
                logger.warning(
                    "SkillCatalog: invalid skills_source '%s' "
                    "(expected builtin|app|both|none)",
                    source,
                )
                return cls({})

            discovered_skills = apply_skill_selector(
                discovered_skills,
                selector=selector,
                denied=denied_skills,
            )
            logger.info(
                "SkillCatalog resolved %d skill bundles for %s/%s (source=%s)",
                len(discovered_skills),
                agent.namespace,
                agent.name,
                source,
            )
            return cls(discovered_skills)
        except Exception as e:
            logger.warning(
                "SkillCatalog: error resolving skill bundles: %s",
                e,
                exc_info=True,
            )
            return cls({})
