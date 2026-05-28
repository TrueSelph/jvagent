"""Engine routing types and formatting utilities.

Unified-capabilities catalog (ADR-0008, Wave 6):

The router emits a single :class:`RoutingResult` with ``selected: List[CapabilityRef]``
in place of the older parallel ``actions`` / ``interact_actions`` lists. After
the router LLM call, the dispatch decode reads each capability's ``kind``
from the registry and produces a :class:`DispatchPlan` whose ``regime`` tells
ReasoningHelm whether to run the engine, skip it (IAs-only), or run with a
slim/regime-aware prompt.

Backcompat shims: ``RoutingResult.actions`` / ``.skills`` / ``.interact_actions``
remain as derived properties for one release so downstream consumers
(observability, log queries) continue to work while they migrate to
``.selected``. The properties are read-only — internal callers update
``selected`` directly.

Posture removal: the ``posture`` field is removed in Wave 6. ReflexHelm gates
SUPPRESS/DEFER upstream; CLARIFY emerged from posture and is no longer
structurally distinguished. The model produces clarifying questions naturally
from context.
"""

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

logger = logging.getLogger(__name__)

# Declarative intent types (used by _normalize_intent_type)
INTENT_TYPES = [
    "CONVERSATIONAL",
    "INFORMATIONAL",
    "DIRECTIVE",
    "INTERACTIVE",
    "UNCLEAR",
]


# ---------------------------------------------------------------------------
# CapabilityRef + DispatchRegime + DispatchPlan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapabilityRef:
    """A single capability selected by the router.

    ``name`` is the capability's catalog key (skill name or InteractAction
    class name). ``kind`` is populated by post-routing decode from the
    capability registry — the model does NOT classify kind; it only picks
    by name.
    """

    name: str
    kind: Literal["skill", "ia"]


class DispatchRegime(str, Enum):
    """Four explicit regimes computed from the capability decode.

    ``SKILLS_ONLY`` and ``MIXED`` run the engine LM loop with regime-aware
    prompt assembly. ``IAS_ONLY`` skips the engine LM call entirely —
    ReasoningHelm pops the DELEGATE chain and yields. ``NONE`` (no
    capabilities selected) still runs the engine but with a posture-only
    prompt and no tool surface (zero-iteration response).
    """

    SKILLS_ONLY = "skills_only"
    IAS_ONLY = "ias_only"
    MIXED = "mixed"
    NONE = "none"


@dataclass(frozen=True)
class DispatchPlan:
    """Regime + capability split, produced by :func:`decode_dispatch_plan`."""

    regime: DispatchRegime
    skills: List[CapabilityRef] = field(default_factory=list)
    ias: List[CapabilityRef] = field(default_factory=list)


def decode_dispatch_plan(routing: "RoutingResult") -> DispatchPlan:
    """Map a :class:`RoutingResult` to a :class:`DispatchPlan`.

    Reads ``routing.selected``, partitions by ``kind``, and returns the
    regime that matches. Empty selection → :class:`DispatchRegime.NONE`.
    """
    skills = [c for c in routing.selected if c.kind == "skill"]
    ias = [c for c in routing.selected if c.kind == "ia"]
    if skills and ias:
        regime = DispatchRegime.MIXED
    elif skills:
        regime = DispatchRegime.SKILLS_ONLY
    elif ias:
        regime = DispatchRegime.IAS_ONLY
    else:
        regime = DispatchRegime.NONE
    return DispatchPlan(regime=regime, skills=skills, ias=ias)


# ---------------------------------------------------------------------------
# VerificationTrace
# ---------------------------------------------------------------------------


@dataclass
class VerificationTrace:
    """Verification trace from Chain of Verification process."""

    intent_check: str = ""
    action_check: str = ""
    issues_found: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent_check": self.intent_check,
            "action_check": self.action_check,
            "issues_found": self.issues_found,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VerificationTrace":
        return cls(
            intent_check=data.get("intent_check", ""),
            action_check=data.get("action_check", ""),
            issues_found=data.get("issues_found", []),
        )


