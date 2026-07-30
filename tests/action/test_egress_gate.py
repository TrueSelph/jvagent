"""The egress invariant: output depends on the text, never on the transport.

The bug that motivated this module was a reply that came out clean over the
REST API and dirty over the messenger — same agent, same text, different
transport. So the central test is not "does the gate scrub" but "does chunking
change anything", checked over *every* split point of each sample rather than
one hand-picked one.
"""

import itertools

import pytest

from jvagent.action.egress_gate import EgressGate, scrub_text
from jvagent.action.parameters import reply_core_parameters, vet_egress

# Texts chosen to exercise each detector and, importantly, the interactions
# between them: a leak mid-message, a trailing closer, a closer that is NOT
# trailing, the duplicate greeting from the live bug, and clean text that must
# survive untouched.
SAMPLES = [
    "Hello! I am Orchestrator Agent, here to help you with your needs. "
    "Hello! How can I assist you today?",
    "Hi there! I'm Acme Support. Hi! How can I help today?",
    "Your order ships Tuesday. Let me know if you need anything else.",
    "Feel free to ask. Your order ships Tuesday.",
    "Your order ships Tuesday. Feel free to ask. Anything else I can do?",
    "I am an AI language model. Your order ships Tuesday.",
    "Your order ships Tuesday.",
    "I am an AI.",
    "Let me know if you need anything else.",
    "My training data ends in 2023. But your order ships Tuesday.",
    "What is a language model? It's a tool we can discuss.",
    "",
    "Hello!",
]


def _pool():
    return reply_core_parameters()


def _stream(text, splits):
    """Feed *text* through the gate broken at *splits*; return what was sent."""
    gate = EgressGate(_pool())
    out = []
    prev = 0
    for point in list(splits) + [len(text)]:
        out.append(gate.feed(text[prev:point]))
        prev = point
    out.append(gate.close())
    return "".join(out)


# --- the invariant -------------------------------------------------------


@pytest.mark.parametrize("text", SAMPLES)
def test_every_single_split_point_matches_the_whole_text_scrub(text):
    """One chunk boundary, tried at every position in the string."""
    expected = vet_egress(text, _pool())
    for i in range(len(text) + 1):
        assert _stream(text, [i]) == expected, f"split at {i} of {text!r}"


@pytest.mark.parametrize("text", SAMPLES[:6])
def test_every_pair_of_split_points_matches(text):
    """Two boundaries — catches state that survives one chunk but not two."""
    expected = vet_egress(text, _pool())
    for a, b in itertools.combinations(range(len(text) + 1), 2):
        assert _stream(text, [a, b]) == expected, f"splits {a},{b} of {text!r}"


@pytest.mark.parametrize("text", SAMPLES)
def test_character_at_a_time_matches(text):
    """The worst case: one character per chunk, as a token stream approaches."""
    assert _stream(text, list(range(len(text)))) == vet_egress(text, _pool())


@pytest.mark.parametrize("text", SAMPLES)
def test_scrub_text_equals_vet_egress(text):
    """The non-streaming path is the gate too, so it cannot drift."""
    assert scrub_text(text, _pool()) == vet_egress(text, _pool())


# --- the live bug --------------------------------------------------------


def test_the_duplicate_greeting_from_the_messenger_is_fixed_when_streamed():
    """The reply that exposed all of this. It was correct over REST and wrong
    over the messenger; the point is that it is now the same either way."""
    text = (
        "Hello! I am Orchestrator Agent, here to help you with your needs. "
        "Hello! How can I assist you today?"
    )
    streamed = _stream(text, list(range(len(text))))
    assert streamed.count("Hello") == 1
    assert "How can I assist you today?" in streamed
    assert streamed == vet_egress(text, _pool())


# --- withholding rules ---------------------------------------------------


def test_a_trailing_closer_is_never_emitted_early():
    """The gate must not release a closer that the end of the message will
    delete — that is the one thing streaming cannot take back."""
    gate = EgressGate(_pool())
    sent = gate.feed("Your order ships Tuesday. Let me know if you need anything else.")
    assert "Let me know" not in sent
    sent += gate.close()
    assert "Let me know" not in sent
    assert "Your order ships Tuesday." in sent


def test_a_closer_that_turns_out_not_to_be_trailing_is_released():
    gate = EgressGate(_pool())
    out = gate.feed("Feel free to ask. ")
    out += gate.feed("Your order ships Tuesday.")
    out += gate.close()
    assert "Feel free to ask." in out
    assert out == vet_egress("Feel free to ask. Your order ships Tuesday.", _pool())


def test_nothing_leaves_while_every_sentence_so_far_is_a_leak():
    """The 'don't blank a reply that is only a leak' fallback can still fire,
    so the answer is not settled and no byte may go out."""
    gate = EgressGate(_pool())
    assert gate.feed("I am an AI language model. ") == ""
    out = gate.feed("Your order ships Tuesday.") + gate.close()
    assert "language model" not in out
    assert "Your order ships Tuesday." in out


def test_a_reply_that_is_only_a_leak_still_arrives():
    """Silence is worse than a bad reply — the non-streaming rule, held under
    streaming too."""
    gate = EgressGate(_pool())
    out = gate.feed("I am an AI.") + gate.close()
    assert out == vet_egress("I am an AI.", _pool()) == "I am an AI."


def test_feed_after_close_is_a_programming_error():
    gate = EgressGate(_pool())
    gate.feed("hi")
    gate.close()
    with pytest.raises(RuntimeError):
        gate.feed("more")


def test_close_is_idempotent():
    """A complete sentence is released by feed(), so close() has nothing left —
    and a second close() must not re-send what already went out."""
    gate = EgressGate(_pool())
    sent = gate.feed("Your order ships Tuesday.")
    assert sent == "Your order ships Tuesday."
    assert gate.close() == ""
    assert gate.close() == ""
    assert gate.emitted == "Your order ships Tuesday."


def test_close_flushes_an_unterminated_tail():
    """Text with no sentence terminator is never settled, so it can only leave
    at close(). Without this the last line of a reply would be lost."""
    gate = EgressGate(_pool())
    assert gate.feed("Your order ships Tuesday") == ""
    assert gate.close() == "Your order ships Tuesday"


def test_strict_buffering_emits_everything_at_close():
    text = "Your order ships Tuesday. Feel free to ask."
    gate = EgressGate(_pool(), strict_buffering=True)
    assert gate.feed(text) == ""
    assert gate.close() == vet_egress(text, _pool())


def test_deleting_a_rule_changes_the_streamed_result_too():
    """Governance follows the parameter on both transports, not just one."""
    text = "I am an AI. Your order ships Tuesday."
    without = [
        p for p in reply_core_parameters() if p.get("key") != "identity.self_disclosure"
    ]
    gate = EgressGate(without)
    out = "".join(gate.feed(c) for c in text) + gate.close()
    assert "I am an AI." in out
    assert out == vet_egress(text, without)
