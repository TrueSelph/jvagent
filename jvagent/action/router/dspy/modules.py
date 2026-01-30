"""DSPy modules for router classification.

This module provides DSPy Module classes that can be optimized using
DSPy's teleprompters and evaluators.
"""

import json
import logging
from typing import Any, Dict, List, Optional

import dspy

from jvagent.action.router.dspy.signatures import (
    create_router_classification_signature,
    INTENT_TYPES,
)
from jvagent.action.router.prompts import ROUTER_CLASSIFICATION_SIGNATURE

logger = logging.getLogger(__name__)


class RouterModule(dspy.Module):
    """DSPy module for intent-first routing of user utterances to InteractActions.

    This module uses a DSPy ChainOfThought module with the RouterClassification
    signature to:
    1. Classify intent type (REQUEST, QUERY, ANSWER, NAVIGATION, CONTINUATION, AMBIGUOUS)
    2. Apply routing logic based on intent type and ongoing activity context
    3. Return matched action names with confidence scores

    The module can be optimized using DSPy's teleprompters (BootstrapFewShot, MIPROv2, etc.).

    Example:
        >>> router = RouterModule()
        >>> result = await router.aforward(
        ...     user_utterance="I want to check the news",
        ...     available_actions='{"NewsAction": ["User wants news"]}',
        ...     conversation_history="[EVENT] Ongoing Activity: SignupInterviewInteractAction"
        ... )
        >>> print(result["intent_type"])  # "REQUEST"
        >>> print(result["actions"])  # ["NewsAction"] (not the ongoing activity)
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
        
        # Concise rationale for chain-of-thought reasoning
        concise_rationale = dspy.OutputField(
            prefix="Analysis:",
            desc="Brief analysis: 1) What is the user expressing/needing? 2) Is this engaging with an ongoing activity or something else? 3) Which actions match their actual need?"
        )
        
        self.route = dspy.ChainOfThought(
            signature_class,
            rationale_field=concise_rationale
        )

    def _parse_actions(self, actions_value: Any) -> List[str]:
        """Parse actions from various formats into a clean list of strings.
        
        Args:
            actions_value: The actions value from prediction (list, string, or other)
            
        Returns:
            List of action name strings
        """
        if not actions_value:
            return []
            
        if isinstance(actions_value, list):
            return [str(a).strip() for a in actions_value if a]
        
        if isinstance(actions_value, str):
            # Try to parse as JSON
            try:
                parsed = json.loads(actions_value)
                if isinstance(parsed, list):
                    return [str(a).strip() for a in parsed if a]
                else:
                    return [str(actions_value).strip()]
            except (json.JSONDecodeError, ValueError):
                return [str(actions_value).strip()]
        
        # Try to convert to list
        try:
            return [str(a).strip() for a in list(actions_value) if a]
        except (TypeError, ValueError):
            logger.warning(f"RouterModule: Could not convert actions to list: {actions_value}")
            return []

    def _parse_intent_type(self, intent_value: Any) -> str:
        """Parse and validate intent type.
        
        Args:
            intent_value: The intent_type value from prediction
            
        Returns:
            Validated intent type string, defaults to "UNCLEAR" if invalid
        """
        if not intent_value:
            return "UNCLEAR"
        
        intent_str = str(intent_value).strip().upper()
        
        # Handle common variations
        if intent_str in INTENT_TYPES:
            return intent_str
        
        # Map common alternatives
        alternatives = {
            # REQUEST variations
            "NEW_REQUEST": "REQUEST",
            "COMMAND": "REQUEST",
            "ACTION": "REQUEST",
            # QUERY variations
            "QUESTION": "QUERY",
            "ASK": "QUERY",
            # RESPONSE variations
            "ANSWER": "RESPONSE",
            "REPLY": "RESPONSE",
            "CONTINUATION": "RESPONSE",
            # SOCIAL variations
            "GREETING": "SOCIAL",
            "THANKS": "SOCIAL",
            "GRATITUDE": "SOCIAL",
            "ACKNOWLEDGMENT": "SOCIAL",
            "SMALLTALK": "SOCIAL",
            # NAVIGATION variations
            "CANCEL": "NAVIGATION",
            "TOPIC_CHANGE": "NAVIGATION",
            "STOP": "NAVIGATION",
            # UNCLEAR variations
            "AMBIGUOUS": "UNCLEAR",
            "UNKNOWN": "UNCLEAR",
        }
        
        return alternatives.get(intent_str, "UNCLEAR")

    def _parse_confidence(self, confidence_value: Any) -> float:
        """Parse and clamp confidence value.
        
        Args:
            confidence_value: The confidence value from prediction
            
        Returns:
            Float between 0.0 and 1.0
        """
        if confidence_value is None:
            return 1.0
        
        try:
            confidence = float(confidence_value)
            return max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            return 1.0

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
            Dictionary with keys: interpretation, actions (list), intent_type, confidence
        """
        try:
            route_kwargs = {
                "user_utterance": user_utterance,
                "available_actions": available_actions,
            }
            if conversation_history:
                route_kwargs["conversation_history"] = conversation_history

            prediction = self.route(**route_kwargs)

            # Extract reasoning as interpretation
            reasoning = ""
            if hasattr(prediction, 'reasoning') and prediction.reasoning:
                reasoning = str(prediction.reasoning).strip()
                logger.debug(f"RouterModule: Analysis: {reasoning[:200]}...")

            # Parse outputs
            actions = self._parse_actions(prediction.actions)
            actions = [a for a in actions if a]  # Filter empty strings
            
            intent_type = self._parse_intent_type(
                getattr(prediction, 'intent_type', None)
            )
            confidence = self._parse_confidence(prediction.confidence)

            return {
                "interpretation": reasoning,
                "actions": actions,
                "intent_type": intent_type,
                "confidence": confidence,
            }

        except Exception as e:
            logger.error(f"RouterModule: Error during routing: {e}", exc_info=True)
            return {
                "interpretation": f"User said: {user_utterance[:50]}",
                "actions": [],
                "intent_type": "UNCLEAR",
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
            Dictionary with keys: interpretation, actions (list), intent_type, confidence
        """
        try:
            route_kwargs = {
                "user_utterance": user_utterance,
                "available_actions": available_actions,
            }
            if conversation_history:
                route_kwargs["conversation_history"] = conversation_history

            prediction = await self.route.acall(**route_kwargs)

            # Extract reasoning as interpretation
            reasoning = ""
            if hasattr(prediction, 'reasoning') and prediction.reasoning:
                reasoning = str(prediction.reasoning).strip()
                logger.debug(f"RouterModule: Analysis: {reasoning[:200]}...")

            # Parse outputs
            actions = self._parse_actions(prediction.actions)
            actions = [a for a in actions if a]  # Filter empty strings
            
            intent_type = self._parse_intent_type(
                getattr(prediction, 'intent_type', None)
            )
            confidence = self._parse_confidence(prediction.confidence)

            return {
                "interpretation": reasoning,
                "actions": actions,
                "intent_type": intent_type,
                "confidence": confidence,
            }

        except Exception as e:
            logger.error(f"RouterModule: Error during async routing: {e}", exc_info=True)
            return {
                "interpretation": f"User said: {user_utterance[:50]}",
                "actions": [],
                "intent_type": "UNCLEAR",
                "confidence": 0.0,
            }
