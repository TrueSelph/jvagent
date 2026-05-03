"""RoutingResult dataclass for structured routing output.

This module provides the RoutingResult dataclass that encapsulates
the output of the InteractRouter's unified classification (posture + routing).
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Posture constants (RESPOND/SUPPRESS/DEFER)
POSTURE_RESPOND = "RESPOND"
POSTURE_SUPPRESS = "SUPPRESS"
POSTURE_DEFER = "DEFER"
VALID_POSTURES = (POSTURE_RESPOND, POSTURE_SUPPRESS, POSTURE_DEFER)


@dataclass
class VerificationTrace:
    """Verification trace from Chain of Verification process.

    Captures the model's self-verification reasoning for debugging
    and observability purposes.

    Attributes:
        intent_check: Reasoning confirming or correcting intent classification
        action_check: Reasoning confirming action selection matches anchors
        issues_found: List of problems identified during verification
    """

    intent_check: str = ""
    action_check: str = ""
    issues_found: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "intent_check": self.intent_check,
            "action_check": self.action_check,
            "issues_found": self.issues_found,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VerificationTrace":
        """Create from dictionary."""
        return cls(
            intent_check=data.get("intent_check", ""),
            action_check=data.get("action_check", ""),
            issues_found=data.get("issues_found", []),
        )


# Type alias for extracted entities - fully generic and declarative
# The router extracts whatever entities are relevant without enforcing a schema
# Downstream actions interpret the dict as needed for their specific use cases
ExtractedEntities = Dict[str, Any]


@dataclass
class RoutingResult:
    """Structured routing result from InteractRouter.

    Encapsulates all outputs from the routing process including interpretation,
    matched actions, confidence, extracted entities, and optional canned response.

    Attributes:
        posture: Response posture (RESPOND | SUPPRESS | DEFER) from posture classification
        interpretation: Synopsis of user intent and why this posture applies. Covers both posture justification and intent summary.
        intent_type: Classified intent (CONVERSATIONAL, INFORMATIONAL, INTERACTIVE, DIRECTIVE, UNCLEAR)
        actions: List of matched action names to route to
        confidence: Confidence score (0.0-1.0)
        verification: Optional self-verification trace (issues_found / passed / reasoning) used by InteractRouter to decide whether to escalate to clarification
        extracted_entities: Generic dict with extracted entity data (no predefined schema)
        canned_response: Optional brief acknowledgment for immediate response
        needs_clarification: True if confidence below threshold
        raw_response: Original LLM response for debugging
    """

    posture: str = POSTURE_RESPOND
    interpretation: str = ""
    intent_type: str = "UNCLEAR"
    actions: List[str] = field(default_factory=list)
    confidence: float = 0.0
    verification: Optional[VerificationTrace] = None
    extracted_entities: ExtractedEntities = field(default_factory=dict)
    canned_response: str = ""
    needs_clarification: bool = False
    raw_response: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
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
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any], raw_response: str = "") -> "RoutingResult":
        """Create from a dictionary.

        Accepts both freshly parsed LLM responses (where ``needs_clarification``
        is absent and InteractRouter will set it later based on confidence /
        verification) and round-tripped ``to_dict`` payloads from the routing
        cache (where ``needs_clarification`` was previously decided).

        ``actions`` may also be supplied under the alias ``skills`` to match the
        ``SkillRouter`` prompt terminology; ``actions`` wins when both are set.

        Args:
            data: Parsed JSON from LLM response or cached ``to_dict`` output
            raw_response: Original LLM response string for debugging

        Returns:
            RoutingResult instance
        """
        verification_data = data.get("verification")
        entities_data = data.get("extracted_entities", {})

        return cls(
            posture=cls._normalize_posture(data.get("posture", POSTURE_RESPOND)),
            interpretation=data.get("interpretation", ""),
            intent_type=cls._normalize_intent_type(data.get("intent_type", "UNCLEAR")),
            actions=cls._parse_actions(data.get("actions", data.get("skills", []))),
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
        """Create an error result when routing fails.

        Args:
            error_message: Description of what went wrong
            utterance: Original user utterance for fallback interpretation

        Returns:
            RoutingResult with UNCLEAR intent and zero confidence
        """
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
        """Normalize and validate posture.

        Args:
            posture_value: Raw posture from LLM response

        Returns:
            Validated posture string, defaults to POSTURE_RESPOND
        """
        if not posture_value:
            return POSTURE_RESPOND

        posture_str = str(posture_value).strip().upper()

        if posture_str in VALID_POSTURES:
            return posture_str

        logger.warning(f"Unrecognized posture '{posture_str}', defaulting to RESPOND")
        return POSTURE_RESPOND

    @staticmethod
    def _normalize_intent_type(intent_value: Any) -> str:
        """Normalize and validate intent type.

        Clean break - only accepts new declarative intent types.
        No backward compatibility with legacy types.

        Args:
            intent_value: Raw intent type from LLM response

        Returns:
            Validated intent type string, defaults to UNCLEAR
        """
        from jvagent.action.router.prompts import INTENT_TYPES

        if not intent_value:
            return "UNCLEAR"

        intent_str = str(intent_value).strip().upper()

        # Only accept the new declarative intent types
        if intent_str in INTENT_TYPES:
            return intent_str

        # No backward compatibility - return UNCLEAR for unrecognized types
        logger.warning(
            f"Unrecognized intent type '{intent_str}', defaulting to UNCLEAR"
        )
        return "UNCLEAR"

    @staticmethod
    def _parse_actions(actions_value: Any) -> List[str]:
        """Parse actions from various formats into a clean list of strings.

        Args:
            actions_value: Raw actions from LLM response

        Returns:
            List of action name strings
        """
        if not actions_value:
            return []

        if isinstance(actions_value, list):
            return [str(a).strip() for a in actions_value if a and str(a).strip()]

        if isinstance(actions_value, str):
            # Try to parse as JSON
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
        """Parse and clamp confidence value.

        Args:
            confidence_value: Raw confidence from LLM response

        Returns:
            Float between 0.0 and 1.0
        """
        if confidence_value is None:
            return 0.0

        try:
            confidence = float(confidence_value)
            return max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            return 0.0

    def is_respond(self) -> bool:
        """Check if posture is RESPOND."""
        return self.posture == POSTURE_RESPOND

    def is_suppress(self) -> bool:
        """Check if posture is SUPPRESS."""
        return self.posture == POSTURE_SUPPRESS

    def is_defer(self) -> bool:
        """Check if posture is DEFER."""
        return self.posture == POSTURE_DEFER

    def is_conversational(self) -> bool:
        """Check if this is a conversational intent."""
        return self.intent_type == "CONVERSATIONAL"

    def is_unclear(self) -> bool:
        """Check if intent is unclear."""
        return self.intent_type == "UNCLEAR"

    def should_clarify(self, threshold: float = 0.7) -> bool:
        """Check if clarification should be requested.

        Args:
            threshold: Confidence threshold below which to clarify

        Returns:
            True if confidence is below threshold or needs_clarification is set
        """
        return self.needs_clarification or self.confidence < threshold


def parse_routing_response(response: str) -> RoutingResult:
    """Parse LLM response string into RoutingResult.

    Handles JSON extraction from potentially wrapped responses
    and provides fallback for malformed responses.

    Args:
        response: Raw LLM response string

    Returns:
        RoutingResult instance
    """
    if not response:
        return RoutingResult.error_result("Empty response from LLM")

    # Try to extract JSON from the response
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
        # Find matching closing brace
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
        if result.intent_type == "CONVERSATIONAL" and result.actions:
            logger.debug(
                "RoutingResult: Enforcing CONVERSATIONAL intent rule - clearing actions"
            )
            result.actions = []

        return result

    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse routing response as JSON: {e}")
        logger.debug(f"Raw response: {response[:500]}")
        return RoutingResult.error_result(f"JSON parse error: {e}", response[:100])
