"""DSPy signatures for router classification.

This module defines typed DSPy signatures that model routing decisions,
enabling DSPy to optimize routing accuracy and consistency.
"""

from typing import List, Optional, Type

import dspy


def create_router_classification_signature(docstring: str) -> Type[dspy.Signature]:
    """Factory function to create RouterClassification signature with custom docstring.
    
    Args:
        docstring: The docstring to use for the signature class
        
    Returns:
        A dynamically created signature class with the provided docstring
    """
    class RouterClassification(dspy.Signature):
        __doc__ = docstring
        
        # Input fields - context for routing
        user_utterance: str = dspy.InputField(
            desc="The user's current message/utterance to route"
        )
        available_actions: str = dspy.InputField(
            desc="JSON-formatted dictionary of available actions and their anchor statements. Each action has a list of anchors describing when it should be used."
        )
        conversation_history: Optional[str] = dspy.InputField(
            desc="Formatted conversation history from previous interactions. Includes user utterances, AI responses, interpretations, and events. Use this to understand context, ongoing topics, prior questions, and system events. Format: chronological list with User/System messages, [INTERPRETATION] prefixes for routing context, and [EVENT] prefixes for system events."
        )
        
        # Output fields - routing result
        # Note: interpretation is provided by ChainOfThought's reasoning field (labeled as 'interpretation' for consistency)
        actions: List[str] = dspy.OutputField(
            desc="List of action names that should handle this request. Use exact action names from available_actions. Return empty list [] if no match. Multiple actions allowed."
        )
        confidence: float = dspy.OutputField(
            desc="Confidence score between 0.0 and 1.0 for the routing decision"
        )
    
    return RouterClassification

