"""Response builder for interact endpoint with production filtering."""

from typing import Any, Dict, Optional

from jvagent.utils.env import is_production_mode
from jvagent.memory.interaction import Interaction


def build_interaction_payload(interaction: Interaction) -> Dict[str, Any]:
    """Build interaction payload, filtering debug data in production.
    
    In production mode, returns minimal payload with only:
    - id, utterance, response
    
    In development mode, returns full payload with:
    - id, utterance, response, actions, directives, parameters, 
      events, observability_metrics, streamed
    
    Args:
        interaction: Interaction node instance
        
    Returns:
        Dictionary with interaction data (filtered based on environment)
    """
    if is_production_mode():
        # Minimal production payload - only essential fields
        return {
            "id": interaction.id,
            "utterance": interaction.utterance,
            "response": interaction.response,
        }
    else:
        # Full development payload - includes all debug/observability data
        return {
            "id": interaction.id,
            "utterance": interaction.utterance,
            "response": interaction.response,
            "actions": interaction.actions,
            "directives": interaction.directives,
            "parameters": interaction.parameters,
            "events": interaction.events,
            "observability_metrics": interaction.observability_metrics,
            "streamed": interaction.streamed,
        }


def build_interact_response(
    user_id: str,
    session_id: str,
    interaction: Interaction,
    report: Optional[list] = None,
) -> Dict[str, Any]:
    """Build complete interact endpoint response with environment-based filtering.
    
    Always includes:
    - user_id, session_id, response, interaction (filtered)
    
    Only in development mode:
    - report (walker traversal report)
    
    Args:
        user_id: User identifier
        session_id: Session identifier
        interaction: Interaction node instance
        report: Optional walker report (only included in development)
        
    Returns:
        Dictionary with complete response payload
    """
    response: Dict[str, Any] = {
        "user_id": user_id,
        "session_id": session_id,
        "response": interaction.response,
        "interaction": build_interaction_payload(interaction),
    }
    
    # Include report only in development mode
    # In production mode, omit the field entirely (not set to None)
    if not is_production_mode() and report is not None:
        response["report"] = report
    
    return response

