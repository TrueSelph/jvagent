"""SkillCatalog: manages skill discovery, resolution, rendering, and validation.

Extracted from SkillInteractAction to isolate skill lifecycle concerns
from loop orchestration. Provides a single entry point for skill bundle
discovery, catalog rendering, activation validation, and response mode
resolution.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from jvagent.action.skill.prompts import (
    GROUNDING_INSTRUCTION_TEMPLATE,
    READ_SKILL_RESULT_TEMPLATE,
    SKILL_ACTIVATION_LIMIT_MESSAGE,
    SKILL_INDEX_ENTRY_TEMPLATE,
    SKILL_INDEX_INTRO,
    SKILL_INDEX_SEARCH_MODE_INTRO,
    SKILL_SEARCH_SEMANTIC_PROMPT,
)
from jvagent.action.skill.version_utils import version_satisfies as _version_satisfies
from jvagent.core.app_context import get_app_root
from jvagent.scaffold.skill_resolve import (
    apply_skill_selector,
    resolve_agent_skills,
    resolve_builtin_skills,
    resolve_merged_skill_bundles,
)

logger = logging.getLogger(__name__)

# TTL for skill discovery cache (seconds)
SKILL_DISCOVERY_CACHE_TTL = 60

# Regexes for questions about the agent (skills, identity, "what can you do?"),
# as opposed to task work. Merged with ``SkillInteractAction.meta_intent_patterns``.
DEFAULT_META_INTENT_PATTERNS: Tuple[str, ...] = (
    r"\bwhat(\s+are)?\s+your\s+skills\b",
    r"\bwhat(\s+skills)?\s+do\s+you\s+have\b",
    r"\blist(\s+of)?\s+your\s+skills\b",
    r"\bwhat(\s+can)\s+you(\s+do)?\b",
    r"\byour\s+capabilities\b",
    r"\bhow\s+can\s+you\s+help\b",
    r"\bwho\s+are\s+you\b",
    r"\bwhat(\s+are)?\s+you(r)?\s+abilities\b",
    r"\bshow\s+me\s+what\s+you(\s+can)?\s+do\b",
)


class SkillCatalog:
    """Manages skill discovery, catalog rendering, and metadata access.

    Encapsulates the resolved skill bundle dictionary and provides methods
    for rendering the skill index (system prompt and tool responses),
    validating skill activations, and resolving response mode overrides.

    Caching:
        ``discover()`` caches resolved skill bundles per agent with a TTL
        to avoid redundant disk I/O across interactions. Call
        ``invalidate_cache()`` after installing or removing skills.
    """

    # Class-level cache: {cache_key: (discovered_skills_dict, cached_at)}
    _cache: Dict[str, Tuple[Dict[str, Dict[str, Any]], datetime]] = {}
    # Lazily-created lock — NOT created at import time to avoid event-loop binding
    # in Python < 3.10 (3.2).  Set to None and materialised inside discover().
    _cache_lock: Optional[asyncio.Lock] = None  # type: ignore[assignment]

    # Maximum number of cache entries before oldest-first eviction (3.3).
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

    def render_search_mode_system_prompt_section(self) -> str:
        """Compact system prompt section when the catalog is large.

        Omits per-skill frontmatter lines; instructs the model to use ``skill_search`` /
        ``list_skills`` first, then ``read_skill``.
        """
        return SKILL_INDEX_SEARCH_MODE_INTRO.format(n_skills=len(self._skills))

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
        # ToolExecutor.activated_skills uses hyphen→underscore keys; align catalog name.
        normalized = skill_name.replace("-", "_")
        if (
            normalized not in activated_skills
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

    async def preflight_check(
        self,
        *,
        action_resolver: Any,
        tool_executor: Any,
    ) -> List[Dict[str, Any]]:
        """Run a deterministic pre-loop capability check for all discovered skills.

        Validates ``requires_actions`` metadata for every skill against the
        live agent graph and checks that required tools are registered in the
        ToolExecutor.  Emits machine-readable failure records (not exceptions)
        so the loop can decide how to proceed.

        Args:
            action_resolver: ActionResolver instance (or None if not in agent context).
            tool_executor: Initialised ToolExecutor with registered tools.

        Returns:
            List of failure dicts with keys ``skill_name``, ``kind``
            (``"missing_action"`` | ``"missing_tool"``), and ``detail``.
            Empty list means all checks passed.
        """
        failures: List[Dict[str, Any]] = []
        registered_tools = set(
            getattr(tool_executor, "get_tool_names", lambda: set())()
        )

        for skill_name, skill_data in self._skills.items():
            # Check requires_actions
            requires_actions: List[str] = skill_data.get("requires_actions", [])
            if requires_actions and action_resolver:
                try:
                    errors = await action_resolver.validate_requirements(
                        requires_actions
                    )
                    for missing in errors or []:
                        failures.append(
                            {
                                "skill_name": skill_name,
                                "kind": "missing_action",
                                "detail": missing,
                            }
                        )
                except Exception as exc:
                    logger.warning(
                        "SkillCatalog.preflight_check: validation error for '%s': %s",
                        skill_name,
                        exc,
                    )

            requires_jvagent = str(skill_data.get("requires_jvagent") or "").strip()
            if requires_jvagent:
                from jvagent.version import __version__ as jvagent_version

                if not _version_satisfies(jvagent_version, requires_jvagent):
                    failures.append(
                        {
                            "skill_name": skill_name,
                            "kind": "jvagent_version",
                            "detail": (
                                f"Skill requires jvagent {requires_jvagent} but "
                                f"runtime is {jvagent_version}"
                            ),
                        }
                    )

            rav = skill_data.get("requires_action_versions") or {}
            if rav and action_resolver:
                try:
                    ver_errs = await action_resolver.validate_action_ref_versions(rav)
                    for detail in ver_errs or []:
                        failures.append(
                            {
                                "skill_name": skill_name,
                                "kind": "action_version",
                                "detail": detail,
                            }
                        )
                except Exception as exc:
                    logger.warning(
                        "SkillCatalog.preflight_check: action version error for '%s': %s",
                        skill_name,
                        exc,
                    )

            # Check required_tools (optional frontmatter key)
            required_tools: List[str] = skill_data.get("required_tools", [])
            for tool in required_tools:
                if tool and tool not in registered_tools:
                    failures.append(
                        {
                            "skill_name": skill_name,
                            "kind": "missing_tool",
                            "detail": tool,
                        }
                    )

        # ---- Chaining compatibility (P2-10) ----
        # Collect all exported keys across skills
        all_exports: Dict[str, List[str]] = {}  # key → [skill_name, ...]
        for skill_name, skill_data in self._skills.items():
            for key in skill_data.get("exports", []):
                all_exports.setdefault(key, []).append(skill_name)

        for skill_name, skill_data in self._skills.items():
            for key in skill_data.get("imports", []):
                if key not in all_exports:
                    failures.append(
                        {
                            "skill_name": skill_name,
                            "kind": "missing_export",
                            "detail": (
                                f"Skill '{skill_name}' imports '{key}' but no "
                                f"discovered skill exports it"
                            ),
                        }
                    )

        # ---- Version constraint validation ----
        # Build a version map for all discovered skills
        skill_versions: Dict[str, str] = {}
        for skill_name, skill_data in self._skills.items():
            ver = (skill_data.get("metadata") or {}).get("version")
            if ver is not None:
                skill_versions[skill_name] = str(ver)

        for skill_name, skill_data in self._skills.items():
            deps = (skill_data.get("metadata") or {}).get("dependencies") or {}
            for dep_name, constraint in deps.items():
                dep_version = skill_versions.get(dep_name)
                if dep_version is None:
                    failures.append(
                        {
                            "skill_name": skill_name,
                            "kind": "unsatisfied_dependency",
                            "detail": (
                                f"Skill '{skill_name}' depends on '{dep_name}' "
                                f"({constraint}) which is not discovered"
                            ),
                        }
                    )
                elif not _version_satisfies(dep_version, str(constraint)):
                    failures.append(
                        {
                            "skill_name": skill_name,
                            "kind": "version_mismatch",
                            "detail": (
                                f"Skill '{skill_name}' requires '{dep_name}' "
                                f"{constraint} but version {dep_version} is installed"
                            ),
                        }
                    )

        if failures:
            logger.warning(
                "SkillCatalog.preflight_check: %d failure(s): %s",
                len(failures),
                failures[:5],
            )
        return failures

    def get_response_mode_override(
        self,
        activated_skills: Set[str],
        default_mode: str,
    ) -> str:
        """Resolve the effective response mode for the final response.

        Inspects each **activated** skill's ``response_mode`` (from SKILL.md
        ``response-mode``). Values ``respond`` and ``publish`` apply; ``None`` /
        omitted means inherit. When multiple activated skills set explicit modes,
        ``respond`` wins over ``publish``; if none set a mode, ``default_mode``
        is used (typically the action's ``response_mode``).

        Args:
            activated_skills: Set of activated skill names.
            default_mode: Default response mode (typically from action config).

        Returns:
            Effective response mode string (``respond`` or ``publish``).
        """
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

    @staticmethod
    def should_inject_persona_identity_for_skill_prompt(
        discovered_skills: Optional[Dict[str, Dict[str, Any]]],
        action_response_mode: str,
    ) -> bool:
        """Whether to load PersonaAction fields for the skill system prompt.

        When every bundled skill uses explicit ``response-mode: publish`` and none
        inherit or request ``respond``, the agentic loop never needs Persona
        identity in the system prompt for response-mode policy (final delivery
        is direct ``publish`` for those activations). Inheriting skills
        (``response_mode`` unset) follow the action default; if that default is
        ``respond``, keep persona text available.

        If there are no discovered skills, follow the action-level
        ``response_mode`` only.

        Args:
            discovered_skills: Skill catalog mapping from discovery (may be empty).
            action_response_mode: Action's ``response_mode`` attribute.

        Returns:
            True if PersonaAction name/description should be read for ``SkillRunContext``.
        """
        mode = (action_response_mode or "publish").strip().lower()
        if not discovered_skills:
            return mode == "respond"
        has_respond = False
        has_inherit = False
        for data in discovered_skills.values():
            m = data.get("response_mode")
            if m == "respond":
                has_respond = True
            elif m is None:
                has_inherit = True
        if has_respond:
            return True
        if mode == "respond" and has_inherit:
            return True
        return False

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

    def top_relevance_score(self, query: str) -> float:
        """Maximum lexical relevance score between ``query`` and any skill.

        Uses the same weighting as :meth:`search` (:meth:`_compute_relevance`).
        Empty or token-less queries return ``0.0``.

        Args:
            query: User utterance or search string.

        Returns:
            Highest score across all skills, or ``0.0`` if none match.
        """
        query_tokens = self._normalize_tokens(query)
        if not query_tokens:
            return 0.0
        query_lower = query.lower()
        best = 0.0
        for skill_name, skill_data in self._skills.items():
            score = self._compute_relevance(
                skill_name, skill_data, query_tokens, query_lower
            )
            if score > best:
                best = score
        return best

    def has_relevant_match(self, query: str, threshold: float) -> bool:
        """Whether any skill meets or exceeds the relevance threshold."""
        return self.top_relevance_score(query) >= threshold

    def build_semantic_search_query(self, query: str, top_k: int = 5) -> str:
        """Build the prompt for LLM-based skill ranking.

        Returns a string prompt that can be sent to a language model for
        semantic skill matching.  The caller provides the model action
        and handles the LLM call.

        Args:
            query: User utterance or search string.
            top_k: Maximum number of results to return.

        Returns:
            Prompt string ready for a model call.
        """
        skill_anchors: Dict[str, Dict[str, Any]] = {}
        for skill_name, skill_data in self._skills.items():
            metadata = skill_data.get("metadata", {}) or {}
            tags = metadata.get("tags") or skill_data.get("tags") or []
            if isinstance(tags, str):
                tags = [tags]
            skill_anchors[skill_name] = {
                "description": skill_data.get("description", ""),
                "scope_hint": skill_data.get("scope_hint", ""),
                "tags": tags,
                "tool_count": len(skill_data.get("tool_files", []) or []),
            }
        return SKILL_SEARCH_SEMANTIC_PROMPT.format(
            query=query,
            top_k=top_k,
            skills_json=json.dumps(skill_anchors, indent=2),
        )

    async def search_semantic(
        self,
        query: str,
        top_k: int = 5,
        *,
        model_action: Any,
        base_model_kwargs: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Rank skills via LLM call using the interact router anchor pattern.

        Presents skill metadata as a JSON dictionary to a language model and
        asks it to rank the top matches with relevance scores and rationales.

        Falls back to lexical search on any failure (bad JSON, model error,
        empty result).

        Args:
            query: User utterance or search string.
            top_k: Maximum number of results to return.
            model_action: Pre-resolved LanguageModelAction for the LLM call.
            base_model_kwargs: Optional model kwargs (temperature, max_tokens,
                model override).  Defaults to low temperature for deterministic
                ranking when not provided.

        Returns:
            Formatted string with ranked skill matches, or lexical fallback.
        """
        try:
            prompt = self.build_semantic_search_query(query, top_k=top_k)
            kwargs: Dict[str, Any] = {
                "messages": [{"role": "user", "content": prompt}],
                "system": None,
                "tools": None,
                "calling_action_name": "SkillCatalog.search_semantic",
                "prompt_for_observability": query,
                "model": base_model_kwargs.get("model") if base_model_kwargs else None,
                "temperature": (
                    base_model_kwargs.get("temperature", 0.1)
                    if base_model_kwargs
                    else 0.1
                ),
                "max_tokens": (
                    base_model_kwargs.get("max_tokens", 500)
                    if base_model_kwargs
                    else 500
                ),
            }

            result = await model_action.query_messages(stream=False, **kwargs)
            response_text = await result.get_response()
            if not response_text and result.response:
                response_text = result.response

            if not response_text:
                raise ValueError("Empty response from semantic search model")

            return self._parse_semantic_search_response(response_text, query, top_k)
        except Exception as exc:
            logger.warning(
                "SkillCatalog.search_semantic: LLM ranking failed: %s — "
                "falling back to lexical search",
                exc,
            )
            return self.search(query, top_k=top_k)

    def _parse_semantic_search_response(
        self, raw_text: str, query: str, top_k: int
    ) -> str:
        """Parse the LLM's JSON response into a formatted skill match string.

        Validates that returned skill names exist in the catalog and
        relevance scores are in range.  Falls back to lexical search if
        parsing fails or no valid matches are found.
        """
        # Strip markdown code fences if the model wraps the JSON
        text = raw_text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) > 2:
                text = "\n".join(lines[1:-1])
            else:
                text = text.strip("`")

        parsed = json.loads(text)
        matches = parsed.get("matches") or []

        lines = [f"Skill matches for '{query}':"]
        valid_count = 0
        for m in matches[:top_k]:
            skill_name = str(m.get("skill_name") or "").strip()
            if skill_name not in self._skills:
                continue
            try:
                relevance = float(m.get("relevance", 0.5))
                relevance = max(0.0, min(1.0, relevance))
            except (TypeError, ValueError):
                relevance = 0.5
            rationale = str(m.get("rationale") or "").strip()
            entry = self.format_index_entry(skill_name, self._skills[skill_name])
            lines.append(f"  {entry} [relevance={relevance:.2f}]")
            if rationale:
                lines.append(f"    → {rationale}")
            valid_count += 1

        if valid_count == 0:
            return self.search(query, top_k=top_k)
        return "\n".join(lines)

    @classmethod
    def is_meta_intent(
        cls, utterance: str, extra_patterns: Optional[List[str]] = None
    ) -> bool:
        """Whether the user is asking a meta / introspective question (skills, identity).

        These turns should not get a "skill activation" nudge, and the final
        review pass is often unhelpful for plain catalog-style answers.
        """
        if not (utterance or "").strip():
            return False
        all_patterns: List[str] = list(DEFAULT_META_INTENT_PATTERNS)
        if extra_patterns:
            for p in extra_patterns:
                if p and str(p).strip():
                    all_patterns.append(str(p))
        text = utterance
        for p in all_patterns:
            try:
                if re.search(p, text, re.IGNORECASE | re.UNICODE):
                    return True
            except re.error as e:
                logger.warning("is_meta_intent: invalid pattern %r: %s", p, e)
        return False

    @staticmethod
    def _normalize_tokens(text: str) -> List[str]:
        """Language-agnostic tokenization: split on non-alphanumeric, lowercase, drop short."""
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
        # Normalize selector to a hashable tuple
        if isinstance(skills_selector, (list, tuple)):
            selector_key = tuple(str(s) for s in skills_selector)
        else:
            selector_key = (str(skills_selector),)

        # Normalize denied skills
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
        async with cls._cache_lock:
            if namespace is None:
                cls._cache.clear()
                logger.debug("SkillCatalog cache cleared")
                return

            keys_to_remove = []
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
        to avoid redundant disk I/O across interactions. Call
        ``invalidate_cache()`` after installing or removing skills.

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

        app_root = get_app_root()
        cache_key = cls._build_cache_key(
            namespace=agent.namespace,
            agent_name=agent.name,
            skills_source=source,
            skills_selector=selector,
            denied_skills=denied_skills,
            app_root=str(app_root),
        )

        # Lazy lock initialisation (3.2) — avoids event-loop binding at import time.
        if cls._cache_lock is None:
            cls._cache_lock = asyncio.Lock()

        # Check cache (tolerate naive cached_at from tests or older entries)
        now = datetime.now(timezone.utc)
        async with cls._cache_lock:
            if cache_key in cls._cache:
                cached_skills, cached_at = cls._cache[cache_key]
                if cached_at.tzinfo is None:
                    cached_at = cached_at.replace(tzinfo=timezone.utc)
                age = (now - cached_at).total_seconds()
                if age < SKILL_DISCOVERY_CACHE_TTL:
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

            # Store in cache with max-size eviction (3.3).
            async with cls._cache_lock:
                if len(cls._cache) >= cls._CACHE_MAX_ENTRIES:
                    # Evict the entry with the oldest cached_at timestamp.
                    oldest_key = min(cls._cache, key=lambda k: cls._cache[k][1])
                    del cls._cache[oldest_key]
                    logger.debug(
                        "SkillCatalog: evicted oldest cache entry (%s) to stay "
                        "within %d-entry limit",
                        oldest_key,
                        cls._CACHE_MAX_ENTRIES,
                    )
                cls._cache[cache_key] = (discovered_skills, now)

            return cls(discovered_skills)
        except Exception as e:
            logger.warning(
                "SkillCatalog: error resolving skill bundles: %s",
                e,
                exc_info=True,
            )
            return cls({})