# Type alias for extracted entities
ExtractedEntities = Dict[str, Any]


# ---------------------------------------------------------------------------
# RoutingResult
# ---------------------------------------------------------------------------


@dataclass
class RoutingResult:
    """Structured routing result from EngineRouter (ADR-0008 shape).

    Fields:

    - ``selected``: the unified capability list. Authoritative; everything
      else is derived from this.
    - ``interpretation``, ``intent_type``, ``confidence``, ``verification``,
      ``extracted_entities``, ``needs_clarification``, ``raw_response``:
      auxiliary metadata about the routing decision.

    Backcompat properties (deprecated, one-release window):

    - ``actions``: skill names — derived from ``selected``.
    - ``skills``: alias of ``actions``.
    - ``interact_actions``: IA class names — derived from ``selected``.
    """

    selected: List[CapabilityRef] = field(default_factory=list)
    interpretation: str = ""
    intent_type: str = "UNCLEAR"
    confidence: float = 0.0
    verification: Optional[VerificationTrace] = None
    extracted_entities: ExtractedEntities = field(default_factory=dict)
    needs_clarification: bool = False
    raw_response: str = ""

    # ------------------------------------------------------------------
    # Backcompat properties (read-only).
    # ------------------------------------------------------------------

    @property
    def actions(self) -> List[str]:
        """Deprecated: skill names derived from ``selected``.

        Internal callers should read ``selected`` directly. Kept as a
        property for one release so observability / log consumers
        reading ``routing.actions`` continue to work during migration.
        """
        return [c.name for c in self.selected if c.kind == "skill"]

    @property
    def skills(self) -> List[str]:
        """Deprecated alias of :attr:`actions` (same data)."""
        return self.actions

    @property
    def interact_actions(self) -> List[str]:
        """Deprecated: IA class names derived from ``selected``.

        See :attr:`actions` for the deprecation note.
        """
        return [c.name for c in self.selected if c.kind == "ia"]

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "interpretation": self.interpretation,
            "intent_type": self.intent_type,
            "selected": [{"name": c.name, "kind": c.kind} for c in self.selected],
            # Backcompat surface for downstream consumers that read the
            # split fields directly from the cache payload / event log.
            "actions": self.actions,
            "interact_actions": self.interact_actions,
            "confidence": self.confidence,
            "extracted_entities": self.extracted_entities,
            "needs_clarification": self.needs_clarification,
        }
        if self.verification:
            result["verification"] = self.verification.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any], raw_response: str = "") -> "RoutingResult":
        verification_data = data.get("verification")
        entities_data = data.get("extracted_entities", {})

        selected = cls._parse_selected(data)

        return cls(
            selected=selected,
            interpretation=data.get("interpretation", ""),
            intent_type=cls._normalize_intent_type(data.get("intent_type", "UNCLEAR")),
            confidence=cls._parse_confidence(data.get("confidence", 0.0)),
            verification=(
                VerificationTrace.from_dict(verification_data)
                if verification_data
                else None
            ),
            extracted_entities=entities_data if isinstance(entities_data, dict) else {},
            needs_clarification=bool(data.get("needs_clarification", False)),
            raw_response=raw_response,
        )

    @classmethod
    def error_result(cls, error_message: str, utterance: str = "") -> "RoutingResult":
        return cls(
            selected=[],
            interpretation=f"Routing error: {error_message}. User said: {utterance[:50]}",
            intent_type="UNCLEAR",
            confidence=0.0,
            verification=None,
            needs_clarification=True,
        )

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @classmethod
    def _parse_selected(cls, data: Dict[str, Any]) -> List[CapabilityRef]:
        """Build the ``selected`` list from a payload.

        Accepts three shapes, in order of preference:

        1. ``selected: [{"name": "...", "kind": "skill|ia"}, ...]`` — the
           ADR-0008 canonical shape.
        2. Split ``skills`` / ``interact_actions`` lists — legacy LLM
           output and pre-Wave-6 cache entries.
        3. ``actions`` field alone — older legacy shape (no IAs).
        """
        raw_selected = data.get("selected")
        if isinstance(raw_selected, list) and raw_selected:
            out: List[CapabilityRef] = []
            for item in raw_selected:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                kind = str(item.get("kind", "")).strip().lower()
                if not name or kind not in ("skill", "ia"):
                    continue
                out.append(CapabilityRef(name=name, kind=kind))  # type: ignore[arg-type]
            return out

        # Legacy split-schema fallback.
        skills_payload = data.get("skills")
        interact_payload = data.get("interact_actions")
        has_split = isinstance(skills_payload, list) or isinstance(
            interact_payload, list
        )
        actions_payload = data.get("actions")
        if has_split:
            skill_names = cls._parse_names(skills_payload)
            ia_names = cls._parse_names(interact_payload)
        else:
            skill_names = cls._parse_names(actions_payload)
            ia_names = []

        legacy_out: List[CapabilityRef] = []
        legacy_out.extend(CapabilityRef(name=n, kind="skill") for n in skill_names)
        legacy_out.extend(CapabilityRef(name=n, kind="ia") for n in ia_names)
        return legacy_out

    @staticmethod
    def _normalize_intent_type(intent_value: Any) -> str:
        if not intent_value:
            return "UNCLEAR"
        intent_str = str(intent_value).strip().upper()
        if intent_str in INTENT_TYPES:
            return intent_str
        logger.warning(
            f"Unrecognized intent type '{intent_str}', defaulting to UNCLEAR"
        )
        return "UNCLEAR"

    @staticmethod
    def _parse_names(value: Any) -> List[str]:
        if not value:
            return []
        if isinstance(value, list):
            return [str(a).strip() for a in value if a and str(a).strip()]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [str(a).strip() for a in parsed if a and str(a).strip()]
                return [value.strip()] if value.strip() else []
            except (json.JSONDecodeError, ValueError):
                return [value.strip()] if value.strip() else []
        return []

    @staticmethod
    def _parse_confidence(confidence_value: Any) -> float:
        if confidence_value is None:
            return 0.0
        try:
            confidence = float(confidence_value)
            return max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            return 0.0

    def is_conversational(self) -> bool:
        return self.intent_type == "CONVERSATIONAL"

    def is_unclear(self) -> bool:
        return self.intent_type == "UNCLEAR"

    def should_clarify(self, threshold: float = 0.7) -> bool:
        return self.needs_clarification or self.confidence < threshold


