"""DSPy signatures for router classification.

This module defines typed DSPy signatures that model routing decisions,
enabling DSPy to optimize routing accuracy and consistency.
"""

import logging
from typing import List, Optional, Type

logger = logging.getLogger(__name__)

# Valid intent types for routing
INTENT_TYPES = ["REQUEST", "QUERY", "RESPONSE", "SOCIAL", "NAVIGATION", "UNCLEAR"]


def create_router_classification_signature(docstring: str) -> Type:
    """Factory function to create RouterClassification signature with custom docstring.
    
    Args:
        docstring: The docstring to use for the signature class
        
    Returns:
        A dynamically created signature class with the provided docstring
    """
    try:
        import dspy
    except Exception as e:
        logger.error(f"Failed to import dspy in create_router_classification_signature: {e}")
        raise
    
    try:
        class RouterClassification(dspy.Signature):
            __doc__ = docstring
            
            # Input fields
            user_utterance: str = dspy.InputField(
                desc="The user's current message to classify and route"
            )
            available_actions: str = dspy.InputField(
                desc="JSON dictionary mapping action names (keys) to anchor statements (values). Return ONLY the keys in the actions output."
            )
            conversation_history: Optional[str] = dspy.InputField(
                desc="Conversation history with user messages, AI responses, and system [EVENT] messages. Check for '[EVENT] Ongoing Activity:' to detect active processes."
            )
            
            # Output fields
            actions: List[str] = dspy.OutputField(
                desc="List of action names (dictionary KEYS from available_actions) to route to. Return [] if no match or ambiguous without ongoing activity."
            )
            intent_type: str = dspy.OutputField(
                desc="What the user is expressing: REQUEST (wants system to do something), QUERY (asking a question), RESPONSE (directly answering assistant's question), SOCIAL (gratitude/greeting/smalltalk), NAVIGATION (topic change/cancel), or UNCLEAR."
            )
            confidence: float = dspy.OutputField(
                desc="Confidence score between 0.0 and 1.0 for the routing decision"
            )
        
        return RouterClassification
    except Exception as e:
        logger.error(f"Failed to create RouterClassification signature: {e}", exc_info=True)
        raise
