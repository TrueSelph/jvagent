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
    
    This module uses a DSPy ChainOfThought module with the RouterClassification
    signature to perform routing with concise reasoning. The LLM generates concise
    reasoning (under 50 words) that serves directly as the intent interpretation,
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
        >>> print(result["interpretation"])  # Concise reasoning used as interpretation
        >>> print(result["actions"])  # ["OrderAction"]
    """
    
    def __init__(self):
        """Initialize the router module with a ChainOfThought module.
        
        Uses a customized rationale field that encourages concise reasoning
        suitable for direct use as interpretation, eliminating the need for
        a separate interpretation field.
        """
        super().__init__()
        # Customize rationale field to produce concise reasoning (< 50 words)
        # that can be used directly as interpretation
        concise_rationale = dspy.OutputField(
            prefix="Brief analysis:",
            desc="Concise intent analysis in under 50 words. Capture what the user wants and relevant context (IDs, references, ongoing events). Example: 'User requests status update for ticket #789, mentions deadline'"
        )
        self.route = dspy.ChainOfThought(
            RouterClassification,
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
            reasoning = str(prediction.reasoning).strip() if hasattr(prediction, 'reasoning') and prediction.reasoning else ""
            if reasoning:
                logger.debug(f"RouterModule: Reasoning (used as interpretation): {reasoning[:200]}...")
            
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
            reasoning = str(prediction.reasoning).strip() if hasattr(prediction, 'reasoning') and prediction.reasoning else ""
            if reasoning:
                logger.debug(f"RouterModule: Reasoning (used as interpretation): {reasoning[:200]}...")
            
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

