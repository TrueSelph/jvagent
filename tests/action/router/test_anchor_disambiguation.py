"""Tests for the anchor disambiguation clause cross-module invariant.

The clause was added in May 2026 after live-smoke showed
``"Help me prepare for an interview"`` mis-routed to a
``signup_interview_interact_action`` whose anchors described training
enrollment. The router LLM (gpt-4o-mini) latched onto the shared noun
"interview" rather than the verb-object intent, DELEGATEd the turn, and
the turn-locked signup IA then took ownership of every subsequent reply.

The mitigation is a single prompt clause that lives identically in BOTH
prompt modules that surface anchor matching to a model:

- ``jvagent.action.router.prompts`` (rails ``InteractRouter``)
- ``jvagent.action.helm.reflex.prompts`` (Bridge's ReflexHelm — ADR-0009
  moved the clause here from the deleted ``reasoning.routing.prompts``)

These tests pin three invariants:

1. Each module exports an ``ANCHOR_DISAMBIGUATION_CLAUSE`` symbol.
2. The two strings are byte-identical — drift between the two copies is
   a regression.
3. Each system prompt embeds the clause verbatim — a future edit that
   accidentally drops the clause text from either prompt fails the test.
"""

from __future__ import annotations

from jvagent.action.helm.reflex.prompts import (
    ANCHOR_DISAMBIGUATION_CLAUSE as REFLEX_ANCHOR_CLAUSE,
)
from jvagent.action.helm.reflex.prompts import (
    REFLEX_SYSTEM_PROMPT,
)
from jvagent.action.router.prompts import (
    ANCHOR_DISAMBIGUATION_CLAUSE as RAILS_ANCHOR_CLAUSE,
)
from jvagent.action.router.prompts import (
    ROUTER_SYSTEM_PROMPT as RAILS_ROUTER_SYSTEM_PROMPT,
)


class TestAnchorDisambiguationClauseInvariants:
    """Cross-module identity + embedding invariants."""

    def test_rails_and_reflex_clauses_are_byte_identical(self):
        """Drift between the two copies is a regression — both prompts must
        teach the LLM the exact same disambiguation rule so behaviour is
        consistent regardless of which path (rails router or Bridge's
        Reflex peer-awareness) is composing the turn."""
        assert RAILS_ANCHOR_CLAUSE == REFLEX_ANCHOR_CLAUSE, (
            "ANCHOR_DISAMBIGUATION_CLAUSE has drifted between "
            "jvagent.action.router.prompts and "
            "jvagent.action.helm.reflex.prompts. Restore them to "
            "byte-identical text."
        )

    def test_clause_is_embedded_in_rails_system_prompt(self):
        assert RAILS_ANCHOR_CLAUSE in RAILS_ROUTER_SYSTEM_PROMPT, (
            "ANCHOR_DISAMBIGUATION_CLAUSE was removed from "
            "ROUTER_SYSTEM_PROMPT (jvagent.action.router.prompts). "
            "Re-add it — without it, the rails router can mis-route "
            "utterances that share nouns with action anchors."
        )

    def test_clause_is_embedded_in_reflex_system_prompt(self):
        # REFLEX_SYSTEM_PROMPT carries a ``{anchor_disambiguation_clause}``
        # placeholder that the helm fills in at assembly time. Pin both:
        # the placeholder is present, AND the clause text appears in the
        # rendered prompt when the placeholder is substituted.
        rendered = REFLEX_SYSTEM_PROMPT.format(
            peer_helms_section="-",
            helms_available_section="",
            peer_actions_section="-",
            anchor_disambiguation_clause=REFLEX_ANCHOR_CLAUSE,
        )
        assert REFLEX_ANCHOR_CLAUSE in rendered, (
            "ANCHOR_DISAMBIGUATION_CLAUSE was removed from "
            "REFLEX_SYSTEM_PROMPT (jvagent.action.helm.reflex.prompts). "
            "Re-add it — without it, ReflexHelm peer-awareness can "
            "mis-route utterances that share nouns with action anchors."
        )


class TestAnchorDisambiguationClauseContent:
    """Pin the load-bearing pieces of the clause text.

    The clause's effectiveness depends on three rhetorical moves:

    1. A clear NAME for the rule the LLM should follow ("by INTENT, not
       by keywords").
    2. An imperative escape hatch ("Prefer an empty actions list").
    3. A concrete contrast example. Small router models internalise one
       worked example more reliably than abstract guidance.

    If any of these gets edited away, the clause's signal degrades —
    pin them.
    """

    def test_clause_has_intent_not_keywords_headline(self):
        assert "by INTENT, not by keywords" in RAILS_ANCHOR_CLAUSE

    def test_clause_has_empty_list_escape_hatch(self):
        assert (
            "Prefer an empty actions list" in RAILS_ANCHOR_CLAUSE
            or "empty actions list" in RAILS_ANCHOR_CLAUSE
        )

    def test_clause_has_concrete_contrast_example(self):
        assert "training signup interviews" in RAILS_ANCHOR_CLAUSE
        assert "help me prepare for a job interview" in RAILS_ANCHOR_CLAUSE

    def test_clause_uses_verb_object_framing(self):
        assert "verb + object" in RAILS_ANCHOR_CLAUSE
