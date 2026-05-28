"""Regression tests for prompt brace safety (Wave-4 H4 verification).

Background — the May 2026 external review flagged ``str.format()`` calls
on user utterance and history strings in
:mod:`jvagent.action.helm.reasoning.routing.router` and
:mod:`jvagent.action.helm.reflex.reflex_helm` as a potential injection
risk: a user could type ``{skills_json}`` or ``Run {nonexistent_field}!``
and crash the prompt-build path with a ``KeyError``.

Empirical verification (Wave 4, May 2026) found this claim **incorrect**:
Python's ``str.format()`` does NOT recurse into substituted values. If
the template is ``"User said: {utterance}"`` and we call
``template.format(utterance="Hi {time}")``, the result is
``"User said: Hi {time}"`` literally — the inner ``{time}`` is not
re-parsed as a placeholder. The route from user input to LLM is
safe-by-construction.

These tests pin the current safety so a future refactor — e.g. swapping
to chained ``.format()`` calls, ``str.Template`` with naive substitution,
or operator-supplied prompt templates that embed user text via a second
formatter — gets caught immediately. If any of these tests start
failing, the prompt-build path has acquired a re-substitution step that
DOES create the injection risk the reviewer was concerned about, and
the fix is to escape braces before substitution (``utt.replace("{", "{{").replace("}", "}}")``)
or to switch the entry point to a non-substituting templater.
"""

from __future__ import annotations

import pytest

from jvagent.action.helm.reasoning.routing.prompts import (
    build_routing_user_prompt_template,
)
from jvagent.action.helm.reflex.prompts import REFLEX_USER_PROMPT_TEMPLATE

# Adversarial utterances — every shape someone could plausibly type into
# a chat client that contains braces. None should crash; each should
# land in the rendered prompt literally.
_ADVERSARIAL_UTTERANCES = [
    # Exact placeholder names from the templates themselves.
    "{skills_json}",
    "{utterance}",
    "{history_section}",
    "{interact_actions_json}",
    # Benign-looking braces (templating in other contexts).
    "What does {time} mean in your codebase?",
    "Run {x} for me please",
    # Doubled braces — Python's escape syntax inside .format().
    "Use this snippet: {{name}}",
    # Positional placeholders.
    "{} {} {}",
    "{0}",
    "{0}{1}",
    # Unbalanced braces (often appear in code-pasted text).
    "Some text { not closed",
    "Some text } unmatched",
    "Open { then close } literally",
    # JSON-shaped (common when users paste responses or configs).
    '{"key": "value"}',
    '[{"a": 1}, {"b": 2}]',
    # Format-spec syntax (looks like .format() mini-language).
    "Number: {value:0.2f}",
    "Field: {!r}",
    # Empty / whitespace edge cases (paired with braces).
    "{}",
    "{ }",
    "{   }",
    # Long string with many braces.
    "a" * 100 + "{" * 50 + "}" * 50 + "b" * 100,
]


class TestReflexPromptBraceSafety:
    """``REFLEX_USER_PROMPT_TEMPLATE.format(...)`` must accept any user utterance."""

    @pytest.mark.parametrize("utterance", _ADVERSARIAL_UTTERANCES)
    def test_does_not_raise_on_braces(self, utterance: str) -> None:
        """Formatting must not raise — KeyError / IndexError / ValueError all forbidden."""
        # Must not raise.
        rendered = REFLEX_USER_PROMPT_TEMPLATE.format(
            history_section="(no prior turns)",
            utterance=utterance,
        )
        # Sanity — the rendered prompt is a string with content.
        assert isinstance(rendered, str)
        assert len(rendered) > 0

    @pytest.mark.parametrize("utterance", _ADVERSARIAL_UTTERANCES)
    def test_utterance_appears_literally(self, utterance: str) -> None:
        """The user's exact text — braces and all — must appear in the output.

        This is the load-bearing safety guarantee: the LLM sees the
        user's literal words, not a transformed version with placeholders
        substituted in or stripped out.

        Doubled-brace cases (``{{name}}``) are notable — Python's
        ``.format()`` escape semantics apply only to the TEMPLATE, not
        to substituted values. So ``"{x}".format(x="{{name}}")`` yields
        ``"{{name}}"`` literally — the doubled braces are NOT normalised.
        This is the strongest possible inertness guarantee and is the
        property the safety argument relies on.
        """
        rendered = REFLEX_USER_PROMPT_TEMPLATE.format(
            history_section="(no prior turns)",
            utterance=utterance,
        )
        assert utterance in rendered, (
            f"User utterance must appear literally in the rendered "
            f"prompt — including any braces. Missing {utterance!r}"
        )


