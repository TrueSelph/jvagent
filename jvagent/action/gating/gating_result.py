"""GatingResult dataclass for response posture classification.

This module provides the GatingResult dataclass that encapsulates
the output of ResponseGatingAction's posture classification.
"""

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

POSTURE_RESPOND = "RESPOND"
POSTURE_SUPPRESS = "SUPPRESS"
POSTURE_DEFER = "DEFER"
VALID_POSTURES = (POSTURE_RESPOND, POSTURE_SUPPRESS, POSTURE_DEFER)


@dataclass
class GatingResult:
    """Structured result from ResponseGatingAction posture classification.

    Attributes:
        posture: RESPOND | SUPPRESS | DEFER
        confidence: Confidence score (0.0-1.0)
        reasoning: Brief explanation for the classification
    """
    posture: str = POSTURE_RESPOND
    confidence: float = 0.0
    reasoning: str = ""

    def is_respond(self) -> bool:
        return self.posture == POSTURE_RESPOND

    def is_suppress(self) -> bool:
        return self.posture == POSTURE_SUPPRESS

    def is_defer(self) -> bool:
        return self.posture == POSTURE_DEFER


def parse_gating_response(response: str) -> GatingResult:
    """Parse LLM response string into GatingResult.

    Handles JSON extraction from potentially wrapped responses
    and provides fallback to RESPOND for malformed responses.

    Args:
        response: Raw LLM response string

    Returns:
        GatingResult instance
    """
    if not response:
        return GatingResult(posture=POSTURE_RESPOND, confidence=0.0, reasoning="Empty response")

    json_str = response.strip()

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
        posture = str(data.get("posture", POSTURE_RESPOND)).strip().upper()
        if posture not in VALID_POSTURES:
            posture = POSTURE_RESPOND
        confidence = float(data.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))
        reasoning = str(data.get("reasoning", "")).strip()
        return GatingResult(posture=posture, confidence=confidence, reasoning=reasoning)
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        logger.warning(f"Failed to parse gating response as JSON: {e}")
        return GatingResult(posture=POSTURE_RESPOND, confidence=0.0, reasoning=str(e))
