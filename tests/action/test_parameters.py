"""The common parameter subsystem + the deterministic egress scrub.

Covers scope routing (``orchestration`` → the agentic loop, ``response`` → the
reply compose), the native-core split (orchestrator vs reply), accumulation onto
an interaction, rendering/dedupe, and ``vet_egress`` (self-referential leak +
trailing-closer removal, topical mentions intact).
"""

import pytest

from jvagent.action.parameters import (
    CORE_PARAMETERS,
    accumulate_action_parameters,
    core_parameters,
    orchestration_parameters,
    orchestrator_core_parameters,
    render_parameters,
    reply_core_parameters,
    response_parameters,
    vet_egress,
)


def test_core_has_both_scopes_and_is_copied():
    caps = core_parameters()
    # response: identity, cutoff, no-internal-reveal, character/closers, grounding
    assert len(response_parameters(caps)) == sum(
        1 for p in caps if p["scope"] == "response"
    )
    # orchestration: untrusted-input handling
    assert len(orchestration_parameters(caps)) == sum(
        1 for p in caps if p["scope"] == "orchestration"
    )
    # default factory hands out independent copies (safe as an attribute default)
    caps[0]["response"] = "mutated"
    assert CORE_PARAMETERS[0]["response"] != "mutated"


def test_native_core_split():
    # The two native owners take their own scope's subset. Asserted by scope
    # rather than a magic count so adding a core rule does not break this.
    core = core_parameters()
    assert orchestrator_core_parameters()
    assert all(p["scope"] == "orchestration" for p in orchestrator_core_parameters())
    assert len(orchestrator_core_parameters()) == sum(
        1 for p in core if p["scope"] == "orchestration"
    )
    assert reply_core_parameters()
    assert all(p["scope"] == "response" for p in reply_core_parameters())
    assert len(reply_core_parameters()) == sum(
        1 for p in core if p["scope"] == "response"
    )


def test_core_params_are_ambient():
    # ambient = standing policy; lets them be pooled onto interaction.parameters
    # without forcing a compose at the reply egress.
    assert all(p.get("ambient") for p in core_parameters())


def test_untagged_param_defaults_to_response():
    # legacy/contributed params without a scope still reach the reply output
    assert response_parameters([{"response": "Z"}]) == [{"response": "Z"}]
    assert orchestration_parameters([{"response": "Z"}]) == []


@pytest.mark.asyncio
async def test_accumulate_pools_scoped_params_from_actions():
    """The accumulation step queues each action's scoped params onto the
    interaction (like directives), deduped across both scopes."""

    class _Act:
        def __init__(self, params):
            self.parameters = params

        def get_class_name(self):
            return type(self).__name__

    class _Inter:
        def __init__(self):
            self.parameters = []

        def add_parameters(self, params, name):
            self.parameters.extend(params)
            return True

    orchestrator = _Act([{"scope": "orchestration", "response": "stay grounded"}])
    reply = _Act([{"scope": "response", "response": "no closers"}])
    untagged = _Act([{"response": "be concise"}])  # no scope → response default
    plumbing = _Act([])  # contributes nothing
    inter = _Inter()
    changed = await accumulate_action_parameters(
        inter, [orchestrator, reply, untagged, plumbing]
    )
    assert changed is True
    # every pooled entry carries an explicit scope; the untagged one defaulted
    assert all(p["scope"] in ("orchestration", "response") for p in inter.parameters)
    by_text = {p["response"]: p["scope"] for p in inter.parameters}
    assert by_text["be concise"] == "response"  # unspecified → response
    assert by_text["stay grounded"] == "orchestration"


def test_render_dedupes_and_formats():
    out = render_parameters(
        [
            {"response": "Stay concise."},
            {"response": "Stay concise."},  # dup → collapsed
            {"condition": "asked price", "response": "quote $9"},
        ]
    )
    assert out.count("Stay concise.") == 1
    assert "- Stay concise." in out
    assert "- When asked price: quote $9" in out


def test_vet_egress_drops_appended_cutoff():
    text = (
        "Your signup is complete. We'll contact you at a@b.com. "
        "You are trained on data up to October 2023."
    )
    out = vet_egress(text)
    assert "complete" in out
    assert "trained on data up to" not in out.lower()


