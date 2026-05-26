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
- **can_interrupt: True**: Reflex may emit ``SHIFT(interrupt=True)`` to
  break a turn-lock; e.g. when an interview is active and the user
  says "stop".
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
    REFLEX_SYSTEM_PROMPT,
    REFLEX_USER_PROMPT_TEMPLATE,
    render_peer_action_line,
    render_peer_helm_line,
)
from jvagent.action.manifest import Manifest

if TYPE_CHECKING:
    from jvagent.action.bridge.state import BridgeState
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


DEFAULT_REFLEX_MODEL = "gpt-4o-mini"
DEFAULT_REFLEX_MODEL_ACTION = "OpenAILanguageModelAction"


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
    can_interrupt: bool = attribute(default=True)

    model: str = attribute(default=DEFAULT_REFLEX_MODEL)
    model_action_type: str = attribute(default=DEFAULT_REFLEX_MODEL_ACTION)
    model_temperature: float = attribute(default=0.0)
    model_max_tokens: int = attribute(default=256)
    timeout_seconds: float = attribute(default=3.0)
    history_limit: int = attribute(default=4)

    default_shift_target: str = attribute(default="ReasoningHelm")
    fallback_text: str = attribute(default="Got it — one moment.")

    # ------------------------------------------------------------------
    # Step entry point
    # ------------------------------------------------------------------

    async def step(
        self,
        visitor: "InteractWalker",
        bridge_state: "BridgeState",
    ) -> HelmStepResult:
        """Run one classification + return a verb."""
        utterance = (getattr(visitor, "utterance", None) or "").strip()
        if not utterance:
            # Empty utterance — yield silently (no point burning an LM call).
            logger.debug("ReflexHelm: empty utterance — yielding")
            return YIELD()

        try:
            agent = await self.get_agent()
        except Exception as exc:
            logger.warning("ReflexHelm: get_agent failed: %s", exc)
            return self._safe_default_shift("agent unavailable")

        peer_helms = await self._collect_peer_helms(agent)
        peer_actions = await self._collect_peer_actions(agent)

        system_prompt = self._build_system_prompt(peer_helms, peer_actions)
        history = await self._build_history(visitor)
        user_prompt = REFLEX_USER_PROMPT_TEMPLATE.format(
            history_section=history or "(no prior turns)",
            utterance=utterance,
        )

        verb = await self._classify(system_prompt, user_prompt)
        if verb is None:
            return self._safe_default_shift("classification failed")

        # Validate the verb's target / interact_action against the
        # actually-installed peer helms / actions. If invalid, fall back.
        normalized = self._normalize_verb(
            verb,
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

        Each entry is ``{"name", "purpose", "latency_class", "turn_lock",
        "can_interrupt"}`` so the prompt builder can render lines without
        re-reading manifests.
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
                    "can_interrupt": manifest.can_interrupt,
                }
            )
        return peers

    async def _collect_peer_actions(self, agent: Any) -> List[Dict[str, Any]]:
        """Enumerate rails ``InteractAction`` instances with declared manifests.

        Returns ``[{"name", "purpose"}]`` for use in the DELEGATE
        candidate list. Actions without a declared ``purpose`` in their
        manifest are filtered out — Reflex needs a description to pick
        them.
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
            # Exclude Bridge itself + any other top-level orchestrator IAs
            # so Reflex can't DELEGATE to its own surrounding container.
            cls_name = action.__class__.__name__
            if cls_name in ("BridgeInteractAction", "CockpitInteractAction"):
                continue
            try:
                manifest = action.get_manifest()
            except Exception:
                continue
            purpose = (manifest.purpose or "").strip()
            if not purpose:
                continue
            peers.append({"name": cls_name, "purpose": purpose})
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
                can_interrupt=p["can_interrupt"],
            )
            for p in peer_helms
        ]
        action_lines = [
            render_peer_action_line(a["name"], purpose=a["purpose"])
            for a in peer_actions
        ]
        peer_helms_section = (
            "\n".join(helm_lines) if helm_lines else "(no peer helms installed)"
        )
        peer_actions_section = (
            "\n".join(action_lines)
            if action_lines
            else "(no rails actions with declared manifests)"
        )
        return REFLEX_SYSTEM_PROMPT.format(
            peer_helms_section=peer_helms_section,
            peer_actions_section=peer_actions_section,
        )

    async def _build_history(self, visitor: "InteractWalker") -> str:
        """Render the last ``history_limit`` turns into a compact text block."""
        conversation = getattr(visitor, "conversation", None)
        if conversation is None:
            return ""
        try:
            interaction = getattr(visitor, "interaction", None)
            excluded = getattr(interaction, "id", None) if interaction else None
            turns = await conversation.get_interaction_history(
                limit=max(1, int(self.history_limit)),
                excluded=excluded,
                with_utterance=True,
                with_response=True,
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
            result = await model_action.query_messages(
                messages=messages,
                stream=False,
                tools=None,
                model=self.model or None,
                temperature=self.model_temperature,
                max_tokens=self.model_max_tokens,
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

    def _normalize_verb(
        self,
        parsed: Dict[str, Any],
        *,
        peer_helm_names: set,
        peer_action_names: set,
    ) -> HelmStepResult:
        """Convert the parsed JSON into a validated helm verb.

        Validation steps:
        - SHIFT target must be in ``peer_helm_names`` — else fall back.
        - DELEGATE interact_action must be in ``peer_action_names`` — else
          fall back.
        - EMIT requires ``can_emit_directly`` — else fall back.
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
                interrupt=bool(parsed.get("interrupt", False)),
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
        """Last-resort EMIT when neither classification nor shift can proceed."""
        text = self.fallback_text or "Got it."
        logger.warning("ReflexHelm: emitting fallback text (%s)", reason)
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
