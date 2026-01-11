"""DSPy modules for persona response generation.

This module provides DSPy Module classes that can be optimized using
DSPy's teleprompters and evaluators.
"""

import logging
from typing import Any, Dict, List, Optional

import dspy

from jvagent.action.persona.dspy.signatures import create_persona_response_signature
from jvagent.action.persona.prompts import PERSONA_RESPONSE_SIGNATURE

logger = logging.getLogger(__name__)


class PersonaResponseModule(dspy.Module):
    """DSPy module for generating persona responses with complete prompt element modeling.
    
    This module uses a DSPy ChainOfThought module with the PersonaResponse signature to generate
    responses with step-by-step reasoning. It can be optimized using DSPy's teleprompters 
    (BootstrapFewShot, MIPROv2, etc.) to improve directive and parameter following consistency.
    
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
    
    def __init__(self, action_instance=None):
        """Initialize the module with a ChainOfThought module for better reasoning.
        
        Args:
            action_instance: Optional PersonaAction instance. If provided,
                uses the signature docstring from action_instance.persona_response_signature.
                If None, uses the default from prompts.py.
        """
        super().__init__()
        if action_instance and hasattr(action_instance, 'persona_response_signature'):
            docstring = action_instance.persona_response_signature
        else:
            docstring = PERSONA_RESPONSE_SIGNATURE
        signature_class = create_persona_response_signature(docstring)
        self.generate = dspy.ChainOfThought(signature_class)
    
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
                "is_continuation": is_continuation,  # Pass bool directly
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
            
            # Call DSPy ChainOfThought module (use acall for async)
            prediction = await self.generate.acall(**classify_kwargs)
            
            # ChainOfThought adds a 'reasoning' field along with 'response'
            # We need to extract only the 'response' field, not the reasoning
            # Handle different types: Prediction object, dict, or string
            if isinstance(prediction, str):
                # If prediction is already a string, use it directly
                response = prediction
            elif hasattr(prediction, 'response'):
                # Prediction object with response attribute
                response = prediction.response
            elif isinstance(prediction, dict) and 'response' in prediction:
                # Dictionary with response key
                response = prediction['response']
            elif hasattr(prediction, '__getitem__') and 'response' in prediction:
                # Object that supports dictionary-like access
                response = prediction['response']
            else:
                # Fallback: try to get response from prediction store
                response = None
                if hasattr(prediction, 'get'):
                    response = prediction.get('response', None)
                if response is None:
                    response = getattr(prediction, 'response', None)
                if response is None:
                    logger.warning(
                        f"PersonaResponseModule: Prediction does not contain 'response' field. "
                        f"Prediction type: {type(prediction)}, "
                        f"Available fields: {list(prediction.keys()) if hasattr(prediction, 'keys') else 'unknown'}"
                    )
                    # If no response field, use the prediction string representation
                    response = str(prediction)
            
            return response
            
        except Exception as e:
            logger.error(
                f"PersonaResponseModule: Error during response generation: {e}",
                exc_info=True
            )
            raise

