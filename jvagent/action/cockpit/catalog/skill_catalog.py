"""Cockpit skill catalog: discovery, rendering, and search."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from jvagent.action.cockpit.catalog.prompts import (
    SKILL_INDEX_ENTRY_TEMPLATE,
    SKILL_INDEX_INTRO,
    SKILL_INDEX_SEARCH_MODE_INTRO,
)
from jvagent.core.app_context import get_app_root
from jvagent.scaffold.skill_resolve import (
    apply_skill_selector,
    resolve_agent_skills,
    resolve_builtin_skills,
    resolve_merged_skill_bundles,
)

logger = logging.getLogger(__name__)

# TTL for skill discovery cache (seconds)
_SKILL_DISCOVERY_CACHE_TTL = 60


# ---------------------------------------------------------------------------
# SkillCatalog
# ---------------------------------------------------------------------------


class SkillCatalog:
    """Skill catalog used by cockpit for discovery, rendering, and search."""

    # Class-level cache: {cache_key: (discovered_skills_dict, cached_at)}
    _cache: Dict[str, Tuple[Dict[str, Dict[str, Any]], datetime]] = {}
    _cache_lock: Optional[asyncio.Lock] = None

    _CACHE_MAX_ENTRIES: int = 200

    def __init__(self, discovered_skills: Dict[str, Dict[str, Any]]):
        self._skills = discovered_skills

    @property
    def skills(self) -> Dict[str, Dict[str, Any]]:
        """Raw skill data dictionary."""
        return self._skills

    @skills.setter
    def skills(self, value: Dict[str, Dict[str, Any]]) -> None:
        self._skills = value

    @property
    def is_empty(self) -> bool:
        """Whether any skills are available."""
        return not self._skills

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def format_index_entry(self, skill_name: str, skill_data: Dict[str, Any]) -> str:
        """Format one skill for the system prompt index or tool response."""
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

    def render_search_mode_system_prompt_section(self) -> str:
        """Compact system prompt section when the catalog is large."""
        return SKILL_INDEX_SEARCH_MODE_INTRO.format(n_skills=len(self._skills))

    # ------------------------------------------------------------------
    # Response mode overrides
    # ------------------------------------------------------------------

    def get_response_mode_override(
        self,
        activated_skills: Set[str],
        default_mode: str,
    ) -> str:
        """Resolve the effective response mode for the final response."""
        activated_norm = {s.replace("-", "_") for s in activated_skills}
        explicit: Set[str] = set()
        for catalog_key, skill_data in self._skills.items():
            if catalog_key.replace("-", "_") not in activated_norm:
                continue
            mode = skill_data.get("response_mode")
            if mode == "respond":
                explicit.add("respond")
            elif mode == "publish":
                explicit.add("publish")
        if "respond" in explicit:
            return "respond"
        if "publish" in explicit:
            return "publish"
        return default_mode

    def get_verbatim_final_override(self, activated_skills: Set[str]) -> bool:
        """Return True if any activated skill demands verbatim delivery."""
        activated_norm = {s.replace("-", "_") for s in activated_skills}
        for catalog_key, skill_data in self._skills.items():
            if catalog_key.replace("-", "_") not in activated_norm:
                continue
            if skill_data.get("verbatim_final"):
                return True
        return False

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: int = 5) -> str:
        """Search skills by metadata-driven token overlap."""
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

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    @classmethod
    async def discover(
        cls,
        visitor: Any,
        skills_selector: Any,
        skills_source: str,
        denied_skills: Optional[List[str]] = None,
    ) -> "SkillCatalog":
        """Factory: resolve skill bundles from configured sources.

        Uses an in-memory TTL cache keyed by agent identity and selector
        to avoid redundant disk I/O across interactions.
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

        app_root = get_app_root()
        cache_key = cls._build_cache_key(
            namespace=agent.namespace,
            agent_name=agent.name,
            skills_source=source,
            skills_selector=selector,
            denied_skills=denied_skills,
            app_root=str(app_root),
        )

        if cls._cache_lock is None:
            cls._cache_lock = asyncio.Lock()

        now = datetime.now(timezone.utc)
        async with cls._cache_lock:
            if cache_key in cls._cache:
                cached_skills, cached_at = cls._cache[cache_key]
                if cached_at.tzinfo is None:
                    cached_at = cached_at.replace(tzinfo=timezone.utc)
                age = (now - cached_at).total_seconds()
                if age < _SKILL_DISCOVERY_CACHE_TTL:
                    logger.debug(
                        "SkillCatalog cache hit for %s/%s (age: %.1fs)",
                        agent.namespace,
                        agent.name,
                        age,
                    )
                    return cls(cached_skills)
                else:
                    logger.debug(
                        "SkillCatalog cache expired for %s/%s (age: %.1fs)",
                        agent.namespace,
                        agent.name,
                        age,
                    )
                    del cls._cache[cache_key]

        # Cache miss — resolve from disk
        try:
            if source == "both":
                discovered_skills = await asyncio.to_thread(
                    resolve_merged_skill_bundles,
                    app_root=app_root,
                    namespace=agent.namespace,
                    agent_name=agent.name,
                    include_builtin=True,
                )
            elif source == "builtin":
                discovered_skills = await asyncio.to_thread(resolve_builtin_skills)
            elif source == "app":
                discovered_skills = await asyncio.to_thread(
                    resolve_agent_skills,
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

            async with cls._cache_lock:
                if len(cls._cache) >= cls._CACHE_MAX_ENTRIES:
                    oldest_key = min(cls._cache, key=lambda k: cls._cache[k][1])
                    del cls._cache[oldest_key]
                cls._cache[cache_key] = (discovered_skills, now)

            return cls(discovered_skills)
        except Exception as e:
            logger.warning(
                "SkillCatalog: error resolving skill bundles: %s",
                e,
                exc_info=True,
            )
            return cls({})

    @classmethod
    async def invalidate_cache(
        cls,
        namespace: Optional[str] = None,
        agent_name: Optional[str] = None,
    ) -> None:
        """Invalidate cached skill discovery entries.

        Args:
            namespace: If provided, invalidate only entries for this namespace.
            agent_name: If provided with namespace, invalidate only for this agent.
        """
        if cls._cache_lock is None:
            cls._cache_lock = asyncio.Lock()

        async with cls._cache_lock:
            if namespace is None:
                cls._cache.clear()
                logger.debug("SkillCatalog cache cleared")
                return

            keys_to_remove: List[str] = []
            for key in cls._cache:
                parts = key.split("|")
                if len(parts) >= 2:
                    key_ns = parts[0]
                    key_agent = parts[1]
                    if key_ns == namespace:
                        if agent_name is None or key_agent == agent_name:
                            keys_to_remove.append(key)

            for key in keys_to_remove:
                del cls._cache[key]

            logger.debug(
                "SkillCatalog cache invalidated for %s/%s (%d entries)",
                namespace or "*",
                agent_name or "*",
                len(keys_to_remove),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_tokens(text: str) -> List[str]:
        """Language-agnostic tokenization: split on non-alphanumeric, lowercase, drop short."""
        return [t for t in re.findall(r"[a-zA-Z0-9_À-ɏ]+", text.lower()) if len(t) >= 2]

    @staticmethod
    def _compute_relevance(
        skill_name: str,
        skill_data: Dict[str, Any],
        query_tokens: List[str],
        query_lower: str,
    ) -> float:
        """Compute relevance score between a skill and query tokens."""
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

        if skill_name.lower() in query_lower:
            score += SUBSTRING_BONUS * WEIGHT_NAME

        return score

    @staticmethod
    def _build_cache_key(
        namespace: str,
        agent_name: str,
        skills_source: str,
        skills_selector: Any,
        denied_skills: Optional[List[str]],
        app_root: str,
    ) -> str:
        """Build a deterministic cache key for skill discovery."""
        if isinstance(skills_selector, (list, tuple)):
            selector_key = tuple(str(s) for s in skills_selector)
        else:
            selector_key = (str(skills_selector),)

        if denied_skills:
            denied_key = tuple(str(d) for d in denied_skills)
        else:
            denied_key = ()

        return "|".join(
            [
                namespace,
                agent_name,
                str(skills_source),
                ",".join(selector_key),
                ",".join(denied_key),
                str(app_root),
            ]
        )