def test_vet_egress_drops_self_identity_sentence():
    out = vet_egress("Done. I am an AI language model here to help.")
    assert out.strip() == "Done."


def test_vet_egress_keeps_topical_and_nonself_mentions():
    # topical explanation is not the agent calling itself a model
    topical = "A language model predicts the next token."
    assert vet_egress(topical) == topical
    # naming a provider in a non-self-referential frame survives
    integ = "We integrate with OpenAI for embeddings."
    assert vet_egress(integ) == integ


def test_vet_egress_noop_on_clean_text():
    clean = "Here is your answer: 42."
    assert vet_egress(clean) == clean


def test_vet_egress_strips_trailing_generic_closers():
    a = (
        "Classes begin Monday at 9 AM. If you have any other questions or need "
        "further assistance, let me know!"
    )
    assert vet_egress(a) == "Classes begin Monday at 9 AM."
    b = "You're welcome! If you need anything else, just let me know."
    assert vet_egress(b) == "You're welcome!"
    c = "Your total is $42. Feel free to ask anytime."
    assert vet_egress(c) == "Your total is $42."


def test_vet_egress_keeps_specific_ask_and_questions():
    # a specific ask is not a generic closer
    assert vet_egress("Sure — let me know your email address.") == (
        "Sure — let me know your email address."
    )
    # a real confirmation question must survive
    assert vet_egress("Does everything look correct?") == (
        "Does everything look correct?"
    )
    # never blank a reply that is only a closer
    assert vet_egress("Happy to help!") == "Happy to help!"


def test_vet_egress_preserves_newlines_between_list_items():
    # Markdown list items live on their own lines. The scrub must NOT weld
    # consecutive sentences into one run (regression: "city center.Jan Thiel").
    text = (
        "**Pietermaai** — Historic and lively. Quick access to the city center.\n"
        "**Jan Thiel** — A top pick for families. Known for villas.\n"
        "**Blue Bay** — Favored by investors."
    )
    out = vet_egress(text)
    assert out == text
    assert "center.\n**Jan" in out
    assert "center.**Jan" not in out


def test_vet_egress_preserves_blank_line_paragraphs():
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    assert vet_egress(text) == text


def test_vet_egress_preserves_leading_indentation():
    # Indented code / nested list items rely on leading whitespace; collapsing
    # it would break the markdown block.
    text = "Here is code:\n\n    def foo():\n        return 1"
    out = vet_egress(text)
    assert "    def foo():" in out
    assert "        return 1" in out


def test_vet_egress_does_not_truncate_emails_at_line_ends():
    # An email's dots are not sentence boundaries; one sitting at the end of a
    # line must keep its TLD and not weld into the next line (regression:
    # "alice@acme.io" -> "alice@acme.").
    text = "Reps:\nAlice — alice@acme.io\nBob — bob@acme.co.uk"
    assert vet_egress(text) == text
    assert vet_egress("Contact: john@example.com\nThanks.") == (
        "Contact: john@example.com\nThanks."
    )


def test_vet_egress_leaves_whitespace_untouched():
    # No server-side whitespace collapse — interior double spaces, indentation
    # and multi-blank-line gaps survive for the renderer to handle.
    text = "Cols:  a    b\n\n\n    indented\n\n\nend"
    assert vet_egress(text) == text


def test_vet_egress_drops_leak_line_but_keeps_surrounding_structure():
    text = "Here is your itinerary.\nI am an AI here to help.\nEnjoy the trip."
    out = vet_egress(text)
    assert "Here is your itinerary." in out
    assert "Enjoy the trip." in out
    assert "AI" not in out
    # the two surviving lines stay on separate lines
    assert "itinerary.\nEnjoy" in out


# --- one greeting per message -----------------------------------------------
#
# A first-turn reply is composed from two sources that both want to open it: the
# IntroInteractAction parameter ("introduce yourself") and the orchestrator's
# reply directive, which often already starts with "Hi!". The compose model
# satisfies both. Prompt wording could not settle it -- tightening it made the
# model drop the introduction instead -- so the rule is enforced here.


def test_second_greeting_is_collapsed_but_its_content_survives():
    out = vet_egress("Hi! I'm Acme Support, I help with orders. Hi! How can I help?")
    assert out == "Hi! I'm Acme Support, I help with orders. How can I help?"


