"""DSPy signatures for persona response generation.

This module defines typed DSPy signatures that model all elements of the persona prompt,
enabling DSPy to optimize directive and parameter following.
"""

import logging
from typing import Optional, Type

logger = logging.getLogger(__name__)


def create_persona_response_signature(docstring: str) -> Type:
    """Factory function to create PersonaResponse signature with custom docstring.
    
    Args:
        docstring: The docstring to use for the signature class
        
    Returns:
        A dynamically created signature class with the provided docstring
    """
    try:
        import dspy
    except Exception as e:
        logger.error(f"Failed to import dspy in create_persona_response_signature: {e}")
        raise
    
    try:
        class PersonaResponse(dspy.Signature):
            __doc__ = docstring
        
            # Core inputs - Agent Identity
            user_utterance: str = dspy.InputField(desc="The user's current message")
            persona_name: str = dspy.InputField(desc="Agent display name")
            persona_description: str = dspy.InputField(desc="Agent description and personality")
            persona_capabilities: str = dspy.InputField(desc="List of agent capabilities (one per line, or 'None specified')")
            user_display_name: str = dspy.InputField(desc="How to refer to the user")
            current_date: str = dspy.InputField(desc="Current date (e.g., 'Monday, 15 January, 2024')")
            current_time: str = dspy.InputField(desc="Current time (e.g., '02:30 PM')")
            
            # Directives and parameters
            directives: str = dspy.InputField(desc="List of directives that MUST be followed (numbered format, or 'None' if no directives). Execute all directives naturally within your persona.")
            directive_count: str = dspy.InputField(desc="Number of directives to execute (e.g., '3 directive(s)' or '0 directive(s)')")
            parameters: str = dspy.InputField(desc="List of conditional parameters (condition -> response format, or 'None' if no parameters). Apply when conditions match.")
            
            # Optional context
            interpretation: Optional[str] = dspy.InputField(desc="Optional interpretation/insights about user intent (or empty if none). Use for context only; directives have absolute priority.")
            conversation_history: Optional[str] = dspy.InputField(desc="Formatted conversation history (or empty if none). Check history to ensure response differs from previous messages.")
            
            # Continuation mode (conditional)
            is_continuation: bool = dspy.InputField(desc="Whether this is a continuation of a previous response")
            previous_response: Optional[str] = dspy.InputField(desc="Previous response text if continuation (truncated to last 2000 chars, or empty if not continuation)")
            original_user_utterance: Optional[str] = dspy.InputField(desc="Original user utterance if continuation (truncated to 500 chars, or empty if not continuation)")
            
            # Channel formatting (conditional)
            channel: str = dspy.InputField(desc="Communication channel name (e.g., 'web', 'email', 'sms', 'default')")
            channel_formatting: Optional[str] = dspy.InputField(desc="Channel-specific formatting instructions (or empty if none). Match channel-appropriate tone and formatting.")
            
            # Output
            response: str = dspy.OutputField(
                desc="Response that faithfully incorporates all applicable directives and parameters. Before finalizing, verify: all directives executed naturally within persona, all applicable parameters applied, no repetition of previous messages, response grounded in provided information (no hallucinations), natural conversational tone maintained, end cleanly without unnecessary closings unless conversation is finished."
            )
        
        return PersonaResponse
    except Exception as e:
        logger.error(f"Failed to create PersonaResponse signature: {e}", exc_info=True)
        raise