# ---------------------------------------------------------------------------
# parse_routing_response
# ---------------------------------------------------------------------------


def parse_routing_response(response: str) -> RoutingResult:
    r"""Parse LLM response string into :class:`RoutingResult`.

    Tolerates JSON wrapped in ``\`\`\`json`` fences and extra surrounding
    prose. The CONVERSATIONAL invariant from the legacy parser is
    preserved: CONVERSATIONAL intent forces an empty ``selected``.
    """
    if not response:
        return RoutingResult.error_result("Empty response from LLM")

    json_str = response.strip()

    # Handle markdown code blocks
    if "```json" in json_str:
        start = json_str.find("```json") + 7
        end = json_str.find("```", start)
        if end > start:
            json_str = json_str[start:end].strip()
    elif "```" in json_str:
        start = json_str.find("```") + 3
        end = json_str.find("```", start)
        if end > start:
            json_str = json_str[start:end].strip()

    # Find JSON object boundaries
    if "{" in json_str:
        start = json_str.find("{")
        depth = 0
        end = start
        for i, char in enumerate(json_str[start:], start):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        json_str = json_str[start:end]

    try:
        data = json.loads(json_str)
        result = RoutingResult.from_dict(data, raw_response=response)

        # CONVERSATIONAL invariant: never route to capabilities.
        if result.intent_type == "CONVERSATIONAL" and result.selected:
            logger.debug(
                "RoutingResult: Enforcing CONVERSATIONAL intent rule - clearing selected"
            )
            result.selected = []

        return result

    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse routing response as JSON: {e}")
        return RoutingResult.error_result(f"JSON parse error: {e}", response[:100])


