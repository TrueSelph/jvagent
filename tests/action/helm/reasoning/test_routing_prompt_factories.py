"""Tests for the routing-prompt factories in ReasoningHelm.

The routing prompts were duplicated from standalone Cockpit at extraction
time (per BRIDGE-ROADMAP §C-2). Bridge composition deprecates two of those
surfaces:

1. **POSTURE classification** — Reflex gates SUPPRESS/DEFER upstream, so
   the full STEP 0 block (~400 tokens) is redundant in Bridge mode. A
   one-line defensive fallback covers the pathological pass-through case.

2. **canned_response guidance** — Reflex's ``transient_ack`` on SHIFT owns
   the user-facing immediate response. ``enable_canned_response`` defaults
   to False in ``bridge_agent.yaml``; the principle bullet that describes
   what canned_response should look like is dead surface when the flag is
   off.

The prompts module exposes ``build_routing_system_prompt`` and
``build_routing_user_prompt_template`` for assembling either the full
(standalone-equivalent) prompt or the Bridge-mode prompt that
``EngineRouter`` actually sends. These tests pin the contract for both
shapes plus the single-source-of-truth invariant on the module-level
constants.
"""

from __future__ import annotations

from jvagent.action.helm.reasoning.routing.prompts import (
    ANCHOR_DISAMBIGUATION_CLAUSE,
    ROUTING_SYSTEM_PROMPT,
    ROUTING_USER_PROMPT_TEMPLATE,
    build_routing_system_prompt,
    build_routing_user_prompt_template,
)


class TestModuleConstantsAreFactoryOutput:
    """Single-source-of-truth invariant.

    Both module-level constants must be byte-identical to the factory
    called with full-shape flags. Drift between the two means importers
    of the constants see different content than the factory produces —
    a future maintainer would have to audit two surfaces instead of one.
    """

    def test_routing_system_prompt_constant_matches_full_factory(self):
        expected = build_routing_system_prompt(include_posture_block=True)
        assert ROUTING_SYSTEM_PROMPT == expected, (
            "ROUTING_SYSTEM_PROMPT module constant has drifted from "
            "build_routing_system_prompt(include_posture_block=True). "
            "Either rebuild the constant through the factory or update "
            "the factory."
        )

    def test_routing_user_prompt_template_constant_matches_full_factory(self):
        expected = build_routing_user_prompt_template(include_posture_recap=True)
        assert ROUTING_USER_PROMPT_TEMPLATE == expected, (
            "ROUTING_USER_PROMPT_TEMPLATE module constant has drifted from "
            "build_routing_user_prompt_template(True). Either rebuild the "
            "constant through the factory or update the factory."
        )


class TestBridgeSystemPromptShape:
    """Bridge-mode system prompt drops the STEP 0 posture block."""

    def setup_method(self):
        self.bridge_prompt = build_routing_system_prompt(include_posture_block=False)
        self.full_prompt = build_routing_system_prompt(include_posture_block=True)

    def test_bridge_prompt_omits_full_step0_heading(self):
        # The full prompt opens its posture surface with "STEP 0 — POSTURE".
        # Bridge mode must NOT carry that heading.
        assert "STEP 0 — POSTURE" not in self.bridge_prompt
        assert "STEP 0 — POSTURE" in self.full_prompt

    def test_bridge_prompt_omits_respond_use_when_bullets(self):
        # The "RESPOND — use when:" / "SUPPRESS — use ONLY when:" bullet
        # lists are the bulk of the POSTURE block. Stripped in Bridge.
        assert "RESPOND — use when" not in self.bridge_prompt
        assert "SUPPRESS — use ONLY when" not in self.bridge_prompt
        assert "DEFER — use ONLY when" not in self.bridge_prompt

    def test_bridge_prompt_contains_defensive_posture_one_liner(self):
        # The one-line defensive fallback keeps the model honest on
        # pathological pass-throughs (Reflex SHIFTing something genuinely
        # unintelligible).
        assert "POSTURE — Always RESPOND" in self.bridge_prompt
        assert "truly unintelligible" in self.bridge_prompt

    def test_no_canned_response_guidance_in_either_shape(self):
        # canned_response is permanently absent from ReasoningHelm's prompt
        # surface (Reflex owns the transient_ack lead-in). The bullet text
        # should be missing from BOTH the bridge and full variants — this
        # is the Phase-2 contract.
        assert "canned_response (when emitted)" not in self.bridge_prompt
        assert "canned_response (when emitted)" not in self.full_prompt

    def test_bridge_prompt_preserves_anchor_disambiguation_clause(self):
        # The anchor clause is load-bearing for routing precision and
        # must survive every prompt-shape variant.
        assert ANCHOR_DISAMBIGUATION_CLAUSE in self.bridge_prompt
        assert ANCHOR_DISAMBIGUATION_CLAUSE in self.full_prompt

    def test_bridge_prompt_preserves_intent_types_block(self):
        # INTENT TYPES is the actual routing instruction surface — must
        # never be stripped, even in the Bridge-mode prompt.
        assert "INTENT TYPES" in self.bridge_prompt
        assert "INFORMATIONAL" in self.bridge_prompt
        assert "DIRECTIVE" in self.bridge_prompt

    def test_bridge_prompt_preserves_decision_rules(self):
        # DECISION RULES tells the model when to pick skills vs
        # interact_actions vs both vs neither. Core routing logic.
        assert "DECISION RULES" in self.bridge_prompt
        assert "skills only" in self.bridge_prompt
        assert "interact_actions only" in self.bridge_prompt

    def test_bridge_prompt_preserves_recap_as_informational_rule(self):
        # The "recap requests are always INFORMATIONAL" clause is
        # load-bearing — without it the conversational fast-path
        # swallows recap requests and the engine never runs.
        assert "INFORMATIONAL" in self.bridge_prompt
        assert "recap" in self.bridge_prompt.lower()

    def test_bridge_prompt_is_meaningfully_smaller(self):
        # Sanity check on the token savings — the optimisation should
        # cut at least 1000 characters (~250 tokens). If this fails the
        # factory probably isn't actually stripping the blocks it claims to.
        assert len(self.full_prompt) - len(self.bridge_prompt) >= 1000, (
            f"Bridge-mode prompt savings unexpectedly small: "
            f"full={len(self.full_prompt)} chars, "
            f"bridge={len(self.bridge_prompt)} chars, "
            f"delta={len(self.full_prompt) - len(self.bridge_prompt)} chars. "
            f"Expected at least 1000 chars saved."
        )


