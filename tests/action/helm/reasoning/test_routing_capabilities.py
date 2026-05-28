"""Tests for the ADR-0008 unified-capabilities router prompt + parser.

Covers:

- Router prompt renders a single ``CAPABILITIES AVAILABLE`` section
  (no parallel SKILLS / INTERACT ACTIONS surfaces).
- System prompt drops the posture-classification surface.
- Parser accepts the new ``selected`` JSON output shape AND the legacy
  split-schema fallback for cache entries written pre-Wave-6.
- ``RoutingResult.skills`` / ``.actions`` / ``.interact_actions`` backcompat
  properties yield correct values from ``selected``.
"""

from __future__ import annotations

import json

from jvagent.action.helm.reasoning.routing.prompts import (
    ANCHOR_DISAMBIGUATION_CLAUSE,
    ROUTING_SYSTEM_PROMPT,
    ROUTING_USER_PROMPT_TEMPLATE,
    build_routing_system_prompt,
    build_routing_user_prompt_template,
)
from jvagent.action.helm.reasoning.routing.types import (
    CapabilityRef,
    RoutingResult,
    parse_routing_response,
)


class TestSystemPromptShape:
    """The system prompt is the ADR-0008 unified-catalog shape — no posture."""

    def setup_method(self):
        self.system_prompt = build_routing_system_prompt()

    def test_system_prompt_module_constant_matches_factory(self):
        assert ROUTING_SYSTEM_PROMPT == self.system_prompt

    def test_no_posture_classification_block(self):
        # The full posture block opened with "STEP 0 — POSTURE" in the
        # legacy prompt. Wave 6 removed it entirely.
        assert "STEP 0 — POSTURE" not in self.system_prompt
        assert "RESPOND — use when" not in self.system_prompt
        assert "SUPPRESS — use ONLY when" not in self.system_prompt
        assert "DEFER — use ONLY when" not in self.system_prompt

    def test_no_canned_response_guidance(self):
        # canned_response is permanently absent from this prompt surface.
        assert "canned_response" not in self.system_prompt

    def test_capability_selection_block_is_present(self):
        # ADR-0008 prompt presents a single CAPABILITY SELECTION surface.
        assert "CAPABILITY SELECTION" in self.system_prompt
        assert "single catalog" in self.system_prompt.lower()

    def test_intent_types_block_preserved(self):
        # Intent classification stays — operators read it from logs.
        assert "INTENT TYPES" in self.system_prompt
        assert "INFORMATIONAL" in self.system_prompt
        assert "CONVERSATIONAL" in self.system_prompt

    def test_recap_clause_preserved(self):
        # Load-bearing for routing precision on recap requests.
        assert "INFORMATIONAL" in self.system_prompt
        assert "recap" in self.system_prompt.lower()

    def test_anchor_disambiguation_clause_preserved(self):
        # Shared invariant with the legacy rails InteractRouter.
        assert ANCHOR_DISAMBIGUATION_CLAUSE in self.system_prompt


class TestUserPromptTemplateShape:
    """The user-prompt template renders the unified ``CAPABILITIES AVAILABLE`` section."""

    def setup_method(self):
        self.template = build_routing_user_prompt_template()

    def test_template_module_constant_matches_factory(self):
        assert ROUTING_USER_PROMPT_TEMPLATE == self.template

    def test_required_format_placeholders_present(self):
        required = [
            "{active_tasks_section}",
            "{history_section}",
            "{prior_fragments_section}",
            "{utterance}",
            "{capabilities_json}",
            "{optional_instructions}",
        ]
        for ph in required:
            assert ph in self.template, f"missing placeholder {ph!r}"

    def test_legacy_split_placeholders_removed(self):
        # No more {skills_json} / {interact_actions_json} / {entity_field} / {canned_field}.
        assert "{skills_json}" not in self.template
        assert "{interact_actions_json}" not in self.template
        assert "{entity_field}" not in self.template
        assert "{canned_field}" not in self.template

    def test_unified_catalog_section_heading(self):
        assert "CAPABILITIES AVAILABLE" in self.template
        # Legacy headings must be gone.
        assert "SKILLS CATALOG" not in self.template
        assert "INTERACT ACTIONS CATALOG" not in self.template

    def test_output_schema_advertises_selected_field(self):
        assert '"selected"' in self.template
        # Legacy posture / split-output fields should not be advertised.
        assert '"posture"' not in self.template
        assert '"canned_response"' not in self.template

    def test_template_renders_with_typical_fields(self):
        rendered = self.template.format(
            active_tasks_section="",
            history_section="(no history)\n",
            prior_fragments_section="",
            utterance="What is jvspatial?",
            capabilities_json='{"web_search": {"description": "search the web"}}',
            optional_instructions="",
        )
        assert "What is jvspatial?" in rendered
        assert "web_search" in rendered


class TestParserAcceptsSelectedShape:
    """``parse_routing_response`` handles the ADR-0008 ``selected`` shape."""

    def test_selected_shape_parses(self):
        response = json.dumps(
            {
                "interpretation": "user wants to search",
                "intent_type": "INFORMATIONAL",
                "selected": [
                    {"name": "web_search", "kind": "skill"},
                    {"name": "HandoffInteractAction", "kind": "ia"},
                ],
                "confidence": 0.9,
            }
        )
        result = parse_routing_response(response)
        assert len(result.selected) == 2
        names = {c.name for c in result.selected}
        assert names == {"web_search", "HandoffInteractAction"}

    def test_parser_drops_unknown_kind(self):
        response = json.dumps(
            {
                "intent_type": "INFORMATIONAL",
                "selected": [
                    {"name": "web_search", "kind": "skill"},
                    {"name": "x", "kind": "unknown_kind"},
                ],
            }
        )
        result = parse_routing_response(response)
        assert [c.name for c in result.selected] == ["web_search"]

    def test_parser_handles_legacy_split_schema(self):
        """Cache entries written before Wave 6 still deserialise."""
        response = json.dumps(
            {
                "intent_type": "INFORMATIONAL",
                "skills": ["web_search"],
                "interact_actions": ["HandoffInteractAction"],
            }
        )
        result = parse_routing_response(response)
        kinds_by_name = {c.name: c.kind for c in result.selected}
        assert kinds_by_name == {
            "web_search": "skill",
            "HandoffInteractAction": "ia",
        }

    def test_parser_handles_legacy_actions_only(self):
        """Older payloads with only an ``actions`` list still parse to skills."""
        response = json.dumps(
            {
                "intent_type": "INFORMATIONAL",
                "actions": ["web_search"],
            }
        )
        result = parse_routing_response(response)
        assert result.selected == [CapabilityRef(name="web_search", kind="skill")]


class TestBackcompatPropertiesViaParser:
    """End-to-end: parsed payloads expose ``actions`` / ``interact_actions``."""

    def test_skills_property_from_parsed_selected(self):
        result = parse_routing_response(
            json.dumps(
                {
                    "selected": [{"name": "web_search", "kind": "skill"}],
                    "intent_type": "INFORMATIONAL",
                }
            )
        )
        assert result.skills == ["web_search"]
        assert result.actions == ["web_search"]
        assert result.interact_actions == []

    def test_interact_actions_property_from_parsed_selected(self):
        result = parse_routing_response(
            json.dumps(
                {
                    "selected": [
                        {"name": "HandoffInteractAction", "kind": "ia"},
                    ],
                    "intent_type": "DIRECTIVE",
                }
            )
        )
        assert result.interact_actions == ["HandoffInteractAction"]
        assert result.skills == []
