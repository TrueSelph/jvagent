"""DSPy modules for interview classification and extraction.

This module provides DSPy Module classes that can be optimized using
DSPy's teleprompters and evaluators.
"""

import logging
from typing import Any, Dict, Optional

import dspy

from jvagent.action.interview.core.foundation.enums import Intent
from jvagent.action.interview.interview_interact_action import ClassificationResult
from jvagent.action.interview.dspy.signatures import create_interview_classification_signature
from jvagent.action.interview.core.foundation.prompts import INTERVIEW_CLASSIFICATION_SIGNATURE

logger = logging.getLogger(__name__)


class InterviewClassifier(dspy.Module):
    """DSPy module for classifying interview intents and extracting field values.
    
    This module uses a DSPy Predict module with the InterviewClassification
    signature to perform classification. It can be optimized using DSPy's
    teleprompters (BootstrapFewShot, MIPROv2, etc.) and evaluated with
    dspy.Evaluate.
    
    Example:
        >>> classifier = InterviewClassifier()
        >>> result = classifier(
        ...     user_input="My name is John Doe",
        ...     current_state="ACTIVE",
        ...     answered_fields="None",
        ...     entities_to_extract="- user_name: Full name"
        ... )
        >>> print(result.intent)  # "SUBMISSION"
        >>> print(result.extracted_data)  # {"user_name": "John Doe"}
    """
    
    def __init__(self, action_instance=None):
        """Initialize the classifier with a Predict module.
        
        Args:
            action_instance: Optional InterviewInteractAction instance. If provided,
                uses the signature docstring from action_instance.interview_classification_signature.
                If None, uses the default from prompts.py.
        """
        super().__init__()
        if action_instance and hasattr(action_instance, 'interview_classification_signature'):
            docstring = action_instance.interview_classification_signature
        else:
            docstring = INTERVIEW_CLASSIFICATION_SIGNATURE
        signature_class = create_interview_classification_signature(docstring)
        self.classify = dspy.Predict(signature_class)
    
    def forward(
        self,
        user_input: str,
        current_state: str,
        answered_fields: str,
        entities_to_extract: str,
        required_fields_info: str,
        conversation_history: Optional[str] = None,
    ) -> ClassificationResult:
        """Classify intent and extract field values.
        
        Args:
            user_input: User's input (typically with reasoning)
            current_state: Current interview state
            answered_fields: Previously answered fields with values
            entities_to_extract: Unanswered fields to extract
            required_fields_info: List of required field names
            conversation_history: Optional formatted conversation history for context
            
        Returns:
            ClassificationResult with intent, confidence, and extracted data
        """
        try:
            # Build kwargs for classification, include history if provided
            classify_kwargs = {
                "user_input": user_input,
                "current_state": current_state,
                "answered_fields": answered_fields,
                "entities_to_extract": entities_to_extract,
                "required_fields_info": required_fields_info,
            }
            if conversation_history:
                classify_kwargs["conversation_history"] = conversation_history
            
            # Call the DSPy Predict module
            prediction = self.classify(**classify_kwargs)
            
            # Extract intent and convert to Intent enum
            intent_str = str(prediction.intent).upper() if prediction.intent else Intent.NONE.value
            try:
                intent = Intent(intent_str)
            except ValueError:
                # Invalid intent value, default to NONE
                logger.warning(f"InterviewClassifier: Invalid intent value '{intent_str}', defaulting to NONE")
                intent = Intent.NONE
            
            # Extract confidence, defaulting to 1.0 if not provided
            confidence = float(prediction.confidence) if prediction.confidence is not None else 1.0
            
            # Extract field and value for UPDATE intent
            # Handle both None and string "null" from JSON parsing
            field = None
            if prediction.field:
                field_str = str(prediction.field).strip().lower()
                if field_str and field_str != "null" and field_str != "none":
                    field = str(prediction.field)
            value = prediction.value  # Can be any type
            
            # Extract extracted_data for SUBMISSION intent
            extracted_data = None
            if intent == Intent.SUBMISSION and prediction.extracted_data:
                # Ensure extracted_data is a dict
                if isinstance(prediction.extracted_data, dict):
                    extracted_data = prediction.extracted_data
                elif isinstance(prediction.extracted_data, str):
                    # Try to parse as JSON if it's a string
                    try:
                        import json
                        extracted_data = json.loads(prediction.extracted_data)
                    except (json.JSONDecodeError, ValueError):
                        logger.warning(
                            f"InterviewClassifier: Could not parse extracted_data as JSON: {prediction.extracted_data}"
                        )
                        extracted_data = None
                else:
                    # Try to convert to dict
                    try:
                        extracted_data = dict(prediction.extracted_data)
                    except (TypeError, ValueError):
                        logger.warning(
                            f"InterviewClassifier: Could not convert extracted_data to dict: {prediction.extracted_data}"
                        )
                        extracted_data = None
                
                # Filter out empty/None/whitespace-only values
                if extracted_data:
                    filtered_data = {}
                    for key, val in extracted_data.items():
                        if val is not None:
                            if isinstance(val, str) and val.strip():
                                filtered_data[key] = val
                            elif not isinstance(val, str):
                                filtered_data[key] = val
                    
                    extracted_data = filtered_data if filtered_data else None
            
            # Build and return ClassificationResult
            return ClassificationResult(
                intent=intent.value,  # Store as string value for ClassificationResult
                confidence=confidence,
                field=field,
                value=value,
                extracted_data=extracted_data
            )
            
        except Exception as e:
            logger.error(
                f"InterviewClassifier: Error during classification: {e}",
                exc_info=True
            )
            # Return default NONE result on error
            return ClassificationResult(intent=Intent.NONE, confidence=0.0)
    
    async def aforward(
        self,
        user_input: str,
        current_state: str,
        answered_fields: str,
        entities_to_extract: str,
        required_fields_info: str,
        conversation_history: Optional[str] = None,
    ) -> ClassificationResult:
        """Async version of forward.
        
        Args:
            user_input: User's input (typically with reasoning)
            current_state: Current interview state
            answered_fields: Previously answered fields with values
            entities_to_extract: Unanswered fields to extract
            required_fields_info: List of required field names
            conversation_history: Optional formatted conversation history for context
            
        Returns:
            ClassificationResult with intent, confidence, and extracted data
        """
        # Use DSPy's async support
        try:
            # Build kwargs for classification, include history if provided
            classify_kwargs = {
                "user_input": user_input,
                "current_state": current_state,
                "answered_fields": answered_fields,
                "entities_to_extract": entities_to_extract,
                "required_fields_info": required_fields_info,
            }
            if conversation_history:
                classify_kwargs["conversation_history"] = conversation_history
            
            prediction = await self.classify.acall(**classify_kwargs)
            
            # Process prediction same as sync version
            # Extract intent and convert to Intent enum
            intent_str = str(prediction.intent).upper() if prediction.intent else Intent.NONE.value
            try:
                intent = Intent(intent_str)
            except ValueError:
                # Invalid intent value, default to NONE
                logger.warning(f"InterviewClassifier: Invalid intent value '{intent_str}', defaulting to NONE")
                intent = Intent.NONE
            confidence = float(prediction.confidence) if prediction.confidence is not None else 1.0
            # Handle both None and string "null" from JSON parsing
            field = None
            if prediction.field:
                field_str = str(prediction.field).strip().lower()
                if field_str and field_str != "null" and field_str != "none":
                    field = str(prediction.field)
            value = prediction.value
            
            extracted_data = None
            if intent == Intent.SUBMISSION and prediction.extracted_data:
                if isinstance(prediction.extracted_data, dict):
                    extracted_data = prediction.extracted_data
                elif isinstance(prediction.extracted_data, str):
                    try:
                        import json
                        extracted_data = json.loads(prediction.extracted_data)
                    except (json.JSONDecodeError, ValueError):
                        logger.warning(
                            f"InterviewClassifier: Could not parse extracted_data as JSON: {prediction.extracted_data}"
                        )
                        extracted_data = None
                else:
                    try:
                        extracted_data = dict(prediction.extracted_data)
                    except (TypeError, ValueError):
                        logger.warning(
                            f"InterviewClassifier: Could not convert extracted_data to dict: {prediction.extracted_data}"
                        )
                        extracted_data = None
                
                if extracted_data:
                    filtered_data = {}
                    for key, val in extracted_data.items():
                        if val is not None:
                            if isinstance(val, str) and val.strip():
                                filtered_data[key] = val
                            elif not isinstance(val, str):
                                filtered_data[key] = val
                    
                    extracted_data = filtered_data if filtered_data else None
            
            return ClassificationResult(
                intent=intent.value,  # Store as string value for ClassificationResult
                confidence=confidence,
                field=field,
                value=value,
                extracted_data=extracted_data
            )
            
        except Exception as e:
            logger.error(
                f"InterviewClassifier: Error during async classification: {e}",
                exc_info=True
            )
            return ClassificationResult(intent=Intent.NONE, confidence=0.0)