class TestBridgeUserPromptTemplate:
    """User-prompt template drops the POSTURE RULES recap in Bridge mode."""

    def setup_method(self):
        self.bridge_template = build_routing_user_prompt_template(
            include_posture_recap=False,
        )
        self.full_template = build_routing_user_prompt_template(
            include_posture_recap=True,
        )

    def test_bridge_template_omits_posture_rules_recap(self):
        assert "POSTURE RULES (recap)" not in self.bridge_template
        assert "POSTURE RULES (recap)" in self.full_template

    def test_bridge_template_omits_full_task_line(self):
        # Full template's TASK line walks the model through posture
        # classification first. Bridge template's TASK line goes straight
        # to intent + skills + interact_actions.
        assert "Classify posture (RESPOND/SUPPRESS/DEFER)" not in self.bridge_template

    def test_bridge_template_preserves_required_format_placeholders(self):
        # EngineRouter calls ``.format(**fields)`` on this template;
        # every placeholder it provides must still be present.
        required = [
            "{active_tasks_section}",
            "{history_section}",
            "{prior_fragments_section}",
            "{utterance}",
            "{skills_json}",
            "{interact_actions_json}",
            "{entity_field}",
            "{canned_field}",
            "{optional_instructions}",
        ]
        for ph in required:
            assert (
                ph in self.bridge_template
            ), f"Bridge template dropped required format placeholder {ph!r}"

    def test_bridge_template_preserves_output_schema(self):
        # The OUTPUT block is the JSON-schema instruction. The
        # ``parse_routing_response`` parser depends on these keys being
        # in the model output, which means they must be in the schema
        # the prompt advertises.
        assert "OUTPUT (JSON only)" in self.bridge_template
        assert "interpretation" in self.bridge_template
        assert "intent_type" in self.bridge_template
        assert '"skills"' in self.bridge_template
        assert '"interact_actions"' in self.bridge_template
        assert "confidence" in self.bridge_template

    def test_bridge_template_preserves_rules_block(self):
        # The numbered RULES block enforces the JSON-key/class-name
        # invariants. Stripping it would let the model emit anchor
        # descriptions instead of class names.
        assert "RULES:" in self.bridge_template
        assert "exact SKILLS CATALOG key" in self.bridge_template
        assert "exact INTERACT ACTIONS CATALOG key" in self.bridge_template


class TestFormatRendersCleanly:
    """The Bridge templates must render through ``.format()`` without errors."""

    def test_bridge_user_template_renders_with_typical_fields(self):
        template = build_routing_user_prompt_template(include_posture_recap=False)
        rendered = template.format(
            active_tasks_section="",
            history_section="(no history)\n",
            prior_fragments_section="",
            utterance="What is jvspatial?",
            skills_json='{"web_search": ["search the web"]}',
            interact_actions_json="{}",
            entity_field="",
            canned_field="",
            optional_instructions="",
        )
        assert "What is jvspatial?" in rendered
        assert "web_search" in rendered
        # No leftover placeholders should remain.
        assert "{" not in rendered or "{{" in template
