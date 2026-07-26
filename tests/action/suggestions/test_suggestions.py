"""Tests for the reusable quick-reply SuggestionsInteractAction.

Covers the LLM-output parser (the fragile bit) and the execute path: it emits
``metadata.suggestions`` on a streaming turn and no-ops otherwise.
"""

import pytest

from jvagent.action.suggestions.suggestions_interact_action import (
    SuggestionsInteractAction,
    is_data_request,
    needs_user_specifics,
    parse_suggestions,
)

# ── parse_suggestions ──────────────────────────────────────────────────────


def test_parses_json_array():
    out = parse_suggestions('["See pricing", "Book a demo", "Talk to sales"]', 3, 4)
    assert out == ["See pricing", "Book a demo", "Talk to sales"]


def test_extracts_array_embedded_in_prose():
    text = 'Sure! Here you go: ["A option", "B option"] — hope that helps'
    assert parse_suggestions(text, 3, 4) == ["A option", "B option"]


def test_drops_over_length_items_without_truncating():
    out = parse_suggestions(
        '["short one", "this reply has too many words to keep"]', 3, 4
    )
    assert out == ["short one"]


def test_caps_number_of_items():
    out = parse_suggestions('["a", "b", "c", "d", "e"]', 2, 4)
    assert out == ["a", "b"]


def test_dedupes_case_insensitively():
    out = parse_suggestions('["Pricing", "pricing", "Demo"]', 5, 4)
    assert out == ["Pricing", "Demo"]


def test_line_fallback_strips_bullets_and_numbers():
    text = "1. See pricing\n- Book a demo\n* Talk to a human"
    assert parse_suggestions(text, 3, 4) == [
        "See pricing",
        "Book a demo",
        "Talk to a human",
    ]


def test_empty_input_yields_empty():
    assert parse_suggestions("", 3, 4) == []


# ── is_data_request ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "Share my email",
        "Provide my phone number",
        "I'd like to give my email",
        "Enter my contact details",
        "Send my address",
        "Submit my payment card",
    ],
)
def test_data_requests_are_flagged(text):
    assert is_data_request(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "See pricing",
        "How much does it cost?",
        "Do you integrate with Salesforce?",
        "Email support",
        "Tell me about products",
        "Book a demo",
        "Talk to a human",
    ],
)
def test_valid_suggestions_pass(text):
    assert is_data_request(text) is False


# ── needs_user_specifics ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "Company name is XYZ",  # the reported case
        "My budget is $500",
        "I need it by [date]",
        "My company is <name>",
        "Order number is ABC123",
        "Team size is 25",
        "Contact me at your email",
        "My name is ___",
        "e.g. Acme Corp",
    ],
)
def test_placeholder_and_invented_specifics_are_flagged(text):
    assert needs_user_specifics(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "It's a personal inquiry",
        "Suggest some options",
        "How much does it cost?",
        "I'd like a demo",
        "Do you integrate with Salesforce?",
        "I'd rather not say",
        "Tell me about products",
    ],
)
def test_genuine_suggestions_pass(text):
    assert needs_user_specifics(text) is False


# ── execute ────────────────────────────────────────────────────────────────


class _FakeLM:
    def __init__(self, text):
        self._text = text
        self.calls = []

    async def generate(self, prompt, system=None, calling_action_name=None, **kw):
        self.calls.append({"prompt": prompt, "system": system, **kw})
        return self._text


class _Interaction:
    utterance = "what does pricing look like?"
    response = "We offer three plans starting at $49/mo."


class _Visitor:
    def __init__(self, stream=True):
        self.stream = stream
        self.interaction = _Interaction()

    async def unrecord_action_execution(self):
        return None


def _make_action(**over):
    action = SuggestionsInteractAction.model_construct(
        num_suggestions=over.get("num_suggestions", 3),
        max_words=over.get("max_words", 4),
        temperature=0.4,
        max_tokens=120,
        model=over.get("model", ""),
        model_action_type="",
        system_prompt="Give {count} of at most {max_words} words.",
    )
    object.__setattr__(action, "name", "jvagent/suggestions")
    return action


