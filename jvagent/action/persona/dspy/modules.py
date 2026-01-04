"""DSPy modules for persona response generation.

This module provides DSPy Module classes that can be optimized using
DSPy's teleprompters and evaluators.
"""

import logging
from typing import Any, Dict, List, Optional

import dspy

from jvagent.action.persona.dspy.signatures import PersonaResponse

logger = logging.getLogger(__name__)


class PersonaResponseModule(dspy.Module):
    """DSPy module for generating persona responses with complete prompt element modeling.
    
    This module uses a DSPy Predict module with the PersonaResponse signature to generate
    responses. It can be optimized using DSPy's teleprompters (BootstrapFewShot, MIPROv2, etc.)
    to improve directive and parameter following consistency.
    
    Example:
        >>> module = PersonaResponseModule()
        >>> response = await module.aforward(
        ...     user_utterance="Hello",
        ...     persona_name="Assistant",
        ...     persona_description="You are helpful",
        ...     persona_capabilities=["Answer questions"],
        ...     user_display_name="user",
        ...     current_date="Monday, 15 January, 2024",
        ...     current_time="02:30 PM",
        ...     directives=[{"content": "Be friendly"}],
        ...     parameters=[],
        ... )
    """
    
    def __init__(self):
        """Initialize the module with a Predict module."""
        super().__init__()
        self.generate = dspy.Predict(PersonaResponse)
    
    async def aforward(
        self,
        user_utterance: str,
        persona_name: str,
        persona_description: str,
        persona_capabilities: List[str],
        user_display_name: str,
        current_date: str,
        current_time: str,
        directives: List[Dict[str, Any]],
        parameters: List[Dict[str, Any]],
        interpretation: Optional[str] = None,
        conversation_history: Optional[str] = None,
        is_continuation: bool = False,
        previous_response: Optional[str] = None,
        original_user_utterance: Optional[str] = None,
        channel: str = "default",
        channel_formatting: Optional[str] = None,
    ) -> str:
        """Generate response using DSPy with all prompt elements.
        
        Args:
            user_utterance: The user's current message
            persona_name: Agent display name
            persona_description: Agent description and personality
            persona_capabilities: List of agent capabilities
            user_display_name: How to refer to the user
            current_date: Current date (formatted)
            current_time: Current time (formatted)
            directives: List of directive dictionaries
            parameters: List of parameter dictionaries
            interpretation: Optional interpretation/insights
            conversation_history: Optional formatted conversation history
            is_continuation: Whether this is a continuation
            previous_response: Previous response if continuation
            original_user_utterance: Original utterance if continuation
            channel: Communication channel name
            channel_formatting: Channel-specific formatting instructions
            
        Returns:
            Generated response string
        """
        try:
            # Format capabilities
            capabilities_str = (
                "\n".join(f"- {cap}" for cap in persona_capabilities)
                if persona_capabilities
                else "None specified"
            )
            
            # Format directives
            directive_count = len(directives)
            if directives:
                directives_str = "\n".join(
                    f"{i+1}. {d.get('content', str(d))}"
                    for i, d in enumerate(directives)
                )
                directive_count_str = f"{directive_count} directive(s)"
            else:
                directives_str = "None"
                directive_count_str = "0 directive(s)"
            
            # Format parameters
            if parameters:
                from jvagent.action.persona.prompts import format_parameter
                parameters_str = "\n".join(
                    format_parameter(p, index=i+1)
                    for i, p in enumerate(parameters)
                )
            else:
                parameters_str = "None"
            
            # Format continuation fields
            is_continuation_str = "true" if is_continuation else "false"
            prev_response = previous_response or ""
            orig_utterance = original_user_utterance or ""
            
            # Format channel
            channel_formatting_str = channel_formatting or ""
            
            # Build kwargs for DSPy call
            classify_kwargs = {
                "user_utterance": user_utterance,
                "persona_name": persona_name,
                "persona_description": persona_description,
                "persona_capabilities": capabilities_str,
                "user_display_name": user_display_name,
                "current_date": current_date,
                "current_time": current_time,
                "directives": directives_str,
                "directive_count": directive_count_str,
                "parameters": parameters_str,
                "is_continuation": is_continuation_str,
                "channel": channel,
            }
            
            # Add optional fields only if they have values
            if interpretation:
                classify_kwargs["interpretation"] = interpretation
            if conversation_history:
                classify_kwargs["conversation_history"] = conversation_history
            if prev_response:
                classify_kwargs["previous_response"] = prev_response
            if orig_utterance:
                classify_kwargs["original_user_utterance"] = orig_utterance
            if channel_formatting_str:
                classify_kwargs["channel_formatting"] = channel_formatting_str
            
            # Call DSPy Predict module (use acall for async)
            prediction = await self.generate.acall(**classify_kwargs)
            
            return prediction.response
            
        except Exception as e:
            logger.error(
                f"PersonaResponseModule: Error during response generation: {e}",
                exc_info=True
            )
            raise

