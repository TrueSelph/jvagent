"""``ReflexHelm`` — fast classifier helm (BRIDGE-ROADMAP §E, ADR-0007 v0).

Orchestrated by :class:`BridgeInteractAction`. Each ``step()`` issues at
most one fast-model call (default OpenAI ``gpt-4o-mini``) and emits a
structured JSON verb that Bridge translates into a
:class:`HelmStepResult`.

Latency target: sub-500ms p50 on trivial turns.

Design choices vs ReasoningHelm:

- **Single LM call per visit**: no router phase, no engine think/act loop.
- **Structured JSON output**: faster than function-calling tool surfaces.
- **No tools**: Reflex cannot use harness / skill / action tools. It can
  only EMIT a short reply or SHIFT/DELEGATE the turn. If it can't
  classify, it SHIFTs to the safe default.
- **Peer awareness via manifests**: the system prompt is built per-call
  from every other :class:`BaseHelm` instance on the agent, reading each
  helm's ``get_manifest()`` (D). Same for rails ``InteractAction``s.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.helm.base import BaseHelm
from jvagent.action.helm.contracts import (
    DELEGATE,
    EMIT,
    SHIFT,
    YIELD,
    HelmStepResult,
)
from jvagent.action.helm.reflex.prompts import (
    ANCHOR_DISAMBIGUATION_CLAUSE,
    REFLEX_SYSTEM_PROMPT,
    REFLEX_USER_PROMPT_TEMPLATE,
    render_helms_available_block,
    render_peer_action_block,
    render_peer_helm_line,
)
from jvagent.action.manifest import Manifest

if TYPE_CHECKING:
    from jvagent.action.bridge.state import BridgeState
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


DEFAULT_REFLEX_MODEL = "gpt-4o-mini"
DEFAULT_REFLEX_MODEL_ACTION = "OpenAILanguageModelAction"


# ---------------------------------------------------------------------------
# Tool-invocation pattern matchers (Layer 1 of the security defense)
# ---------------------------------------------------------------------------
#
# Canonical syntax that says "the user is naming a tool and asking it to
# run directly". When the utterance matches any of these (and
# ``block_raw_tool_invocation`` is True), Reflex returns an EMIT refusal
# and the utterance never reaches Reasoning's engine.
#
# Each entry maps a label (for logging / observability) to a compiled
# regex. The labels are stable identifiers; the regexes are tuned so
# legitimate phrasings ("look up X", "search for Y", "could you find Z")
# never match — only explicit tool-call syntax does.
#
# All patterns are anchored against the utterance with case-insensitive
# matching and ``\b`` word boundaries to avoid matching ``recall`` /
# ``callback`` / etc. as ``call``.
_TOOL_INVOCATION_PATTERNS: Dict[str, "re.Pattern"] = {
    # Dispatch-verb + snake_case identifier. Catches the canonical
    # injection "Call capability_search with query='...'" / "execute
    # response_publish ..." / "run web_search" / "run: get_secrets".
    # The separator between verb and identifier accepts whitespace OR
    # colon ("run:") because operators routinely paste commands in
    # ``verb: target`` shape (cron-like, makefile-like). Snake_case is
    # the near-universal naming convention for engine tools, so
    # requiring an underscore in the identifier filters out legitimate
    # phrasings ("call you", "execute the order", "run a search").
    # Verbs covered: call / invoke / execute / run / dispatch / trigger.
    "dispatch_verb_snake_case_tool": re.compile(
        r"\b(?:call|invoke|execute|run|dispatch|trigger)[\s:]+\w*[a-z]\w*_\w+",
        re.IGNORECASE,
    ),
    # Dispatch-verb + identifier + paren — function-call syntax even
    # without an underscore. Catches "execute foo(" or "run search(" —
    # the parens are the tell. Separator accepts whitespace or colon
    # for symmetry with the snake_case variant above.
    "dispatch_verb_with_parens": re.compile(
        r"\b(?:call|invoke|execute|run|dispatch|trigger)[\s:]+\w+\s*\(",
        re.IGNORECASE,
    ),
    # Slash commands. "/skill X" / "/tool Y" / "/exec Z" — unambiguously
    # command-style; no legitimate interpretation in a chat agent.
    # Tightly anchored to start-of-string (optional leading whitespace
    # only) and explicitly terminated so multi-segment Unix paths cannot
    # match. Pattern requires:
    #   - leading whitespace (optional) then ``/``
    #   - an alpha character (slash commands start with letters; ``/123``
    #     is a path or pagination marker, not a command)
    #   - zero or more word chars
    #   - followed by end-of-string, whitespace, or a punctuation char
    #     that is NEITHER a word char NOR a forward slash
    # This means ``/admin``, ``/admin foo``, and ``/exec; ls`` all match,
    # while ``/etc/passwd``, ``/usr/local``, ``/123/456`` do not.
    "slash_command": re.compile(
        r"^\s*/[a-zA-Z]\w*(?=\s|$|[^\w/])",
        re.IGNORECASE,
    ),
    # Bare ``tool_name(args)`` style invocation embedded in the message.
    # Requires snake_case (an underscore in the identifier) to avoid
    # matching natural-language uses of parenthesized words ("(yes)" /
    # "(more on this below)"). Matches ``capability_search(...)`` /
    # ``response_publish(...)`` even when the user didn't say "call".
    "bare_snake_case_function_call": re.compile(
        r"\b\w*[a-z]\w*_\w+\s*\(",
        re.IGNORECASE,
    ),
}


def _detect_tool_invocation_pattern(utterance: str) -> Optional[str]:
    """Return the label of the first matching tool-invocation pattern, or None.

    Pure function — no side effects. Used by :class:`ReflexHelm` to
    short-circuit classification when an utterance carries explicit
    tool-call syntax.
    """
    text = (utterance or "").strip()
    if not text:
        return None
    for label, pattern in _TOOL_INVOCATION_PATTERNS.items():
        if pattern.search(text):
            return label
    return None


class ReflexHelm(BaseHelm):
    """Sub-500ms fast-classifier helm orchestrated by ``BridgeInteractAction``.

    Configuration (override in ``agent.yaml.context:``):

    - ``model`` / ``model_action_type``: which LM action drives the
      classification. Defaults to ``OpenAILanguageModelAction`` /
      ``gpt-4o-mini`` for ubiquity + speed; swap to Groq / Cerebras /
      Anthropic Haiku for tighter p50.
    - ``model_temperature``: low (default 0.0) for deterministic
      classification.
    - ``model_max_tokens``: small (default 256) since output is a JSON verb.
    - ``timeout_seconds``: hard LM wall-clock cap (default 3.0).
    - ``history_limit``: turns of context included in the prompt (default 4).
    - ``default_shift_target``: helm name to SHIFT to when classification
      is unclear (default ``ReasoningHelm``).
    - ``fallback_text``: emitted when LM output is unparseable AND no
      shift target resolves (default short generic ack).
    - ``can_emit_directly``: if False, Reflex is a pure classifier and
      MUST SHIFT or DELEGATE every turn (no EMIT). Useful when paired
      with a separate Persona helm.
    """

    description: str = attribute(
        default=(
            "Fast classifier helm: sub-500ms first-response on trivial turns; "
            "SHIFTs to peer helms for substantive work."
        )
    )
    latency_class: str = attribute(default="instant")
    can_emit_directly: bool = attribute(default=True)

    model: str = attribute(default=DEFAULT_REFLEX_MODEL)
    model_action_type: str = attribute(default=DEFAULT_REFLEX_MODEL_ACTION)
    model_temperature: float = attribute(default=0.0)
    model_max_tokens: int = attribute(default=256)
    timeout_seconds: float = attribute(default=3.0)
    history_limit: int = attribute(default=4)

    default_shift_target: str = attribute(default="ReasoningHelm")
    # Last-resort text emitted ONLY when Reflex cannot classify AND cannot
    # SHIFT (the default target isn't an installed peer helm — usually a
    # configuration error). Phrased honestly so the user knows the turn
    # didn't land and a retry is welcome — don't promise action with
    # "one moment" because no helm is going to act. Operators localizing
    # per-language should override this in ``agent.yaml.context:``.
    fallback_text: str = attribute(
        default="Sorry — I couldn't process that. Could you rephrase or try again?"
    )

    # ------------------------------------------------------------------
    # Tool-invocation defense (Layer 1 of the two-layer security
    # against tool-name injection)
    # ------------------------------------------------------------------
    #
    # When ``block_raw_tool_invocation`` is True (the secure default),
    # Reflex regex-matches the user utterance for canonical tool-call
    # syntax BEFORE issuing the classification LM call. If a pattern
    # matches, Reflex returns an EMIT carrying ``tool_invocation_refusal_text``
    # — the utterance never reaches Reasoning's engine, so a determined
    # adversary's "Call X with Y" / "/skill Z" injection can't ride the
    # SHIFT path.
    #
    # The companion Layer 2 lives in :class:`Engine` (engine.py): a
    # pre-dispatch substring check that refuses tool calls when the
    # tool's name appears literally in the user's utterance. The two
    # layers together cover the precision/recall tradeoff — Layer 1 is
    # the cheap broad gate, Layer 2 is the deep narrow catch.
    #
    # Operators that intentionally want natural-language tool dispatch
    # (rare — developer agents) can set both flags to False.
    block_raw_tool_invocation: bool = attribute(default=True)
    tool_invocation_refusal_text: str = attribute(
        default=(
            "I don't execute tools by name. Tell me what you're trying "
            "to do and I'll figure out the right approach."
        ),
        description=(
            "Friendly refusal published when the utterance matches a "
            "canonical tool-invocation pattern AND block_raw_tool_invocation "
            "is True. STATIC string — override per agent in agent.yaml. "
            "Set to empty to suppress the publish entirely (the helm "
            "will still refuse to SHIFT)."
        ),
    )

    # ------------------------------------------------------------------
    # Step entry point
    # ------------------------------------------------------------------

    async def _step_impl(
        self,
        visitor: "InteractWalker",
        bridge_state: "BridgeState",
    ) -> HelmStepResult:
        """Run one classification + return a verb.

        Called by :meth:`BaseHelm.step` (the wrapper handles the
        action-trace self-recording via
        ``interaction.record_action_execution``).
        """
        utterance = (getattr(visitor, "utterance", None) or "").strip()
        if not utterance:
            # Empty utterance — yield silently (no point burning an LM call).
            logger.debug("ReflexHelm: empty utterance — yielding")
            return YIELD()

        # Layer 1 of the tool-injection defense — pattern gate.
        # Canonical "call X(...)", "/skill X", "execute X(...)", etc.
        # never reach Reasoning. The model-side SECURITY_BLOCK (Layer 2)
        # is a non-binding instruction the LM can ignore on determined
        # inputs; this regex check is a hard refuse.
        if self.block_raw_tool_invocation:
            matched = _detect_tool_invocation_pattern(utterance)
            if matched is not None:
                logger.info(
                    "ReflexHelm: blocked raw tool invocation "
                    "(pattern=%s) — utterance=%r",
                    matched,
                    utterance[:120],
                )
                refusal = (self.tool_invocation_refusal_text or "").strip()
                # Fall back to a minimalist refusal if the operator
                # explicitly emptied the friendly default.
                if not refusal:
                    refusal = "I can't do that."
                return EMIT(text=refusal, finalize=True)

        try:
            agent = await self.get_agent()
        except Exception as exc:
            logger.warning("ReflexHelm: get_agent failed: %s", exc)
            return self._safe_default_shift("agent unavailable")

        peer_helms = await self._collect_peer_helms(agent)
        conversation = getattr(visitor, "conversation", None)
        peer_actions = await self._collect_peer_actions(agent, conversation)

        system_prompt = self._build_system_prompt(peer_helms, peer_actions)
        history = await self._build_history(visitor)
        # Language detection is model-driven via the ``detected_language``
        # field in the JSON output schema. The model commits to a language
        # before generating user-facing content — a chain-of-thought
        # technique that's materially more reliable on small models than
        # a "match user's language" directive buried in prose. No lexicon
        # is consulted; cf. PROMPTS.md.
        user_prompt = REFLEX_USER_PROMPT_TEMPLATE.format(
            history_section=history or "(no prior turns)",
            utterance=utterance,
        )

        # Detect whether the immediately prior assistant turn was a
        # question. If so, the current utterance is a continuation —
        # even single-word affirmatives ("Yes", "No", "Sure") must SHIFT
        # to ReasoningHelm so the question's flow continues, never EMIT
        # back to the user. The substantive guard reads this signal so
        # the model's misclassification is overridden defensively.
        prior_was_question = await self._prior_assistant_ended_with_question(visitor)

        verb = await self._classify(system_prompt, user_prompt)
        if verb is None:
            return self._safe_default_shift("classification failed")

        # Stamp detected_language on BridgeState so language-adaptive
        # Bridge surfaces (safety_net_ack_text dict-by-language picks,
        # future locale-aware fallback text) can read it without
        # re-running classification. Wave 9h.
        detected = (verb.get("detected_language") or "").strip()
        if detected:
            logger.debug(
                "ReflexHelm: detected_language=%r for utterance %r",
                detected,
                utterance[:80],
            )
            try:
                bridge_state.detected_language = detected
            except Exception:
                pass

        # Validate the verb's target / interact_action against the
        # actually-installed peer helms / actions. If invalid, fall back.
        normalized = self._normalize_verb(
            verb,
            utterance=utterance,
            prior_was_question=prior_was_question,
            peer_helm_names={h["name"] for h in peer_helms},
            peer_action_names={a["name"] for a in peer_actions},
        )
        # Defensive: YIELD on a non-empty utterance leaves the user
        # without a response (walker yields out of Bridge; no other IA
        # publishes). Downgrade to a SHIFT to the default target so the
        # reasoning helm gets a chance — the prompt instructs the model
        # never to YIELD non-empty input, but real classifiers
        # occasionally do.
        if isinstance(normalized, YIELD):
            logger.info(
                "ReflexHelm: classifier YIELDed on non-empty utterance %r; "
                "downgrading to SHIFT(%s) so the reasoning helm handles it",
                utterance[:60],
                self.default_shift_target,
            )
            return self._safe_default_shift("classifier YIELDed on non-empty utterance")
        return normalized

    # ------------------------------------------------------------------
    # Peer discovery
    # ------------------------------------------------------------------

    async def _collect_peer_helms(self, agent: Any) -> List[Dict[str, Any]]:
        """Enumerate other :class:`BaseHelm` instances on the agent.

        Each entry is ``{"name", "purpose", "latency_class", "turn_lock"}``
        so the prompt builder can render lines without re-reading manifests.
        """
        if agent is None:
            return []
        try:
            actions_mgr = await agent.get_actions_manager()
            if actions_mgr is None:
                return []
            all_enabled = await actions_mgr.get_all_actions(enabled_only=True)
        except Exception as exc:
            logger.debug("ReflexHelm: peer-helm enumeration failed: %s", exc)
            return []

        peers: List[Dict[str, Any]] = []
        self_name = self.helm_name()
        for action in all_enabled:
            if not isinstance(action, BaseHelm):
                continue
            try:
                name = action.helm_name()
            except Exception:
                continue
            if action is self or name == self_name:
                continue
            try:
                manifest = action.get_manifest()
            except Exception:
                manifest = Manifest.from_payload(None)
            peers.append(
                {
                    "name": name,
                    "purpose": manifest.purpose,
                    "latency_class": manifest.latency_class,
                    "turn_lock": manifest.turn_lock,
                }
            )
        return peers

    async def _collect_peer_actions(
        self,
        agent: Any,
        conversation: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """Enumerate anchor-routable ``InteractAction`` instances (ADR-0009).

        Returns ``[{"name", "description", "anchors"}]`` for the
        anchor-routable IA catalog Reflex DELEGATES into.

        Exclusion filters (ADR-0009 §4):

        - Pattern orchestrators (``manifest.pattern_orchestrator``) —
          weight-routed, never anchor-routed.
        - Always-execute IAs (``always_execute=True``) — sidecar / audit
          IAs run on every turn via Bridge's curated walker queue, no
          anchor needed.
        - Chain-internal IAs (``manifest.routable_by_anchor=False``) —
          reached only via parent DELEGATE chains.

        Turn-locked IAs (``manifest.turn_lock=True``) ARE included.
        ``find_turn_lock_owner`` only fires for mid-flight turns once
        the IA has acquired the lock; first-entry routing still needs
        an anchor match (or engine recovery hatch). Excluding turn_lock
        IAs from the Reflex catalog left first-entry to Reasoning every
        time and produced the "Reflex never DELEGATEs" gap observed in
        live smoke against bridge_agent.

        IAs with no description AND no anchors are dropped — there is
        nothing for Reflex to match on. The bootstrap warning surfaces
        these to operators at install time.
        """
        if agent is None:
            return []
        try:
            from jvagent.action.interact.base import InteractAction

            actions_mgr = await agent.get_actions_manager()
            if actions_mgr is None:
                return []
            all_enabled = await actions_mgr.get_all_actions(enabled_only=True)
        except Exception as exc:
            logger.debug("ReflexHelm: peer-action enumeration failed: %s", exc)
            return []

        peers: List[Dict[str, Any]] = []
        for action in all_enabled:
            if not isinstance(action, InteractAction):
                continue
            cls_name = action.__class__.__name__
            # Defense-in-depth: legacy class-name exclusion preserved
            # alongside the manifest pattern_orchestrator flag.
            # TODO(wave-10): remove the literal once pattern_orchestrator
            # is universally adopted.
            if cls_name in ("BridgeInteractAction", "CockpitInteractAction"):
                continue
            try:
                manifest = action.get_manifest()
            except Exception:
                continue
            if manifest.pattern_orchestrator:
                continue
            if getattr(action, "always_execute", False):
                continue
            if not manifest.routable_by_anchor:
                continue
            # NOTE: turn_lock IAs are intentionally NOT filtered. They
            # need anchor-match for first entry; Bridge's
            # find_turn_lock_owner takes over for subsequent turns
            # once the lock is acquired (ADR-0009 §4 + post-Wave-9
            # correction).

            # Dynamic anchors override static ``self.anchors`` when the
            # IA opts in via ``get_anchors(conversation)``.
            anchors: List[str] = []
            try:
                dyn = await action.get_anchors(conversation)
                if dyn is not None:
                    anchors = [a for a in dyn if isinstance(a, str) and a.strip()]
                else:
                    static = getattr(action, "anchors", None) or []
                    anchors = [a for a in static if isinstance(a, str) and a.strip()]
            except Exception:
                static = getattr(action, "anchors", None) or []
                anchors = [a for a in static if isinstance(a, str) and a.strip()]

            description = (manifest.purpose or "").strip()
            if not description and not anchors:
                # Nothing to match on — skip rather than emit a noisy
                # empty block.
                continue
            peers.append(
                {"name": cls_name, "description": description, "anchors": anchors}
            )
        return peers

    # ------------------------------------------------------------------
    # Prompt assembly
    # ------------------------------------------------------------------

    def _build_system_prompt(
        self,
        peer_helms: List[Dict[str, Any]],
        peer_actions: List[Dict[str, Any]],
    ) -> str:
        helm_lines = [
            render_peer_helm_line(
                p["name"],
                purpose=p["purpose"],
                latency_class=p["latency_class"],
                turn_lock=p["turn_lock"],
            )
            for p in peer_helms
        ]
        action_blocks = [
            render_peer_action_block(
                a["name"],
                description=a.get("description", ""),
                anchors=a.get("anchors", []),
            )
            for a in peer_actions
        ]
        peer_helms_section = (
            "\n".join(helm_lines) if helm_lines else "(no peer helms installed)"
        )
        peer_actions_section = (
            "\n\n".join(action_blocks)
            if action_blocks
            else "(no anchor-routable flows installed)"
        )
        # The HELMS AVAILABLE block is rendered only when ≥2 deliberate
        # (non-Reflex) helms are installed. Single-Reasoning agents see
        # no block — prompt tokens stay tight.
        deliberate_peers = [
            {"name": p["name"], "purpose": p["purpose"]}
            for p in peer_helms
            if (p.get("latency_class") or "").lower() != "instant"
        ]
        helms_available_section = render_helms_available_block(deliberate_peers)
        return REFLEX_SYSTEM_PROMPT.format(
            peer_helms_section=peer_helms_section,
            helms_available_section=helms_available_section,
            peer_actions_section=peer_actions_section,
            anchor_disambiguation_clause=ANCHOR_DISAMBIGUATION_CLAUSE,
        )

    async def _build_history(self, visitor: "InteractWalker") -> str:
        """Render the last ``history_limit`` turns into a compact text block."""
        conversation = getattr(visitor, "conversation", None)
        if conversation is None:
            return ""
        try:
            interaction = getattr(visitor, "interaction", None)
            excluded = getattr(interaction, "id", None) if interaction else None
            # ``formatted=False`` is load-bearing here: the default
            # ``formatted=True`` returns ``{"role", "content"}`` pairs
            # (LM-ready chat messages), which would make every
            # ``turn.get("utterance")`` / ``turn.get("response")`` below
            # silently return None — Reflex would then see "(no prior
            # turns)" in its prompt every call. Raw format yields the
            # ``utterance`` / ``response`` keys we read.
            turns = await conversation.get_interaction_history(
                limit=max(1, int(self.history_limit)),
                excluded=excluded,
                with_utterance=True,
                with_response=True,
                formatted=False,
            )
        except Exception as exc:
            logger.debug("ReflexHelm: history fetch failed: %s", exc)
            return ""
        if not turns:
            return ""
        lines: List[str] = []
        for turn in turns:
            utt = (turn.get("utterance") or "").strip()
            resp = (turn.get("response") or "").strip()
            if utt:
                lines.append(f"USER: {utt}")
            if resp:
                lines.append(f"AGENT: {resp}")
        return "\n".join(lines)

    async def _prior_assistant_ended_with_question(
        self,
        visitor: "InteractWalker",
    ) -> bool:
        """Return True iff the immediately prior assistant turn ended with ``?``.

        Used by :meth:`_is_substantive_utterance` to detect continuation
        replies (``Yes`` / ``No`` / ``Sure``) after an assistant question.
        Without this signal, small classifier models occasionally EMIT
        the user's affirmative verbatim — live-smoke observed
        ``"Yes"`` echoed back when the assistant had just asked
        ``"Would you like me to search?"``.

        Returns False on any error or missing data — the guard then
        operates only on word-count + ``?`` signals as before.
        """
        conversation = getattr(visitor, "conversation", None)
        if conversation is None:
            return False
        try:
            interaction = getattr(visitor, "interaction", None)
            excluded = getattr(interaction, "id", None) if interaction else None
            # ``formatted=False`` is load-bearing: the default
            # ``formatted=True`` returns ``{"role", "content"}`` pairs,
            # which would silently make ``turn.get("response")`` below
            # always return None — the guard would never fire. Raw
            # format yields the ``response`` key we need.
            turns = await conversation.get_interaction_history(
                limit=1,
                excluded=excluded,
                with_utterance=False,
                with_response=True,
                formatted=False,
            )
        except Exception as exc:
            logger.debug("ReflexHelm: prior-question check failed: %s", exc)
            return False
        if not turns:
            return False
        # Iterate from the most recent backward so we find the latest
        # assistant response with non-empty content.
        for turn in reversed(turns):
            if not isinstance(turn, dict):
                continue
            resp = (turn.get("response") or "").strip()
            if not resp:
                continue
            # Strip trailing punctuation/whitespace except ``?`` so
            # ``"...search?\n"`` and ``"...search? "`` both match.
            stripped = resp.rstrip()
            return stripped.endswith("?")
        return False

    # ------------------------------------------------------------------
    # Classification call
    # ------------------------------------------------------------------

    async def _classify(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> Optional[Dict[str, Any]]:
        """Run the fast-model call and parse the JSON verb out of the response.

        Returns ``None`` when the model is unavailable, the call times out,
        or the output cannot be parsed.
        """
        model_action = await self.get_model_action(required=False)
        if model_action is None:
            logger.warning(
                "ReflexHelm: no model action available "
                "(model_action_type=%r); falling back to default shift",
                self.model_action_type,
            )
            return None

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            # Pass ``system`` and ``prompt_for_observability`` explicitly so the
            # ``model_call`` observability event carries the structured prompt
            # fields. Without these, the emitter sees None and drops
            # ``system_prompt`` / ``user_prompt`` from the recorded event —
            # making the call non-debuggable from logs alone.
            # ``calling_action_name`` mirrors the same convention so the
            # event records which helm originated the call.
            result = await model_action.query_messages(
                messages=messages,
                stream=False,
                system=system_prompt,
                prompt_for_observability=user_prompt,
                tools=None,
                model=self.model or None,
                temperature=self.model_temperature,
                max_tokens=self.model_max_tokens,
                calling_action_name=self.helm_name(),
            )
        except Exception as exc:
            logger.warning("ReflexHelm: model call raised: %s", exc)
            return None

        raw = (getattr(result, "response", None) or "").strip()
        if not raw:
            logger.debug("ReflexHelm: empty model response")
            return None

        parsed = _parse_json_verb(raw)
        if parsed is None:
            logger.warning(
                "ReflexHelm: failed to parse JSON verb from response: %r",
                raw[:200],
            )
        return parsed

    # ------------------------------------------------------------------
    # Verb normalisation
    # ------------------------------------------------------------------

    # Word count above which an utterance is considered substantive and
    # cannot be EMIT'd directly. Trivial smalltalk (greetings, thanks,
    # short acks) is always ≤3 words; anything longer is overwhelmingly
    # a real request that needs SHIFT to a reasoning helm.
    #
    # Failure mode this prevents (live-smoke observed on
    # llama-3.1-8b-instant):
    #
    #   user: "Search the web for the current weather in San Francisco today"
    #   model: {"verb": "EMIT", "text": "Buscando ahora el clima en San
    #            Francisco hoy"}
    #
    # The model conflated transient_ack-style content with the EMIT verb,
    # short-circuiting the turn before Reasoning ran. The defensive guard
    # below downgrades to SHIFT regardless of what the model said.
    _SUBSTANTIVE_UTTERANCE_WORD_THRESHOLD = 3

    def _normalize_verb(
        self,
        parsed: Dict[str, Any],
        *,
        utterance: str,
        prior_was_question: bool = False,
        peer_helm_names: set,
        peer_action_names: set,
    ) -> HelmStepResult:
        """Convert the parsed JSON into a validated helm verb.

        Validation steps:
        - SHIFT target must be in ``peer_helm_names`` — else fall back.
        - DELEGATE interact_action must be in ``peer_action_names`` — else
          fall back.
        - EMIT requires ``can_emit_directly`` AND a non-substantive
          utterance (see :meth:`_is_substantive_utterance` — also forces
          SHIFT when the prior assistant turn ended with a question and
          the current utterance is a continuation).
        - Unknown verbs fall back to the default shift target.
        """
        verb = (parsed.get("verb") or "").strip().upper()

        if verb == "EMIT":
            if not self.can_emit_directly:
                return self._safe_default_shift(
                    "can_emit_directly=False, declining EMIT"
                )
            text = (parsed.get("text") or "").strip()
            if not text:
                return self._safe_default_shift("EMIT verb missing text")
            # Defensive override: substantive utterances must SHIFT.
            if self._is_substantive_utterance(
                utterance, prior_was_question=prior_was_question
            ):
                reason = (
                    "continuation after assistant question"
                    if prior_was_question
                    else "substantive utterance"
                )
                logger.warning(
                    "ReflexHelm: model returned EMIT for %s %r (text=%r); "
                    "overriding to SHIFT(%s) — likely model mis-classification.",
                    reason,
                    utterance[:80],
                    text[:80],
                    self.default_shift_target,
                )
                return self._safe_default_shift(
                    f"EMIT on {reason} (defensive override)"
                )
            return EMIT(text=text, finalize=True)

        if verb == "SHIFT":
            target = (parsed.get("target") or "").strip()
            if target not in peer_helm_names:
                logger.info(
                    "ReflexHelm: SHIFT target %r not in peer helms %s; "
                    "falling back to default %r",
                    target,
                    sorted(peer_helm_names),
                    self.default_shift_target,
                )
                target = self.default_shift_target
                if target not in peer_helm_names:
                    return self._safe_default_emit("no valid SHIFT target available")
            return SHIFT(
                target=target,
                reason=(parsed.get("reason") or "ReflexHelm shift").strip(),
                transient_ack=(parsed.get("transient_ack") or None),
                handoff_state=parsed.get("handoff_state") or None,
            )

        if verb == "DELEGATE":
            ia = (parsed.get("interact_action") or "").strip()
            if ia not in peer_action_names:
                logger.info(
                    "ReflexHelm: DELEGATE target %r not in peer actions %s; "
                    "falling back to default shift",
                    ia,
                    sorted(peer_action_names),
                )
                return self._safe_default_shift("DELEGATE target unknown")
            return DELEGATE(
                interact_action=ia,
                args=parsed.get("args") or None,
            )

        if verb == "YIELD":
            logger.debug(
                "ReflexHelm: classifier yielded (reason=%r)",
                parsed.get("reason"),
            )
            return YIELD()

        # Unknown verb — safe default.
        logger.warning(
            "ReflexHelm: unknown verb %r; falling back to default shift", verb
        )
        return self._safe_default_shift(f"unknown verb {verb!r}")

    def _is_substantive_utterance(
        self,
        utterance: str,
        *,
        prior_was_question: bool = False,
    ) -> bool:
        """Heuristic: True iff this utterance should never get EMIT.

        Three signals make an utterance substantive (force SHIFT):

        1. The utterance contains a ``?`` (interrogative).
        2. The utterance has more than
           ``_SUBSTANTIVE_UTTERANCE_WORD_THRESHOLD`` words.
        3. The prior assistant turn ended with a question — any
           single-word reply ("Yes", "No", "Sure", "Maybe") is a
           continuation of that question and must SHIFT to Reasoning,
           never EMIT back. Without this signal, models occasionally
           echo the user's affirmative as the response (live-smoke
           observed: assistant asked "Would you like me to search?",
           user said "Yes", Reflex EMIT'd "Yes" verbatim).

        Returns False (EMIT allowed) only for short non-interrogative
        utterances when the assistant didn't just ask a question. The
        model's own ``detected_language`` + content choice handle the
        rest.
        """
        text = (utterance or "").strip()
        if not text:
            return False
        if prior_was_question:
            return True
        if "?" in text:
            return True
        word_count = len(text.split())
        return word_count > self._SUBSTANTIVE_UTTERANCE_WORD_THRESHOLD

    def _safe_default_shift(self, reason: str) -> HelmStepResult:
        """Build a fallback SHIFT to the configured default target.

        If the default target isn't even installed, fall back to a fallback
        EMIT so the turn doesn't silently die.
        """
        target = self.default_shift_target
        if not target:
            return self._safe_default_emit(reason)
        return SHIFT(
            target=target,
            reason=f"ReflexHelm safe-default: {reason}",
            transient_ack=None,
        )

    def _safe_default_emit(self, reason: str) -> HelmStepResult:
        """Last-resort EMIT when classification AND shift both fail.

        Fires only when Reflex cannot classify the utterance AND the
        configured ``default_shift_target`` is not an installed peer
        helm — i.e. Bridge is misconfigured. Honest fallback text;
        ``finalize=True`` ends the turn (do NOT re-enqueue — that would
        loop into the same failure).
        """
        text = (
            self.fallback_text
            or "Sorry — I couldn't process that. Could you rephrase or try again?"
        )
        logger.warning(
            "ReflexHelm: emitting fallback text (%s) — check Bridge helm "
            "configuration: default_shift_target=%r must resolve to an "
            "installed BaseHelm peer.",
            reason,
            self.default_shift_target,
        )
        return EMIT(text=text, finalize=True)


# ---------------------------------------------------------------------------
# JSON parsing helper
# ---------------------------------------------------------------------------


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json_verb(raw: str) -> Optional[Dict[str, Any]]:
    """Extract the first valid JSON object from the model response.

    Tolerates leading / trailing prose around the JSON object. Returns
    ``None`` when no parseable object is found.
    """
    candidate = raw.strip()
    # First attempt: the whole response parses as JSON.
    try:
        obj = json.loads(candidate)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # Second attempt: greedy regex for `{...}` substring.
    match = _JSON_OBJECT_RE.search(candidate)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        return None
    return None
