"""A reply may not claim a source the turn never consulted.

Live failure: after research was assimilated, the agent answered "what was
Eldon's most recent workshop?" with no tool call at all (a guess), then answered
"was this from the knowledge base?" with "yes, retrieved from the knowledge
base" — a false statement about its own provenance, which is worse than the
guess. Both turns ran one light tick and invoked only `reply`.

The response parameters already forbid unverified claims. This enforces the one
case that is machine-checkable: the loop knows no tool ran.
"""

from __future__ import annotations

from jvagent.action.orchestrator.loop_helpers import unsupported_source_claim

# --- detector ---------------------------------------------------------------


def test_assertions_of_retrieval_are_caught():
    for text in (
        "Yes, the information was retrieved from the knowledge base we created.",
        "I searched the knowledge base and found three documents.",
        "According to the knowledge base, he founded two companies.",
        "Based on the search results, the workshop was in March.",
        "The search results show two entries.",
        "That came from the knowledge base.",
    ):
        assert unsupported_source_claim(text), text


def test_offers_and_plans_are_not_claims():
    """Only assertions are guarded — an agent must stay free to offer a lookup."""
    for text in (
        "I can search the knowledge base if you like.",
        "Would you like me to look in the knowledge base?",
        "I'll check the knowledge base next.",
        "Shall I consult the documents?",
        "If you want, I could search the knowledge base.",
    ):
        assert not unsupported_source_claim(text), text


def test_ordinary_answers_are_untouched():
    for text in (
        "Eldon's most recent workshop focused on digital marketing.",
        "Your order ships Tuesday.",
        "",
    ):
        assert not unsupported_source_claim(text), text


# --- loop wiring ------------------------------------------------------------


def _orchestrator():
    from jvagent.action.orchestrator.orchestrator_interact_action import (
        OrchestratorInteractAction,
    )

    return OrchestratorInteractAction()


def test_guard_fires_only_when_no_substantive_tool_ran():
    ex = _orchestrator()
    claim = "Yes, that was retrieved from the knowledge base."

    nudge = ex._grounding_deflection(claim, 0)
    assert nudge is not None
    assert "not called ANY tool" in nudge["observation"]
    assert claim in nudge["observation"]

    # A turn that actually did work is not second-guessed about which tool.
    assert ex._grounding_deflection(claim, 1) is None


def test_guard_ignores_a_reply_making_no_claim():
    ex = _orchestrator()
    assert ex._grounding_deflection("The workshop was about marketing.", 0) is None


def test_guard_can_be_disabled():
    ex = _orchestrator()
    ex.enforce_grounded_claims = False
    assert ex._grounding_deflection("I searched the knowledge base.", 0) is None


# --- fabricated specifics ---------------------------------------------------
#
# The claim-detector only sees replies that assert a source. A live turn answered
# "where did he teach?" with "Eldon Marks taught at the University of Toronto"
# after retrieving nothing — no source claim, pure fabrication. If the turn
# called no tool, every specific in the reply must already be in what the agent
# can see.


def _corpus():
    return (
        "user: Who is Eldon Marks?\n"
        "Eldon Marks is a founder, innovator and collectivist with two decades "
        "in tech. He has worked as an academic, practitioner and serial "
        "entrepreneur, mentoring thousands."
    )


def test_invented_place_is_caught():
    from jvagent.action.orchestrator.loop_helpers import unsupported_specifics

    assert (
        unsupported_specifics(
            "Eldon Marks taught at the University of Toronto.", _corpus()
        )
        == "University of Toronto"
    )


def test_invented_year_is_caught():
    from jvagent.action.orchestrator.loop_helpers import unsupported_specifics

    assert unsupported_specifics("He founded it in 1998.", _corpus()) == "1998"


def test_paraphrase_from_context_is_left_alone():
    """The turn that summarised the retrieved text without new specifics was
    legitimate and must not be blocked."""
    from jvagent.action.orchestrator.loop_helpers import unsupported_specifics

    for text in (
        "Before becoming an entrepreneur, Eldon Marks worked as an academic and "
        "practitioner in the tech industry.",
        "He mentored thousands and founded companies.",
        "Eldon Marks is a founder and innovator.",
    ):
        assert unsupported_specifics(text, _corpus()) == "", text


