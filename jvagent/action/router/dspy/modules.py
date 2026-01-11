"""DSPy modules for router classification.

This module provides DSPy Module classes that can be optimized using
DSPy's teleprompters and evaluators.
"""

import json
import logging
from typing import Any, Dict, List, Optional

import dspy

from jvagent.action.router.dspy.signatures import create_router_classification_signature
from jvagent.action.router.prompts import ROUTER_CLASSIFICATION_SIGNATURE

logger = logging.getLogger(__name__)


class RouterModule(dspy.Module):
    """DSPy module for routing user utterances to appropriate InteractActions.
    
    This module uses a DSPy ChainOfThought module with the RouterClassification
    signature to perform routing with concise interpretation. The LLM generates concise
    interpretation (under 80 words) that serves directly as the intent interpretation,
    eliminating the need for a separate interpretation field. This optimization
    reduces latency and token usage.
    
    The module can be optimized using DSPy's teleprompters (BootstrapFewShot, MIPROv2, etc.)
    and evaluated with dspy.Evaluate.
    
    Example:
        >>> router = RouterModule()
        >>> result = await router.aforward(
        ...     user_utterance="What's the status of my order?",
        ...     available_actions='{"OrderAction": ["User asks about order status"]}',
        ...     conversation_history="User: I placed an order\nSystem: Order confirmed"
        ... )
        >>> print(result["interpretation"])  # Concise interpretation
        >>> print(result["actions"])  # ["OrderAction"]
    """
    
    def __init__(self, action_instance=None):
        """Initialize the router module with a ChainOfThought module.
        
        Args:
            action_instance: Optional InteractRouter instance. If provided,
                uses the signature docstring from action_instance.router_classification_signature.
                If None, uses the default from prompts.py.
        """
        super().__init__()
        if action_instance and hasattr(action_instance, 'router_classification_signature'):
            docstring = action_instance.router_classification_signature
        else:
            docstring = ROUTER_CLASSIFICATION_SIGNATURE
        signature_class = create_router_classification_signature(docstring)
        # Customize rationale field to produce concise interpretation (< 50 words)
        # that can be used directly as interpretation
        concise_rationale = dspy.OutputField(
            prefix="Brief analysis:",
            desc=(
                "Concise, shorthanded intent analysis in under 80 words. Capture what the user wants and relevant context. "
                "CRITICAL: Always extract and include specific information from the current utterance and conversation history. "
                "Extract concrete values: names, emails, IDs, ticket numbers, dates, amounts, and other specific data. "
                "Scan both the current utterance AND conversation history for pertinent details. "
                "The interpretation must be rich enough for downstream actions to extract information without re-parsing the raw utterance. "
                "Examples: 'User provides name \"John Doe\" and email \"john@example.com\" for signup', "
                "'User requests status for ticket #789, deadline Friday', "
                "'User confirms order #12345 for $99.99'"
            )
        )
        self.route = dspy.ChainOfThought(
            signature_class,
            rationale_field=concise_rationale
        )
    
    def forward(
        self,
        user_utterance: str,
        available_actions: str,
        conversation_history: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Route utterance to appropriate actions (synchronous).
        
        Args:
            user_utterance: The user's current message
            available_actions: JSON-formatted string of actions and their anchors
            conversation_history: Optional formatted conversation history
            
        Returns:
            Dictionary with keys: interpretation, actions (list), confidence
        """
        try:
            # Build kwargs for routing, only include history if provided
            route_kwargs = {
                "user_utterance": user_utterance,
                "available_actions": available_actions,
            }
            if conversation_history:
                route_kwargs["conversation_history"] = conversation_history
            
            # Call the DSPy ChainOfThought module
            prediction = self.route(**route_kwargs)
            
            # ChainOfThought adds a 'reasoning' field along with original outputs
            # Use reasoning directly as interpretation (optimization: eliminates separate interpretation generation)
            # Note: Internally DSPy uses 'reasoning', but we label it as 'interpretation' for consistency
            reasoning = str(prediction.reasoning).strip() if hasattr(prediction, 'reasoning') and prediction.reasoning else ""
            if reasoning:
                logger.debug(f"RouterModule: Interpretation: {reasoning[:200]}...")
            
            # Use reasoning as interpretation (fallback to empty string if not available)
            interpretation = reasoning
            
            # Extract actions - handle both list and string formats
            actions = []
            if prediction.actions:
                if isinstance(prediction.actions, list):
                    actions = [str(a).strip() for a in prediction.actions if a]
                elif isinstance(prediction.actions, str):
                    # Try to parse as JSON if it's a string
                    try:
                        parsed = json.loads(prediction.actions)
                        if isinstance(parsed, list):
                            actions = [str(a).strip() for a in parsed if a]
                        else:
                            # Single action name
                            actions = [str(prediction.actions).strip()]
                    except (json.JSONDecodeError, ValueError):
                        # Treat as single action name
                        actions = [str(prediction.actions).strip()]
                else:
                    # Try to convert to list
                    try:
                        actions = [str(a).strip() for a in list(prediction.actions) if a]
                    except (TypeError, ValueError):
                        logger.warning(
                            f"RouterModule: Could not convert actions to list: {prediction.actions}"
                        )
                        actions = []
            
            # Filter out empty strings
            actions = [a for a in actions if a]
            
            # Extract confidence, defaulting to 1.0 if not provided
            confidence = float(prediction.confidence) if prediction.confidence is not None else 1.0
            # Clamp confidence to [0.0, 1.0]
            confidence = max(0.0, min(1.0, confidence))
            
            return {
                "interpretation": interpretation,
                "actions": actions,
                "confidence": confidence,
            }
            
        except Exception as e:
            logger.error(
                f"RouterModule: Error during routing: {e}",
                exc_info=True
            )
            # Return safe default on error
            return {
                "interpretation": f"User said: {user_utterance[:50]}",
                "actions": [],
                "confidence": 0.0,
            }
    
    async def aforward(
        self,
        user_utterance: str,
        available_actions: str,
        conversation_history: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Route utterance to appropriate actions (asynchronous).
        
        Args:
            user_utterance: The user's current message
            available_actions: JSON-formatted string of actions and their anchors
            conversation_history: Optional formatted conversation history
            
        Returns:
            Dictionary with keys: interpretation, actions (list), confidence
        """
        try:
            # Build kwargs for routing, only include history if provided
            route_kwargs = {
                "user_utterance": user_utterance,
                "available_actions": available_actions,
            }
            if conversation_history:
                route_kwargs["conversation_history"] = conversation_history
            
            # Call the DSPy ChainOfThought module (use acall for async)
            prediction = await self.route.acall(**route_kwargs)
            
            # ChainOfThought adds a 'reasoning' field along with original outputs
            # Use reasoning directly as interpretation (optimization: eliminates separate interpretation generation)
            # Note: Internally DSPy uses 'reasoning', but we label it as 'interpretation' for consistency
            reasoning = str(prediction.reasoning).strip() if hasattr(prediction, 'reasoning') and prediction.reasoning else ""
            if reasoning:
                logger.debug(f"RouterModule: Interpretation: {reasoning[:200]}...")
            
            # Use reasoning as interpretation (fallback to empty string if not available)
            interpretation = reasoning
            
            actions = []
            if prediction.actions:
                if isinstance(prediction.actions, list):
                    actions = [str(a).strip() for a in prediction.actions if a]
                elif isinstance(prediction.actions, str):
                    try:
                        parsed = json.loads(prediction.actions)
                        if isinstance(parsed, list):
                            actions = [str(a).strip() for a in parsed if a]
                        else:
                            actions = [str(prediction.actions).strip()]
                    except (json.JSONDecodeError, ValueError):
                        actions = [str(prediction.actions).strip()]
                else:
                    try:
                        actions = [str(a).strip() for a in list(prediction.actions) if a]
                    except (TypeError, ValueError):
                        logger.warning(
                            f"RouterModule: Could not convert actions to list: {prediction.actions}"
                        )
                        actions = []
            
            actions = [a for a in actions if a]
            
            confidence = float(prediction.confidence) if prediction.confidence is not None else 1.0
            confidence = max(0.0, min(1.0, confidence))
            
            return {
                "interpretation": interpretation,
                "actions": actions,
                "confidence": confidence,
            }
            
        except Exception as e:
            logger.error(
                f"RouterModule: Error during async routing: {e}",
                exc_info=True
            )
            return {
                "interpretation": f"User said: {user_utterance[:50]}",
                "actions": [],
                "confidence": 0.0,
            }

