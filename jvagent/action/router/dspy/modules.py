"""DSPy modules for router classification.

This module provides DSPy Module classes that can be optimized using
DSPy's teleprompters and evaluators.
"""

import json
import logging
from typing import Any, Dict, List, Optional

import dspy

from jvagent.action.router.dspy.signatures import RouterClassification

logger = logging.getLogger(__name__)


class RouterModule(dspy.Module):
    """DSPy module for routing user utterances to appropriate InteractActions.
    
    This module uses a DSPy Predict module with the RouterClassification
    signature to perform routing. It can be optimized using DSPy's
    teleprompters (BootstrapFewShot, MIPROv2, etc.) and evaluated with
    dspy.Evaluate.
    
    Example:
        >>> router = RouterModule()
        >>> result = await router.aforward(
        ...     user_utterance="What's the status of my order?",
        ...     available_actions='{"OrderAction": ["User asks about order status"]}',
        ...     conversation_history="User: I placed an order\nSystem: Order confirmed"
        ... )
        >>> print(result["interpretation"])  # "User requests order status"
        >>> print(result["actions"])  # ["OrderAction"]
    """
    
    def __init__(self):
        """Initialize the router module with a Predict module."""
        super().__init__()
        self.route = dspy.Predict(RouterClassification)
    
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
            
            # Call the DSPy Predict module
            prediction = self.route(**route_kwargs)
            
            # Extract interpretation
            interpretation = str(prediction.interpretation).strip() if prediction.interpretation else ""
            
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
            
            # Call the DSPy Predict module (use acall for async)
            prediction = await self.route.acall(**route_kwargs)
            
            # Process prediction same as sync version
            interpretation = str(prediction.interpretation).strip() if prediction.interpretation else ""
            
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

