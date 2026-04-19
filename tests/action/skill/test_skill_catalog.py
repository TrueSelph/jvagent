"""Tests for SkillCatalog: discovery, search, rendering, validation."""

import pytest

from jvagent.action.skill.skill_catalog import SkillCatalog

# --- Fixtures ---


def _sample_skills():
    return {
        "research": {
            "description": "Investigate topics and synthesize findings.",
            "scope_hint": "analysis and synthesis",
            "metadata": {"tags": ["research", "analysis"]},
            "tool_files": ["search.py"],
            "requires_actions": [],
        },
        "web_search": {
            "description": "Search the public web for supplemental information.",
            "scope_hint": "external search",
            "metadata": {"tags": ["search", "retrieval"]},
            "tool_files": ["search.py"],
            "requires_actions": [],
        },
        "gmail": {
            "description": "Read and send email via Gmail.",
            "scope_hint": "email communication",
            "metadata": {"tags": ["gmail", "email", "communication"]},
            "tool_files": ["gmail_tool.py"],
            "requires_actions": ["gmail_action"],
        },
        "calendar": {
            "description": "Manage calendar events and scheduling.",
            "scope_hint": "scheduling and events",
            "metadata": {"tags": ["calendar", "scheduling"]},
            "tool_files": [],
            "requires_actions": [],
        },
    }


# --- Rendering ---


class TestFormatIndexEntry:
    def test_basic_entry(self):
        catalog = SkillCatalog(_sample_skills())
        entry = catalog.format_index_entry("research", _sample_skills()["research"])
        assert "research" in entry
        assert "Investigate topics" in entry

    def test_entry_with_scope_hint(self):
        catalog = SkillCatalog(_sample_skills())
        entry = catalog.format_index_entry("research", _sample_skills()["research"])
        assert "scope:" in entry

    def test_entry_with_tags(self):
        catalog = SkillCatalog(_sample_skills())
        entry = catalog.format_index_entry("research", _sample_skills()["research"])
        assert "research" in entry or "analysis" in entry

    def test_entry_with_requires_actions(self):
        catalog = SkillCatalog(_sample_skills())
        entry = catalog.format_index_entry("gmail", _sample_skills()["gmail"])
        assert "requires=" in entry or "gmail_action" in entry

    def test_entry_with_tool_files(self):
        catalog = SkillCatalog(_sample_skills())
        entry = catalog.format_index_entry("research", _sample_skills()["research"])
        assert "tools=" in entry

    def test_empty_description_default(self):
        catalog = SkillCatalog({"minimal": {}})
        entry = catalog.format_index_entry("minimal", {})
        assert "Standard operating procedure" in entry

    def test_tag_string_coerced_to_list(self):
        catalog = SkillCatalog({"s": {"metadata": {"tags": "single"}}})
        entry = catalog.format_index_entry("s", {"metadata": {"tags": "single"}})
        assert "single" in entry


class TestRenderCatalog:
    def test_renders_all_skills(self):
        skills = _sample_skills()
        catalog = SkillCatalog(skills)
        result = catalog.render_catalog()
        assert "Available skills:" in result
        for name in skills:
            assert name in result

    def test_empty_catalog(self):
        catalog = SkillCatalog({})
        result = catalog.render_catalog()
        assert "Available skills:" in result


class TestRenderSystemPromptSection:
    def test_includes_intro(self):
        catalog = SkillCatalog(_sample_skills())
        result = catalog.render_system_prompt_section()
        assert len(result) > 0


# --- Activation validation ---


class TestCheckActivationLimit:
    def test_allows_when_under_limit(self):
        catalog = SkillCatalog(_sample_skills())
        result = catalog.check_activation_limit("research", set(), max_activations=3)
        assert result is None

    def test_allows_already_activated(self):
        catalog = SkillCatalog(_sample_skills())
        activated = {"research"}
        result = catalog.check_activation_limit(
            "research", activated, max_activations=1
        )
        assert result is None

    def test_blocks_at_limit(self):
        catalog = SkillCatalog(_sample_skills())
        activated = {"research", "gmail"}
        result = catalog.check_activation_limit(
            "calendar", activated, max_activations=2
        )
        assert result is not None
        assert "limit" in result.lower() or "2" in result


# --- Response mode ---