def test_specifics_present_in_context_are_fine():
    from jvagent.action.orchestrator.loop_helpers import unsupported_specifics

    corpus = _corpus() + " He spoke at the WE3A Conference in 2024."
    assert unsupported_specifics("He spoke at the WE3A Conference.", corpus) == ""
    assert unsupported_specifics("That was in 2024.", corpus) == ""


def test_guard_wires_specifics_into_the_loop():
    ex = _orchestrator()
    nudge = ex._grounding_deflection(
        "Eldon Marks taught at the University of Toronto.", 0, _corpus()
    )
    assert nudge is not None
    assert "University of Toronto" in nudge["observation"]
    assert "does not appear anywhere" in nudge["observation"]

    # A turn that actually retrieved is not second-guessed.
    assert (
        ex._grounding_deflection(
            "Eldon Marks taught at the University of Toronto.", 1, _corpus()
        )
        is None
    )


def test_specifics_check_can_be_disabled_independently():
    ex = _orchestrator()
    ex.enforce_grounded_specifics = False
    assert (
        ex._grounding_deflection(
            "He taught at the University of Toronto.", 0, _corpus()
        )
        is None
    )
    # The source-claim half still applies.
    assert ex._grounding_deflection("I searched the knowledge base.", 0, "") is not None


def test_corpus_includes_history_and_observations():
    ex = _orchestrator()
    corpus = ex._grounding_corpus(
        "who is he?",
        [{"role": "assistant", "content": "He spoke at the WE3A Conference."}],
        [{"tool": "pageindex__search", "args": {}, "observation": "Founded in 2019."}],
    )
    assert "WE3A Conference" in corpus
    assert "2019" in corpus


# --- the WIRING, not just the helper ----------------------------------------
#
# The helper was correct while the reply path still called it with an empty
# corpus, which short-circuits to "nothing found". Everything passed and the
# agent went on inventing a university. These drive the real loop.


async def _run(make_orchestrator, make_visitor, monkeypatch, decisions, history):
    from jvagent.action.reply.reply_action import ReplyAction

    reply = ReplyAction()
    ex = make_orchestrator(actions=[reply], decisions=decisions)

    async def _pipe(self, text, interaction, visitor, streaming=False, transient=False):
        interaction.response = (interaction.response or "") + text

    monkeypatch.setattr(ReplyAction, "_pipe_response", _pipe)

    visitor = make_visitor(utterance="Where did he teach")

    async def _history(**_kw):
        return history

    visitor.conversation.get_interaction_history = _history
    await ex.execute(visitor)
    return visitor


async def test_reply_path_deflects_a_fabricated_specific(
    make_orchestrator, make_visitor, monkeypatch
):
    """The exact live failure: 'Where did he teach' answered with a university
    that appears nowhere, via the reply tool, with no tool call."""
    history = [
        {
            "role": "assistant",
            "content": (
                "Eldon Marks is a founder and innovator with two decades in tech. "
                "He has worked as an academic, practitioner and serial entrepreneur."
            ),
        }
    ]
    decisions = [
        {
            "action": "tool",
            "tool": "reply",
            "args": {"text": "Eldon Marks taught at the University of Toronto."},
        },
        {
            "action": "tool",
            "tool": "reply",
            "args": {"text": "I don't have that detail in what I've looked at."},
        },
        {"action": "final", "answer": ""},
    ]
    visitor = await _run(
        make_orchestrator, make_visitor, monkeypatch, decisions, history
    )
    response = visitor.interaction.response or ""
    assert "University of Toronto" not in response
    assert "don't have that detail" in response


async def test_reply_path_lets_a_grounded_answer_through(
    make_orchestrator, make_visitor, monkeypatch
):
    """A reply built from what the conversation already contains is not blocked."""
    history = [
        {
            "role": "assistant",
            "content": "Eldon Marks has worked as an academic and practitioner.",
        }
    ]
    decisions = [
        {
            "action": "tool",
            "tool": "reply",
            "args": {"text": "He worked as an academic and practitioner."},
        },
        {"action": "final", "answer": ""},
    ]
    visitor = await _run(
        make_orchestrator, make_visitor, monkeypatch, decisions, history
    )
    assert "academic and practitioner" in (visitor.interaction.response or "")