class TestRouterPromptBraceSafety:
    """``routing_user_template.format(...)`` must accept any user utterance.

    The router template is more complex (eight named substitutions) so
    we exercise it via the public factory rather than a constant.
    """

    @pytest.fixture(scope="class")
    def bridge_template(self) -> str:
        """The Bridge-mode router template (no posture recap clause)."""
        return build_routing_user_prompt_template(include_posture_recap=False)

    @pytest.fixture(scope="class")
    def cockpit_template(self) -> str:
        """The Cockpit-mode router template (with posture recap clause)."""
        return build_routing_user_prompt_template(include_posture_recap=True)

    @pytest.mark.parametrize("utterance", _ADVERSARIAL_UTTERANCES)
    def test_bridge_router_does_not_raise(
        self, bridge_template: str, utterance: str
    ) -> None:
        """Bridge-mode router template accepts arbitrary user input."""
        rendered = bridge_template.format(
            utterance=utterance,
            skills_json="{}",
            interact_actions_json="{}",
            active_tasks_section="",
            history_section="",
            prior_fragments_section="",
            entity_field="",
            canned_field="",
            optional_instructions="",
        )
        assert isinstance(rendered, str)
        assert len(rendered) > 0

    @pytest.mark.parametrize("utterance", _ADVERSARIAL_UTTERANCES)
    def test_cockpit_router_does_not_raise(
        self, cockpit_template: str, utterance: str
    ) -> None:
        """Cockpit-mode router template accepts arbitrary user input.

        The Cockpit variant has an extra ``posture_recap`` clause; the
        prompt-build path otherwise mirrors Bridge.
        """
        rendered = cockpit_template.format(
            utterance=utterance,
            skills_json="{}",
            interact_actions_json="{}",
            active_tasks_section="",
            history_section="",
            prior_fragments_section="",
            entity_field="",
            canned_field="",
            optional_instructions="",
        )
        assert isinstance(rendered, str)
        assert len(rendered) > 0

    @pytest.mark.parametrize(
        "history_text",
        [
            "USER: hello\nASSISTANT: hi",
            "USER: {tool_call}\nASSISTANT: response",
            "USER: malformed { history\nASSISTANT: ok",
        ],
    )
    def test_history_section_braces_are_safe(
        self, bridge_template: str, history_text: str
    ) -> None:
        """Braces in the history_section value (assistant or user turns) don't crash either."""
        rendered = bridge_template.format(
            utterance="hello",
            skills_json="{}",
            interact_actions_json="{}",
            active_tasks_section="",
            history_section=history_text,
            prior_fragments_section="",
            entity_field="",
            canned_field="",
            optional_instructions="",
        )
        assert isinstance(rendered, str)


class TestPythonStrFormatNoRecursion:
    """Pin the language guarantee the safety of the prompt path relies on.

    Substituted values are NOT re-parsed as templates. If a future
    Python release ever changes this — or if a refactor introduces a
    second ``.format()`` pass over the result — these tests are the
    canary.
    """

    def test_substituted_value_with_placeholder_not_recursed(self) -> None:
        """``"{x}".format(x="{y}")`` returns ``"{y}"`` literally, not a KeyError."""
        result = "{x}".format(x="{y}")
        assert result == "{y}"

    def test_substituted_value_with_unknown_placeholder_not_recursed(self) -> None:
        """``"{x}".format(x="{nonexistent}")`` returns ``"{nonexistent}"`` literally."""
        result = "{x}".format(x="{nonexistent}")
        assert result == "{nonexistent}"

    def test_substituted_value_with_format_spec_not_recursed(self) -> None:
        """A format-spec-shaped value (``{val:0.2f}``) is inert in the result."""
        result = "{x}".format(x="{val:0.2f}")
        assert result == "{val:0.2f}"