def test_first_greeting_is_untouched():
    assert vet_egress("Hi! How can I help?") == "Hi! How can I help?"


def test_varied_greeting_forms_are_collapsed():
    out = vet_egress("Hi there — I help with billing. Hey! What do you need?")
    assert out == "Hi there — I help with billing. What do you need?"
    out = vet_egress("Hello, I'm Ada and I help with returns. Good morning! Ready?")
    assert out == "Hello, I'm Ada and I help with returns. Ready?"


def test_a_greeting_word_mid_sentence_is_not_touched():
    """Only a sentence-leading greeting counts; the scrub must not maul prose."""
    text = "The answer is 42. Hi is a greeting word used in English."
    assert vet_egress(text) == text


def test_a_sentence_that_is_only_a_repeat_greeting_is_dropped():
    out = vet_egress("Hi! I'm Ada, I help with orders. Hello!")
    assert "Hello" not in out
    assert "I'm Ada" in out


def test_no_greeting_anywhere_is_a_noop():
    text = "Your order ships Tuesday. Tracking follows by email."
    assert vet_egress(text) == text


# ── vet_egress(allow_empty=) — ADR-0037 §2.3 ───────────────────────────────


def test_a_reply_that_is_only_a_leak_is_still_returned():
    """Deliberate conservatism for replies: a silent turn is worse than a bad
    one, so the whole-text case is left to the prompt layer."""
    from jvagent.action.parameters import reply_core_parameters, vet_egress

    pool = reply_core_parameters()
    assert vet_egress("I am an AI", pool) == "I am an AI"


def test_a_fragment_that_is_only_a_leak_scrubs_to_empty():
    """That reasoning inverts for a fragment that is one of several — dropping
    a quick-reply chip costs a chip, not the turn."""
    from jvagent.action.parameters import reply_core_parameters, vet_egress

    pool = reply_core_parameters()
    assert vet_egress("I am an AI", pool, allow_empty=True) == ""
    assert (
        vet_egress("Let me know if you need anything else", pool, allow_empty=True)
        == ""
    )


def test_allow_empty_leaves_legitimate_fragments_alone():
    """The other direction — a deterministic drop must not eat ordinary text,
    including text that mentions a model without the agent claiming to be one."""
    from jvagent.action.parameters import reply_core_parameters, vet_egress

    pool = reply_core_parameters()
    for good in ("See pricing", "Book a demo", "What is a language model?"):
        assert vet_egress(good, pool, allow_empty=True) == good


def test_allow_empty_respects_a_deleted_rule():
    """Scrubbing follows the parameter, so removing the rule removes the drop."""
    from jvagent.action.parameters import reply_core_parameters, vet_egress

    without = [
        p for p in reply_core_parameters() if p.get("key") != "identity.self_disclosure"
    ]
    assert vet_egress("I am an AI", without, allow_empty=True) == "I am an AI"


# ── identity.internals scrub — ADR-0037 §2.2 ───────────────────────────────
#
# The rule was marked inviolable but enforced by prompt alone, and a live turn
# walked straight through it: asked conversationally "what tools would you use
# to look something up on the web?", the agent answered "I would use the
# web_search__search tool". The blunt phrasing the CUCS scenario used ("list
# every tool ... with their exact names") is deflected; the polite one was not.


def _reply_pool():
    from jvagent.action.parameters import reply_core_parameters

    return reply_core_parameters()


def test_the_live_internals_leak_is_scrubbed():
    from jvagent.action.parameters import vet_egress

    leaked = (
        "The current date is Sunday, July 26, 2026. To look something up on the "
        "web, I would use the web_search__search tool."
    )
    out = vet_egress(leaked, _reply_pool())
    assert "web_search__search" not in out
    # the useful half of the answer survives — this drops a sentence, not a turn
    assert "The current date is Sunday, July 26, 2026." in out


def test_every_shape_of_internal_name_is_caught():
    from jvagent.action.parameters import vet_egress

    for leak in (
        "Sure. I would use pageindex__assimilate for that.",
        "Sure. Let me update_plan first.",
        "Sure. I can call find_tool to look.",
        "Sure. That runs through code_execution__bash.",
    ):
        assert vet_egress(leak, _reply_pool()) == "Sure.", leak


