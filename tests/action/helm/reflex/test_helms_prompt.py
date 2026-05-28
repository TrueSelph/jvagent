"""Tests for the conditional ``HELMS AVAILABLE`` block (ADR-0009 §3).

Single-Reasoning agents see no block — prompt budget stays tight.
Multi-deliberate-helm agents see a routing surface that lets Reflex
SHIFT to the specialist helm whose purpose matches the user's intent.
"""

from __future__ import annotations

from jvagent.action.helm.reflex.prompts import (
    REFLEX_SYSTEM_PROMPT,
    render_helms_available_block,
)


class TestHelmsAvailableBlock:
    def test_empty_when_no_deliberate_helms(self):
        out = render_helms_available_block([])
        assert out == ""

    def test_empty_when_one_deliberate_helm(self):
        out = render_helms_available_block(
            [{"name": "ReasoningHelm", "purpose": "general reasoning"}]
        )
        assert out == ""

    def test_renders_when_two_helms(self):
        out = render_helms_available_block(
            [
                {"name": "ReasoningHelm", "purpose": "general reasoning"},
                {"name": "MathSpecialistHelm", "purpose": "math problems"},
            ]
        )
        assert "HELMS AVAILABLE for substantive turns:" in out
        assert "ReasoningHelm: general reasoning" in out
        assert "MathSpecialistHelm: math problems" in out
        assert "SHIFT to that helm" in out

    def test_renders_when_three_helms(self):
        out = render_helms_available_block(
            [
                {"name": "ReasoningHelm", "purpose": "general"},
                {"name": "MathHelm", "purpose": "math"},
                {"name": "CodeReviewHelm", "purpose": "code"},
            ]
        )
        for n in ("ReasoningHelm", "MathHelm", "CodeReviewHelm"):
            assert n in out

    def test_empty_purpose_renders_placeholder(self):
        out = render_helms_available_block(
            [
                {"name": "A", "purpose": ""},
                {"name": "B", "purpose": "real"},
            ]
        )
        assert "A: (no purpose declared)" in out

    def test_no_name_is_skipped(self):
        out = render_helms_available_block(
            [
                {"name": "", "purpose": "anon"},
                {"name": "B", "purpose": "named"},
            ]
        )
        # Both entries pass the ≥2 length gate, but the empty-name one is
        # dropped from the rendered lines.
        assert ": anon" not in out
        assert "B: named" in out


class TestSystemPromptEmbedsHelmsBlock:
    def test_block_substitutes_into_template(self):
        block = render_helms_available_block(
            [
                {"name": "ReasoningHelm", "purpose": "p1"},
                {"name": "MathHelm", "purpose": "p2"},
            ]
        )
        out = REFLEX_SYSTEM_PROMPT.format(
            peer_helms_section="-",
            helms_available_section=block,
            peer_actions_section="-",
            anchor_disambiguation_clause="(clause)",
        )
        assert "HELMS AVAILABLE for substantive turns:" in out
        assert "MathHelm: p2" in out

    def test_empty_block_keeps_prompt_tight(self):
        out = REFLEX_SYSTEM_PROMPT.format(
            peer_helms_section="-",
            helms_available_section="",
            peer_actions_section="-",
            anchor_disambiguation_clause="(clause)",
        )
        assert "HELMS AVAILABLE" not in out
