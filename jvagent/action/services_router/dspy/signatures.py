"""DSPy signatures for router classification.

This module defines typed DSPy signatures that model routing decisions,
enabling DSPy to optimize routing accuracy and consistency.
"""

from typing import List, Optional

import dspy


class RouterClassification(dspy.Signature):
    """Classify user utterance intent and route to appropriate InteractActions.
    
    Analyze the user's utterance and conversation history to determine intent,
    then match against available action anchors to identify which actions should
    handle this request.
    
    ROUTING RULES:
    - Match when utterance intent aligns with anchor descriptions
    - If multiple actions match, prefer more specific anchors over general ones
    - When uncertain, include all reasonable matches (multi-action responses are allowed)
    - If no clear match, return empty actions array
    - Consider conversation history for context (ongoing topics, prior questions, references)
    - Be precise but inclusive - missing a relevant action is worse than including an extra one
    
    INTERPRETATION GUIDELINES:
    - Keep interpretation under 50 words
    - Capture what the user wants (information request, providing data, or both)
    - Include relevant context (IDs, references to prior conversation, ongoing events, user-provided details)
    - Example format: "User requests status update for ticket #789, mentions deadline"
    
    MATCHING GUIDELINES:
    - An action matches if its anchors align with the interpretation and describe handling this type of request
    - Prefer actions with more specific/detailed anchor matches
    - Include all actions that reasonably match (it's ok to route to multiple actions)
    - Consider conversation history and events - is this continuing a prior topic or answering a previous question?
    - Use exact action names from the available_actions input
    """
    
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
    interpretation: str = dspy.OutputField(
        desc="Intent interpretation in under 50 words. Capture what the user wants and relevant context (IDs, references, ongoing events). Example: 'User requests status update for ticket #789, mentions deadline'"
    )
    actions: List[str] = dspy.OutputField(
        desc="List of action names that should handle this request. Use exact action names from available_actions. Return empty list [] if no match. Multiple actions allowed."
    )
    confidence: float = dspy.OutputField(
        desc="Confidence score between 0.0 and 1.0 for the routing decision"
    )