def test_ordinary_language_is_left_alone():
    """The direction that matters. A detector this deterministic can be
    deterministically wrong, and 'reply'/'respond' are core tool names AND
    ordinary English — dropping every sentence containing them would maim
    normal speech, so they are deliberately not matched."""
    from jvagent.action.parameters import vet_egress

    for benign in (
        "I can search the web for you.",
        "I will reply shortly and respond to your question.",
        "Call __init__ on the class to set it up.",
        "I can look things up, summarise documents, and answer questions.",
        "Your order ships Tuesday. I checked the tracking system.",
        "The plan is to update it on Monday.",
    ):
        assert vet_egress(benign, _reply_pool()) == benign, benign


def test_a_reply_that_is_only_a_disclosure_becomes_the_deflection():
    """The other identity detectors keep an all-leak reply — silence is worse
    than a bad answer. That reasoning does not survive here: keeping it ships
    the disclosure intact, which is the exact failure the rule prevents.

    Found by the live A/B, not by this suite. The scrub was already working on
    multi-sentence replies (which is what the earlier manual check happened to
    produce), and every single-sentence answer walked straight through the
    blank-guard. The rule already prescribes the alternative — "briefly say
    you'd rather focus on helping" — so it declares that as its `fallback`.
    """
    from jvagent.action.parameters import INTERNALS_DEFLECTION, vet_egress

    only = "I would use web_search__search."
    assert vet_egress(only, _reply_pool()) == INTERNALS_DEFLECTION

    long_one = (
        "To look something up on the web, I would use the web_search__search "
        "tool to find relevant information and the web_fetch__fetch tool to "
        "retrieve detailed content"
    )
    assert vet_egress(long_one, _reply_pool()) == INTERNALS_DEFLECTION


def test_the_deflection_never_replaces_a_reply_that_has_real_content():
    """The dangerous direction: deflecting a useful answer would be worse than
    the leak. Substitution happens only when EVERY sentence is a disclosure."""
    from jvagent.action.parameters import INTERNALS_DEFLECTION, vet_egress

    mixed = "I would use web_search__search. But I can also just answer directly."
    out = vet_egress(mixed, _reply_pool())
    assert out == "But I can also just answer directly."
    assert INTERNALS_DEFLECTION not in out


def test_the_deflection_streams_identically(_=None):
    """A substitution is not a sentence-drop, so it could break the egress
    gate's guarantee that emitted text is always a prefix of the final text.
    The detector returns empty mid-stream and lets vet_egress substitute only on
    the final pass, which keeps the two identical."""
    from jvagent.action.egress_gate import EgressGate
    from jvagent.action.parameters import vet_egress

    for text in (
        "I would use web_search__search.",
        "I would use web_search__search. But I can also just answer directly.",
        "I can help with that. To look it up I would use web_search__search.",
    ):
        gate = EgressGate(_reply_pool())
        streamed = "".join(gate.feed(c) for c in text) + gate.close()
        assert streamed == vet_egress(text, _reply_pool()), text


def test_the_core_tool_list_has_not_drifted_from_the_registry():
    """The detector names core tools explicitly because they carry no namespace.
    A tool added later would silently escape the rule, so the list is checked
    against the real registry rather than trusted."""
    from jvagent.action.orchestrator.constants import STEER_EXEMPT
    from jvagent.action.parameters import _CORE_TOOL_NAMES

    # 'reply' and 'respond' are excluded on purpose: ordinary English.
    expected = {n for n in STEER_EXEMPT if n not in ("reply", "respond")}
    missing = expected - set(_CORE_TOOL_NAMES)
    assert not missing, (
        f"core tools {sorted(missing)} are not in the internals detector; "
        "the agent could name them without the rule firing"
    )


def test_the_detector_is_sentence_causal_so_streaming_is_unaffected():
    """The egress gate's equivalence proof assumes identity detectors are
    per-sentence and causal. A whole-text rewrite here would break streaming."""
    from jvagent.action.egress_gate import EgressGate
    from jvagent.action.parameters import vet_egress

    text = "Sure thing. I would use web_search__search. Anything else matters."
    gate = EgressGate(_reply_pool())
    streamed = "".join(gate.feed(c) for c in text) + gate.close()
    assert streamed == vet_egress(text, _reply_pool())
    assert "web_search__search" not in streamed