@pytest.mark.asyncio
async def test_execute_publishes_suggestions_on_streaming_turn(monkeypatch):
    action = _make_action()
    lm = _FakeLM('["See pricing", "Book a demo", "Talk to sales"]')
    published = {}

    async def fake_get_model_action(self, required=False):
        return lm

    async def fake_publish(self, visitor, content, **kw):
        published["content"] = content
        published["metadata"] = kw.get("metadata")
        published["category"] = kw.get("category")
        return object()

    monkeypatch.setattr(
        SuggestionsInteractAction, "get_model_action", fake_get_model_action
    )
    monkeypatch.setattr(SuggestionsInteractAction, "publish", fake_publish)

    await action.execute(_Visitor(stream=True))

    assert published["category"] == "user"
    assert published["content"] == ""
    assert published["metadata"]["suggestions"] == [
        "See pricing",
        "Book a demo",
        "Talk to sales",
    ]
    # System prompt placeholders were filled.
    assert lm.calls[0]["system"].startswith("Give 3 of at most 4 words.")


@pytest.mark.asyncio
async def test_execute_filters_data_request_suggestions(monkeypatch):
    action = _make_action()
    lm = _FakeLM('["See pricing", "Share my email", "Book a demo", "Provide my phone"]')
    published = {}

    async def fake_get_model_action(self, required=False):
        return lm

    async def fake_publish(self, visitor, content, **kw):
        published["metadata"] = kw.get("metadata")
        return object()

    monkeypatch.setattr(
        SuggestionsInteractAction, "get_model_action", fake_get_model_action
    )
    monkeypatch.setattr(SuggestionsInteractAction, "publish", fake_publish)

    await action.execute(_Visitor(stream=True))

    # The two data-request chips are dropped; the valid ones remain.
    assert published["metadata"]["suggestions"] == ["See pricing", "Book a demo"]


@pytest.mark.asyncio
async def test_execute_publishes_nothing_when_all_are_unusable(monkeypatch):
    """Emitting zero chips is valid — better than padding with filler."""
    action = _make_action()
    lm = _FakeLM('["Company name is XYZ", "Share my email", "My budget is $500"]')
    published = {"called": False}

    async def fake_get_model_action(self, required=False):
        return lm

    async def fake_publish(self, visitor, content, **kw):
        published["called"] = True

    monkeypatch.setattr(
        SuggestionsInteractAction, "get_model_action", fake_get_model_action
    )
    monkeypatch.setattr(SuggestionsInteractAction, "publish", fake_publish)

    await action.execute(_Visitor(stream=True))

    assert published["called"] is False


@pytest.mark.asyncio
async def test_execute_accepts_an_empty_model_response(monkeypatch):
    """An empty array from the model is a legitimate 'no suggestions' answer."""
    action = _make_action()
    lm = _FakeLM("[]")
    published = {"called": False}

    async def fake_get_model_action(self, required=False):
        return lm

    async def fake_publish(self, visitor, content, **kw):
        published["called"] = True

    monkeypatch.setattr(
        SuggestionsInteractAction, "get_model_action", fake_get_model_action
    )
    monkeypatch.setattr(SuggestionsInteractAction, "publish", fake_publish)

    await action.execute(_Visitor(stream=True))

    assert published["called"] is False


@pytest.mark.asyncio
async def test_execute_keeps_the_good_ones_and_drops_the_rest(monkeypatch):
    action = _make_action(max_words=8)
    lm = _FakeLM(
        '["Company name is XYZ", "How much does it cost?", "It\'s a personal inquiry"]'
    )
    published = {}

    async def fake_get_model_action(self, required=False):
        return lm

    async def fake_publish(self, visitor, content, **kw):
        published["metadata"] = kw.get("metadata")

    monkeypatch.setattr(
        SuggestionsInteractAction, "get_model_action", fake_get_model_action
    )
    monkeypatch.setattr(SuggestionsInteractAction, "publish", fake_publish)

    await action.execute(_Visitor(stream=True))

    assert published["metadata"]["suggestions"] == [
        "How much does it cost?",
        "It's a personal inquiry",
    ]


