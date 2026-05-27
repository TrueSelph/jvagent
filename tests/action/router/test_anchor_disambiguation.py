"""Tests for the anchor disambiguation clause in router prompts.

The clause was added in May 2026 after live-smoke showed
``"Help me prepare for an interview"`` mis-routed to a
``signup_interview_interact_action`` whose anchors described training
enrollment. The router LLM (gpt-4o-mini) latched onto the shared noun
"interview" rather than the verb-object intent, DELEGATEd the turn, and
the turn-locked signup IA then took ownership of every subsequent reply.

The mitigation is a single prompt clause that lives identically in BOTH
router prompt modules:

- ``jvagent.action.router.prompts`` (rails ``InteractRouter``)
- ``jvagent.action.helm.reasoning.routing.prompts`` (helm ``CockpitRouter``)

These tests pin three invariants:

1. Each module exports an ``ANCHOR_DISAMBIGUATION_CLAUSE`` symbol.
2. The two strings are byte-identical — drift between the two copies is
   a regression.
3. Each system prompt embeds the clause verbatim — a future edit that
   accidentally drops the clause text from either prompt fails the test.

If a future change wants to refactor the clause (e.g. extract it to a
shared module under ``jvagent.action.routing``), update the imports here
and the cross-module identity invariant will continue to hold.
"""

from __future__ import annotations

from jvagent.action.helm.reasoning.routing.prompts import (
    ANCHOR_DISAMBIGUATION_CLAUSE as COCKPIT_ANCHOR_CLAUSE,
)
from jvagent.action.helm.reasoning.routing.prompts import (
    ROUTING_SYSTEM_PROMPT as COCKPIT_ROUTER_SYSTEM_PROMPT,
)
from jvagent.action.router.prompts import (
    ANCHOR_DISAMBIGUATION_CLAUSE as RAILS_ANCHOR_CLAUSE,
)
from jvagent.action.router.prompts import (
    ROUTER_SYSTEM_PROMPT as RAILS_ROUTER_SYSTEM_PROMPT,
)


class TestAnchorDisambiguationClauseInvariants:
    """Cross-module identity + embedding invariants."""

    def test_rails_and_cockpit_clauses_are_byte_identical(self):
        """Drift between the two copies is a regression — both router
        prompts must teach the LLM the exact same disambiguation rule
        so behaviour is consistent regardless of which pattern (rails or
        Bridge/Helm) is composing the turn."""
        assert RAILS_ANCHOR_CLAUSE == COCKPIT_ANCHOR_CLAUSE, (
            "ANCHOR_DISAMBIGUATION_CLAUSE has drifted between "
            "jvagent.action.router.prompts and "
            "jvagent.action.helm.reasoning.routing.prompts. Restore them to "
            "byte-identical text."
        )

    def test_clause_is_embedded_in_rails_system_prompt(self):
        assert RAILS_ANCHOR_CLAUSE in RAILS_ROUTER_SYSTEM_PROMPT, (
            "ANCHOR_DISAMBIGUATION_CLAUSE was removed from "
            "ROUTER_SYSTEM_PROMPT (jvagent.action.router.prompts). "
            "Re-add it — without it, the rails router can mis-route "
            "utterances that share nouns with action anchors."
        )

    def test_clause_is_embedded_in_cockpit_system_prompt(self):
        assert COCKPIT_ANCHOR_CLAUSE in COCKPIT_ROUTER_SYSTEM_PROMPT, (
            "ANCHOR_DISAMBIGUATION_CLAUSE was removed from "
            "ROUTING_SYSTEM_PROMPT "
            "(jvagent.action.helm.reasoning.routing.prompts). Re-add it — "
            "without it, the cockpit router (used by ReasoningHelm) can "
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
        # The headline frames the rule for the LLM at the top of the section.
        assert "by INTENT, not by keywords" in RAILS_ANCHOR_CLAUSE

    def test_clause_has_empty_list_escape_hatch(self):
        # Without the explicit "prefer empty actions list" rule the LLM
        # tends to pick *something* even on low confidence — a known
        # gpt-4o-mini behaviour. This phrase is the escape hatch.
        assert (
            "Prefer an empty actions list" in RAILS_ANCHOR_CLAUSE
            or "empty actions list" in RAILS_ANCHOR_CLAUSE
        )

    def test_clause_has_concrete_contrast_example(self):
        # The contrast example shipped with the clause is the actual
        # failure observed in live smoke (May 2026). Editing it out
        # removes the most reliable signal in the prompt.
        assert "training signup interviews" in RAILS_ANCHOR_CLAUSE
        assert "help me prepare for a job interview" in RAILS_ANCHOR_CLAUSE

    def test_clause_uses_verb_object_framing(self):
        # The "verb + object" framing forces the LLM to compare predicate
        # structures rather than vocabulary. Without this phrasing the
        # clause becomes a generic "be careful" warning, which small
        # models routinely ignore.
        assert "verb + object" in RAILS_ANCHOR_CLAUSE
