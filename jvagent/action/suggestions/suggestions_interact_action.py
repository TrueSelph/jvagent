"""SuggestionsInteractAction — LLM-generated quick-reply chips.

After the reply is produced, this action asks a (typically light) language model
for a few short follow-up quick replies phrased from the user's perspective and
publishes them as ``metadata.suggestions`` on an empty ``category:"user"``
message. The embeddable messenger renders those as agent-driven quick-reply
chips (``extractSuggestions`` in jvmessenger); other channels ignore the empty
message.

Reusable and provider-agnostic: it resolves a model via ``model_action_type``
(or any available LM when unset) and degrades to a no-op when no model is
available or the model output can't be parsed — it never breaks the turn.

Placement: runs after the Orchestrator (``weight`` 100 by default) so the reply
exists, and only on **streaming** turns (the web/messenger path) so it never
emits an empty message to non-streaming channel adapters. It is foreground
(``run_in_background=False``) so the chips ride the same response stream — this
adds one light LLM call to the turn's latency; use a small/fast model.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, List, Union

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM = (
    "You generate short quick-reply buttons for a chat UI. Given the latest "
    "exchange, propose the most likely next things the USER would say. Write "
    "each as a brief first-person statement or question in the user's own voice "
    '— something they could send verbatim (e.g. "How much does it cost?", '
    '"I\'d like a demo", "Do you integrate with Salesforce?"). '
    "Do NOT write UI labels or commands ('Pricing', 'Contact us').\n"
    "A tapped chip sends its label EXACTLY as written, so every suggestion must "
    "be true and complete for any user without editing. Therefore:\n"
    "- Never suggest a reply carrying information only the user can supply — "
    "their name, company, email, phone, address, budget, dates, order numbers.\n"
    "- Never invent a specific value or use a placeholder. "
    '"Company name is XYZ", "My budget is $500", "I need it by [date]" are all '
    "wrong: the user would have to edit them, so they are useless as buttons.\n"
    '- Prefer intent over data: "It\'s for my company" or "I\'d rather not say" '
    'work as buttons; "My company is Acme" does not.\n'
    "QUALITY OVER QUANTITY: return at most {count} suggestions, and return FEWER "
    "— or an empty array [] — when the rest would be filler, redundant, or "
    "guesswork. An empty array is a perfectly good answer, especially when the "
    "assistant just asked for information only the user can give.\n"
    "Output ONLY a JSON array of strings, each at most {max_words} words, no "
    "numbering, no trailing period. Do not repeat what was just said."
)

# Suggestions asking the user to hand over personal/contact/account data cannot
# work as tap-to-send chips (the label is sent verbatim, not the data), so they
# are dropped even if the model produces them. Matches "my <data>" and
# "<provide-verb> ... <data>" phrasings.
_DATA_NOUNS = (
    r"(e-?mails?|phones?|numbers?|names?|addres(?:s|ses)|contacts?|"
    r"details?|info(?:rmation)?|cards?|payments?|zip|postal)"
)
_PROVIDE_VERBS = (
    r"(share|provide|give|send|enter|submit|leave|fill|type|input|register)"
)
_RE_MY_DATA = re.compile(rf"\bmy\b[\w'\s]*\b{_DATA_NOUNS}\b", re.I)
_RE_PROVIDE_DATA = re.compile(rf"\b{_PROVIDE_VERBS}\b[\w'\s]*\b{_DATA_NOUNS}\b", re.I)


# A chip whose label contains a placeholder or an invented specific is useless:
# tapping it sends the placeholder verbatim ("Company name is XYZ"). These catch
# the shapes models actually produce.
_RE_BRACKET_PLACEHOLDER = re.compile(r"[\[\{<][^\]\}>]{1,40}[\]\}>]")
_RE_BLANK_PLACEHOLDER = re.compile(r"_{2,}|\.{3,}\s*$")
# Stand-in tokens (XYZ / ABC123 / "your company" / "e.g. ..."). No trailing \b on
# the stand-ins so "ABC123" and "XYZ-1" are caught too.
_RE_TOKEN_PLACEHOLDER = re.compile(
    r"\b(xyz+|abc+|acme|foo|bar|lorem|tbd|n/?a|example\.com)"
    r"|\be\.?g\.?\b"
    r"|\byour\s+(company|business|name|email|budget|date)\b",
    re.I,
)
# "<subject> is <value>" / "I need it by <value>" — declaring a specific the
# model cannot know. Value-bearing prepositional declarations.
_DECLARED_SUBJECTS = (
    r"(company|business|budget|name|team\s*size|address|order|phone|email|"
    r"deadline|date|timeline)"
)
# One optional intervening noun so "Order number is …" matches as well as
# "Order is …".
_RE_DECLARES_VALUE = re.compile(
    rf"\b{_DECLARED_SUBJECTS}\b(\s+\w+)?\s+(is|are|:)\s+\S", re.I
)
# A bare currency/number specific the model invented, e.g. "My budget is $500".
_RE_INVENTED_AMOUNT = re.compile(r"[$£€]\s?\d")


def is_data_request(text: str) -> bool:
    """True if a suggestion asks the user to supply personal/contact data.

    Such a chip is broken (a tap sends the label, not the actual data), so it is
    filtered out — e.g. "Share my email", "Provide my phone number".
    """
    s = text or ""
    return bool(_RE_MY_DATA.search(s) or _RE_PROVIDE_DATA.search(s))


def needs_user_specifics(text: str) -> bool:
    """True if a suggestion embeds a placeholder or a value only the user knows.

    A tapped chip sends its label verbatim, so "Company name is XYZ" would send
    the literal placeholder and "My budget is $500" would assert a number the
    user never gave. Both are useless as buttons and are dropped.
    """
    s = (text or "").strip()
    if not s:
        return False
    return bool(
        _RE_BRACKET_PLACEHOLDER.search(s)
        or _RE_BLANK_PLACEHOLDER.search(s)
        or _RE_TOKEN_PLACEHOLDER.search(s)
        or _RE_DECLARES_VALUE.search(s)
        or _RE_INVENTED_AMOUNT.search(s)
    )


def parse_suggestions(text: str, count: int, max_words: int) -> List[str]:
    """Parse an LLM reply into a clean list of short quick-reply strings.

    Accepts a JSON array (preferred) or falls back to line-splitting, then
    strips bullets/quotes, caps each item to ``max_words`` words, drops empties
    and duplicates, and returns at most ``count`` items.
    """
    raw = (text or "").strip()
    items: Any = None
    match = re.search(r"\[.*\]", raw, re.S)
    if match:
        try:
            items = json.loads(match.group(0))
        except (ValueError, TypeError):
            items = None
    if not isinstance(items, list):
        items = [
            re.sub(r"^[\s\-\*•\d\.\)]+", "", line).strip() for line in raw.splitlines()
        ]

    out: List[str] = []
    seen = set()
    for item in items:
        if not isinstance(item, str):
            continue
        s = item.strip().strip('"').strip("'").strip()
        if not s:
            continue
        # Drop over-length items rather than truncating them mid-phrase (a
        # chopped "How much does it cost per…" is worse than no chip).
        if len(s.split()) > max_words:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= count:
            break
    return out


class SuggestionsInteractAction(InteractAction):
    """Emit LLM-generated quick-reply chips (metadata.suggestions)."""

    description: str = attribute(
        default="Generates dynamic quick-reply chips for the messenger via an LLM.",
        description="Action description",
    )

    weight: int = attribute(
        default=100,
        description="Runs after the Orchestrator (weight -200) so the reply exists.",
    )

    always_execute: bool = attribute(
        default=True,
        description="Always attempt suggestions regardless of routing.",
    )

    model_action_type: str = attribute(
        default="",
        description=(
            "LM provider class to use (e.g. OpenAILanguageModelAction). Empty "
            "resolves any available language model."
        ),
    )

    model: str = attribute(
        default="",
        description="Optional model id override passed to the LM (e.g. gpt-4o-mini).",
    )

    num_suggestions: int = attribute(
        default=3, description="Number of quick replies to emit."
    )

    max_words: int = attribute(
        default=8,
        description=(
            "Maximum words per quick reply; longer suggestions are dropped (not "
            "truncated). Allow room for a short first-person question/statement "
            "(e.g. 'How much does it cost per month?')."
        ),
    )

    avoid_data_requests: bool = attribute(
        default=True,
        description=(
            "Drop suggestions that ask the user to provide personal/contact data "
            "(e.g. 'Share my email') — a tapped chip can't carry that data."
        ),
    )

    temperature: float = attribute(
        default=0.4, description="Sampling temperature for the suggestion model."
    )

    max_tokens: int = attribute(
        default=120, description="Token cap for the suggestion completion."
    )

    system_prompt: str = attribute(
        default=_DEFAULT_SYSTEM,
        description=(
            "System prompt for the suggestion model. Supports {count} and "
            "{max_words} placeholders."
        ),
    )

    async def execute(self, visitor: "InteractWalker") -> None:
        """Generate quick replies for this turn and publish them as metadata."""
        interaction = visitor.interaction
        if not interaction:
            await visitor.unrecord_action_execution()
            return
        # Only emit on streaming turns (web/messenger) — an empty message would
        # otherwise be handed to non-streaming channel adapters.
        if not getattr(visitor, "stream", False):
            await visitor.unrecord_action_execution()
            return

        try:
            utterance = (getattr(interaction, "utterance", "") or "").strip()
            reply = (getattr(interaction, "response", "") or "").strip()
            if not utterance and not reply:
                await visitor.unrecord_action_execution()
                return

            lm = await self.get_model_action(required=False)
            if lm is None:
                logger.debug("SuggestionsInteractAction: no language model available")
                await visitor.unrecord_action_execution()
                return

            system = self.system_prompt.format(
                count=self.num_suggestions, max_words=self.max_words
            )
            prompt = (
                "Latest exchange:\n"
                f"User: {utterance}\n"
                f"Assistant: {reply}\n\n"
                f"Return AT MOST {self.num_suggestions} quick replies as a JSON "
                "array of strings — fewer, or [], if the rest would be filler or "
                "would require information only the user can supply."
            )
            gen_kwargs: dict = {
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            }
            if self.model:
                gen_kwargs["model"] = self.model

            text = await lm.generate(
                prompt,
                system=system,
                calling_action_name=getattr(self, "name", None) or "suggestions",
                **gen_kwargs,
            )
            # Over-fetch candidates so filtering doesn't starve the count, then
            # drop unusable ones and cap. Emitting nothing is a valid outcome —
            # padding with filler is worse than no chips at all.
            candidates = parse_suggestions(
                text, self.num_suggestions + 3, self.max_words
            )
            if self.avoid_data_requests:
                candidates = [
                    s
                    for s in candidates
                    if not is_data_request(s) and not needs_user_specifics(s)
                ]
            suggestions = candidates[: self.num_suggestions]
            if not suggestions:
                logger.debug("SuggestionsInteractAction: no usable suggestions")
                await visitor.unrecord_action_execution()
                return

            await self.publish(
                visitor,
                content="",
                allow_empty=True,
                category="user",
                metadata={"suggestions": suggestions},
                stream=False,
            )
        except Exception as e:  # never break the turn over suggestions
            logger.error("SuggestionsInteractAction: %s", e, exc_info=True)
            await visitor.unrecord_action_execution()

    async def healthcheck(self) -> Union[bool, dict]:
        return True