class TestGetResponseModeOverride:
    def test_overrides_when_skill_has_respond_mode(self):
        skills = {"s": {"response_mode": "respond"}}
        catalog = SkillCatalog(skills)
        result = catalog.get_response_mode_override({"s"}, "suppress")
        assert result == "respond"

    def test_returns_default_when_no_override(self):
        skills = {"s": {"response_mode": "suppress"}}
        catalog = SkillCatalog(skills)
        result = catalog.get_response_mode_override({"s"}, "suppress")
        assert result == "suppress"

    def test_returns_default_when_no_skills_activated(self):
        catalog = SkillCatalog(_sample_skills())
        result = catalog.get_response_mode_override(set(), "suppress")
        assert result == "suppress"


# --- Properties ---


class TestProperties:
    def test_is_empty_true(self):
        assert SkillCatalog({}).is_empty is True

    def test_is_empty_false(self):
        assert SkillCatalog({"s": {}}).is_empty is False

    def test_skills_returns_raw_dict(self):
        skills = _sample_skills()
        catalog = SkillCatalog(skills)
        assert catalog.skills is skills


# --- Search ---


class TestSearch:
    def test_returns_matches_for_relevant_query(self):
        catalog = SkillCatalog(_sample_skills())
        result = catalog.search("search the web for information", top_k=3)
        assert "Skill matches for" in result
        assert "web_search" in result

    def test_falls_back_to_full_catalog_when_no_matches(self):
        catalog = SkillCatalog({"obscure": {"description": "xylophone zither"}})
        result = catalog.search("quantum entanglement", top_k=3)
        assert "Available skills:" in result

    def test_tag_matching(self):
        catalog = SkillCatalog(_sample_skills())
        result = catalog.search("email", top_k=3)
        assert "gmail" in result

    def test_name_substring_bonus(self):
        catalog = SkillCatalog(_sample_skills())
        result = catalog.search("I need to use gmail for something", top_k=3)
        assert "gmail" in result

    def test_top_k_limits_results(self):
        catalog = SkillCatalog(_sample_skills())
        result = catalog.search("search information", top_k=1)
        # Only 1 skill in the top result
        lines = [
            l
            for l in result.splitlines()
            if l.strip() and not l.startswith("Skill matches")
        ]
        assert len(lines) <= 1

    def test_empty_query_returns_full_catalog(self):
        catalog = SkillCatalog(_sample_skills())
        result = catalog.search("", top_k=3)
        assert "Available skills:" in result

    def test_multilingual_tokens(self):
        skills = {
            "recherche": {
                "description": "Recherche et analyse",
                "metadata": {"tags": ["recherche"]},
            }
        }
        catalog = SkillCatalog(skills)
        result = catalog.search("recherche", top_k=3)
        assert "recherche" in result

    def test_compute_relevance_returns_zero_for_unrelated(self):
        score = SkillCatalog._compute_relevance(
            "xyz", {"description": "abc", "metadata": {}}, ["hello"], "hello"
        )
        assert score == 0.0

    def test_normalize_tokens_drops_short(self):
        tokens = SkillCatalog._normalize_tokens("I a test")
        assert "a" not in tokens  # 1 char, dropped
        assert "I" not in tokens  # 1 char, dropped
        assert "test" in tokens

    def test_normalize_tokens_unicode(self):
        tokens = SkillCatalog._normalize_tokens("résumé café")
        assert len(tokens) > 0


# --- Requirements validation ---


class TestValidateRequirements:
    @pytest.mark.asyncio
    async def test_passes_when_no_requirements(self):
        catalog = SkillCatalog(_sample_skills())
        result = await catalog.validate_requirements("research", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_fails_when_no_resolver_but_requirements_exist(self):
        catalog = SkillCatalog(_sample_skills())
        result = await catalog.validate_requirements("gmail", None)
        assert result is not None
        assert "cannot be activated" in result

    @pytest.mark.asyncio
    async def test_delegates_to_resolver(self):
        from unittest.mock import AsyncMock

        resolver = AsyncMock()
        resolver.validate_requirements = AsyncMock(return_value=[])
        catalog = SkillCatalog(_sample_skills())
        result = await catalog.validate_requirements("gmail", resolver)
        assert result is None
        resolver.validate_requirements.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_error_when_resolver_reports_missing(self):
        from unittest.mock import AsyncMock

        resolver = AsyncMock()
        resolver.validate_requirements = AsyncMock(return_value=["missing_action"])
        catalog = SkillCatalog(_sample_skills())
        result = await catalog.validate_requirements("gmail", resolver)
        assert result is not None
        assert "missing_action" in result
