"""Tests for the anchor disambiguation clause in the rails ``InteractRouter``.

The clause was added in May 2026 after live-smoke showed
``"Help me prepare for an interview"`` mis-routed to a
``signup_interview_interact_action`` whose anchors described training
enrollment. The router LLM (gpt-4o-mini) latched onto the shared noun
"interview" rather than the verb-object intent, DELEGATEd the turn, and
the turn-locked signup IA then took ownership of every subsequent reply.

The mitigation is a single prompt clause in ``jvagent.action.router.prompts``
(the rails ``InteractRouter``).

These tests pin two invariants:

1. The module exports an ``ANCHOR_DISAMBIGUATION_CLAUSE`` symbol.
2. The router system prompt embeds the clause verbatim — a future edit that
   accidentally drops the clause text fails the test.
"""

from __future__ import annotations

from jvagent.action.router.prompts import (
    ANCHOR_DISAMBIGUATION_CLAUSE as RAILS_ANCHOR_CLAUSE,
)
from jvagent.action.router.prompts import (
    ROUTER_SYSTEM_PROMPT as RAILS_ROUTER_SYSTEM_PROMPT,
)


class TestAnchorDisambiguationClauseInvariants:
    """Embedding invariant for the rails router prompt."""

    def test_clause_is_embedded_in_rails_system_prompt(self):
        assert RAILS_ANCHOR_CLAUSE in RAILS_ROUTER_SYSTEM_PROMPT, (
            "ANCHOR_DISAMBIGUATION_CLAUSE was removed from "
            "ROUTER_SYSTEM_PROMPT (jvagent.action.router.prompts). "
            "Re-add it — without it, the rails router can mis-route "
            "utterances that share nouns with action anchors."
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
