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
    assert len(response_parameters(caps)) == 5
    # orchestration: untrusted-input handling
    assert len(orchestration_parameters(caps)) == 1
    # default factory hands out independent copies (safe as an attribute default)
    caps[0]["response"] = "mutated"
    assert CORE_PARAMETERS[0]["response"] != "mutated"


def test_native_core_split():
    # the two native owners take their own scope's subset
    assert len(orchestrator_core_parameters()) == 1  # orchestration
    assert all(p["scope"] == "orchestration" for p in orchestrator_core_parameters())
    assert len(reply_core_parameters()) == 5  # response
    assert all(p["scope"] == "response" for p in reply_core_parameters())


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
