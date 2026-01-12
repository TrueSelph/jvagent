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
            desc="JSON-formatted dictionary where KEYS are action names (e.g., 'SignupInterviewInteractAction') and VALUES are lists of anchor statements (e.g., ['User wants to sign up', 'User cancels SignupInterviewInteractAction']). The keys (action names) are what must be returned in the actions output, NOT the anchor statements. Example format: {\"ActionName1\": [\"anchor1\", \"anchor2\"], \"ActionName2\": [\"anchor3\"]}"
        )
        conversation_history: Optional[str] = dspy.InputField(
            desc="Formatted conversation history from previous interactions. Includes user utterances, AI responses, interpretations, and events. Use this to understand context, ongoing topics, prior questions, and system events. Format: chronological list with User/System messages, [INTERPRETATION] prefixes for routing context, and [EVENT] prefixes for system events. CRITICAL: Always check [EVENT] messages for ongoing activities (e.g., '[EVENT] Ongoing Activity: interviewing user as part of {ActionName}'). Actions with ongoing activities in recent events should be prioritized for routing, especially when the current utterance is ambiguous (e.g., 'nope', 'yes', 'ok'). If an action is mentioned in [EVENT] messages as an ongoing activity, route to it even if the utterance doesn't clearly match anchors."
        )
        
        # Output fields - routing result
        # Note: interpretation is provided by ChainOfThought's reasoning field (labeled as 'interpretation' for consistency)
        actions: List[str] = dspy.OutputField(
            desc="List of action names (dictionary KEYS from available_actions) that should handle this request. CRITICAL: Return ONLY the action names (keys), NEVER the anchor statements (values). Each action name must exactly match a key from the available_actions JSON object. CORRECT: [\"SignupInterviewInteractAction\"]. INCORRECT: [\"User cancels SignupInterviewInteractAction\"]. Return empty list [] if no match. Multiple actions allowed."
        )
        confidence: float = dspy.OutputField(
            desc="Confidence score between 0.0 and 1.0 for the routing decision"
        )
    
    return RouterClassification