# ---------------------------------------------------------------------------
# format_interaction_history
# ---------------------------------------------------------------------------


def format_interaction_history(
    interaction_history: List[Dict[str, Any]],
    conversation: Optional[Any] = None,
) -> str:
    """Format interaction history for the routing prompt with context signals."""
    if not interaction_history:
        return "(No previous conversation)"

    first_entry = interaction_history[0] if interaction_history else {}
    is_role_content = (
        isinstance(first_entry, dict)
        and "role" in first_entry
        and "content" in first_entry
    )

    context_signals = []
    last_assistant_msg = None

    if is_role_content:
        for entry in reversed(interaction_history):
            if isinstance(entry, dict) and entry.get("role") == "assistant":
                last_assistant_msg = entry.get("content") or ""
                break

        if last_assistant_msg and last_assistant_msg.strip().endswith("?"):
            context_signals.append("Most recent assistant message is a question")

        for e in reversed(interaction_history):
            if isinstance(e, dict) and e.get("role") == "system":
                content = e.get("content") or ""
                if content.startswith("[SUPPRESSED]"):
                    context_signals.append(
                        "Agent did not respond to recent message (suppressed)"
                    )
                    break
                if content.startswith("[DEFERRED]"):
                    context_signals.append("Deferred fragment(s) pending from user")
                    break
    else:
        for entry in reversed(interaction_history):
            if isinstance(entry, dict) and "ai" in entry:
                ai_msg = entry["ai"]
                if ai_msg and ai_msg.strip().endswith("?"):
                    context_signals.append(
                        "Most recent assistant message is a question"
                    )
                    break

    lines = []
    if context_signals:
        context_line = "Context: " + ". ".join(context_signals) + "."
        lines.append(context_line)
        lines.append("")

    for entry in interaction_history:
        if isinstance(entry, dict):
            if is_role_content:
                role = entry.get("role", "")
                content = entry.get("content") or ""
                if role == "user":
                    lines.append(f"User: {content}")
                elif role == "assistant":
                    if content.strip().endswith("?"):
                        lines.append(f"Assistant (question): {content}")
                    else:
                        lines.append(f"Assistant: {content}")
                elif role == "system":
                    if (content or "").startswith("[EVENT]"):
                        lines.append(content)
                    elif (content or "").startswith("[SUPPRESSED]") or (
                        content or ""
                    ).startswith("[DEFERRED]"):
                        lines.append(content)
                    elif (content or "").startswith("[INTERPRETATION]"):
                        lines.append(content)
                    elif content:
                        lines.append(content)
            else:
                if "human" in entry:
                    lines.append(f"User: {entry['human']}")
                elif "utterance" in entry:
                    lines.append(f"User: {entry['utterance']}")
                if "ai" in entry:
                    ai_msg = entry["ai"]
                    if ai_msg and ai_msg.strip().endswith("?"):
                        lines.append(f"Assistant (question): {ai_msg}")
                    else:
                        lines.append(f"Assistant: {ai_msg}")
                elif "response" in entry and entry["response"]:
                    resp = entry["response"]
                    if resp.strip().endswith("?"):
                        lines.append(f"Assistant (question): {resp}")
                    else:
                        lines.append(f"Assistant: {resp}")
                if "events" in entry:
                    for event in entry["events"]:
                        ev_str = (
                            event.get("content", event)
                            if isinstance(event, dict)
                            else str(event)
                        )
                        lines.append(f"[EVENT] {ev_str}")
        elif isinstance(entry, str):
            lines.append(entry)

    if lines:
        lines.append("")
        lines.append("---")
        lines.append(">>> USER RESPONDS NOW <<<")
        lines.append("---")

    return "\n".join(lines) if lines else "(No previous conversation)"
