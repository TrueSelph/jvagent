"""Cockpit routing types and formatting utilities."""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Posture constants
# ---------------------------------------------------------------------------

POSTURE_RESPOND = "RESPOND"
POSTURE_SUPPRESS = "SUPPRESS"
POSTURE_DEFER = "DEFER"
VALID_POSTURES = (POSTURE_RESPOND, POSTURE_SUPPRESS, POSTURE_DEFER)

# Declarative intent types (used by _normalize_intent_type)
INTENT_TYPES = [
    "CONVERSATIONAL",
    "INFORMATIONAL",
    "DIRECTIVE",
    "INTERACTIVE",
    "UNCLEAR",
]


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
    """Structured routing result from CockpitRouter."""

    posture: str = POSTURE_RESPOND
    interpretation: str = ""
    intent_type: str = "UNCLEAR"
    actions: List[str] = field(default_factory=list)
    interact_actions: List[str] = field(default_factory=list)
    confidence: float = 0.0
    verification: Optional[VerificationTrace] = None
    extracted_entities: ExtractedEntities = field(default_factory=dict)
    canned_response: str = ""
    needs_clarification: bool = False
    raw_response: str = ""

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "posture": self.posture,
            "interpretation": self.interpretation,
            "intent_type": self.intent_type,
            "actions": self.actions,
            "confidence": self.confidence,
            "extracted_entities": self.extracted_entities,
            "canned_response": self.canned_response,
            "needs_clarification": self.needs_clarification,
        }
        if self.verification:
            result["verification"] = self.verification.to_dict()
        if self.interact_actions:
            result["interact_actions"] = self.interact_actions
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any], raw_response: str = "") -> "RoutingResult":
        verification_data = data.get("verification")
        entities_data = data.get("extracted_entities", {})

        has_split_schema = "interact_actions" in data or (
            "skills" in data and "actions" not in data
        )
        if has_split_schema:
            parsed_actions = cls._parse_actions(data.get("skills", []))
            parsed_interact_actions = cls._parse_actions(
                data.get("interact_actions", [])
            )
        else:
            parsed_actions = cls._parse_actions(
                data.get("actions", data.get("skills", []))
            )
            parsed_interact_actions = []

        return cls(
            posture=cls._normalize_posture(data.get("posture", POSTURE_RESPOND)),
            interpretation=data.get("interpretation", ""),
            intent_type=cls._normalize_intent_type(data.get("intent_type", "UNCLEAR")),
            actions=parsed_actions,
            interact_actions=parsed_interact_actions,
            confidence=cls._parse_confidence(data.get("confidence", 0.0)),
            verification=(
                VerificationTrace.from_dict(verification_data)
                if verification_data
                else None
            ),
            extracted_entities=entities_data if isinstance(entities_data, dict) else {},
            canned_response=data.get("canned_response", ""),
            needs_clarification=bool(data.get("needs_clarification", False)),
            raw_response=raw_response,
        )

    @classmethod
    def error_result(cls, error_message: str, utterance: str = "") -> "RoutingResult":
        return cls(
            posture=POSTURE_RESPOND,
            interpretation=f"Routing error: {error_message}. User said: {utterance[:50]}",
            intent_type="UNCLEAR",
            actions=[],
            confidence=0.0,
            verification=None,
            needs_clarification=True,
        )

    @staticmethod
    def _normalize_posture(posture_value: Any) -> str:
        if not posture_value:
            return POSTURE_RESPOND
        posture_str = str(posture_value).strip().upper()
        if posture_str in VALID_POSTURES:
            return posture_str
        logger.warning(f"Unrecognized posture '{posture_str}', defaulting to RESPOND")
        return POSTURE_RESPOND

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
    def _parse_actions(actions_value: Any) -> List[str]:
        if not actions_value:
            return []
        if isinstance(actions_value, list):
            return [str(a).strip() for a in actions_value if a and str(a).strip()]
        if isinstance(actions_value, str):
            try:
                parsed = json.loads(actions_value)
                if isinstance(parsed, list):
                    return [str(a).strip() for a in parsed if a and str(a).strip()]
                return [actions_value.strip()] if actions_value.strip() else []
            except (json.JSONDecodeError, ValueError):
                return [actions_value.strip()] if actions_value.strip() else []
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

    def is_respond(self) -> bool:
        return self.posture == POSTURE_RESPOND

    def is_suppress(self) -> bool:
        return self.posture == POSTURE_SUPPRESS

    def is_defer(self) -> bool:
        return self.posture == POSTURE_DEFER

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
    """Parse LLM response string into RoutingResult."""
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

        # Enforce CONVERSATIONAL intent rule
        if result.intent_type == "CONVERSATIONAL" and (
            result.actions or result.interact_actions
        ):
            logger.debug(
                "RoutingResult: Enforcing CONVERSATIONAL intent rule - clearing routes"
            )
            result.actions = []
            result.interact_actions = []

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
