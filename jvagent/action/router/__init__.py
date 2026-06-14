"""Router action package for intent-based routing with Chain of Verification.

This package provides the InteractRouter action which analyzes user utterances
and routes them to appropriate actions using direct LLM calls with Chain of
Verification (CoVe) prompting for improved accuracy.

Key components:
- InteractRouter: Main router action with CoVe-based routing
- RoutingResult: Structured output from routing with verification trace
- parse_routing_response: Helper to parse LLM responses into RoutingResult
"""

from .interact_router import InteractRouter
from .routing_result import (
    POSTURE_DEFER,
    POSTURE_RESPOND,
    POSTURE_SUPPRESS,
    ExtractedEntities,
    RoutingResult,
    VerificationTrace,
    parse_routing_response,
)

__all__ = [
    "ExtractedEntities",
    "InteractRouter",
    "POSTURE_DEFER",
    "POSTURE_RESPOND",
    "POSTURE_SUPPRESS",
    "RoutingResult",
    "VerificationTrace",
    "parse_routing_response",
]