@pytest.mark.asyncio
async def test_execute_skips_on_non_streaming_turn(monkeypatch):
    action = _make_action()
    called = {"publish": False, "lm": False}

    async def fake_get_model_action(self, required=False):
        called["lm"] = True
        return _FakeLM("[]")

    async def fake_publish(self, visitor, content, **kw):
        called["publish"] = True

    monkeypatch.setattr(
        SuggestionsInteractAction, "get_model_action", fake_get_model_action
    )
    monkeypatch.setattr(SuggestionsInteractAction, "publish", fake_publish)

    await action.execute(_Visitor(stream=False))

    assert called == {"publish": False, "lm": False}


@pytest.mark.asyncio
async def test_execute_noop_without_model(monkeypatch):
    action = _make_action()
    published = {"called": False}

    async def fake_get_model_action(self, required=False):
        return None

    async def fake_publish(self, visitor, content, **kw):
        published["called"] = True

    monkeypatch.setattr(
        SuggestionsInteractAction, "get_model_action", fake_get_model_action
    )
    monkeypatch.setattr(SuggestionsInteractAction, "publish", fake_publish)

    await action.execute(_Visitor(stream=True))

    assert published["called"] is False


# ── governance (ADR-0037 §2.3) ─────────────────────────────────────────────
#
# Chips are user-facing text the agent puts in the user's mouth, but they ride
# in `metadata` rather than in `content`, so publish()'s scrub never sees them.
# They were the last model-generated output reaching a user with no rule
# applied. Both halves are covered here: the rules go INTO the prompt, and a
# deterministic scrub catches what the prompt does not.


@pytest.mark.asyncio
async def test_response_rules_are_rendered_into_the_suggestion_prompt(monkeypatch):
    action = _make_action()
    lm = _FakeLM('["See pricing"]')

    async def fake_get_model_action(self, required=False):
        return lm

    async def fake_publish(self, visitor, content, **kw):
        return object()

    monkeypatch.setattr(
        SuggestionsInteractAction, "get_model_action", fake_get_model_action
    )
    monkeypatch.setattr(SuggestionsInteractAction, "publish", fake_publish)

    await action.execute(_Visitor(stream=True))

    system = lm.calls[0]["system"]
    assert "govern the suggestions you write" in system
    # the identity rule in particular — a chip is a thing the agent offers to say
    assert "language model" in system


@pytest.mark.asyncio
async def test_a_rule_breaking_chip_is_scrubbed_before_it_reaches_the_user(
    monkeypatch,
):
    """The prompt is probabilistic; this is the deterministic backstop."""
    action = _make_action(num_suggestions=2)
    lm = _FakeLM('["I am an AI", "See pricing", "Book a demo"]')
    published = {}

    async def fake_get_model_action(self, required=False):
        return lm

    async def fake_publish(self, visitor, content, **kw):
        published["metadata"] = kw.get("metadata")
        return object()

    monkeypatch.setattr(
        SuggestionsInteractAction, "get_model_action", fake_get_model_action
    )
    monkeypatch.setattr(SuggestionsInteractAction, "publish", fake_publish)

    await action.execute(_Visitor(stream=True))

    chips = published["metadata"]["suggestions"]
    assert "I am an AI" not in chips
    # scrubbing happens BEFORE the cap, so the row is still full
    assert chips == ["See pricing", "Book a demo"]


@pytest.mark.asyncio
async def test_ordinary_chips_survive_the_scrub(monkeypatch):
    """Two-direction check: the scrub must leave legitimate chips alone."""
    action = _make_action()
    lm = _FakeLM('["See pricing", "Book a demo", "Talk to sales"]')
    published = {}

    async def fake_get_model_action(self, required=False):
        return lm

    async def fake_publish(self, visitor, content, **kw):
        published["metadata"] = kw.get("metadata")
        return object()

    monkeypatch.setattr(
        SuggestionsInteractAction, "get_model_action", fake_get_model_action
    )
    monkeypatch.setattr(SuggestionsInteractAction, "publish", fake_publish)

    await action.execute(_Visitor(stream=True))

    assert published["metadata"]["suggestions"] == [
        "See pricing",
        "Book a demo",
        "Talk to sales",
    ]
