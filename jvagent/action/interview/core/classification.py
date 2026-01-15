"""Classification logic for interview action.

This module handles intent classification and field extraction from user input.
"""

import json
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .interview_session import InterviewSession
from .enums import InterviewState, Intent

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.interview.interview_interact_action import ClassificationResult, InterviewInteractAction
    from jvagent.memory import Interaction

logger = logging.getLogger(__name__)


class InterviewClassifier:
    """Handles classification and extraction for interview actions."""
    
    def __init__(self, action: "InterviewInteractAction"):
        """Initialize classifier with action instance.
        
        Args:
            action: InterviewInteractAction instance
        """
        self.action = action
    
    async def classify_and_extract(
        self,
        session: InterviewSession,
        utterance: str,
        interaction: "Interaction",
        visitor: "InteractWalker"
    ) -> "ClassificationResult":
        """Unified classification and extraction routine.

        Uses a single LLM call to detect intent (CANCELLATION, CONFIRMATION, UPDATE, SUBMISSION, NONE)
        and extract field values simultaneously for efficiency and consistency.

        Args:
            session: Interview session
            utterance: User's utterance (fallback if interpretation not available)
            interaction: Current interaction
            visitor: InteractWalker

        Returns:
            ClassificationResult with unified intent and extracted data
        """
        from jvagent.action.interview.interview_interact_action import ClassificationResult
        
        # Skip classification for terminal states
        if session.state == InterviewState.COMPLETED or session.state == InterviewState.CANCELLED:
            return ClassificationResult(intent=Intent.NONE)

        # Build user input - prioritize interpretation when available
        interpretation_available = interaction.interpretation and interaction.interpretation.strip()
        if interpretation_available:
            # Use interpretation as primary source, include utterance only for context if different
            user_input = interaction.interpretation
            if utterance and utterance.strip() and utterance.strip() != interaction.interpretation.strip():
                # Only include utterance if it adds context (is different from interpretation)
                user_input = f"Interpretation: {interaction.interpretation}\nUser's utterance: {utterance}"
        elif utterance and utterance.strip():
            user_input = utterance
        else:
            return ClassificationResult(intent=Intent.NONE)

        # Use DSPy if enabled, otherwise use legacy implementation
        if self.action.use_dspy:
            return await self._classify_with_dspy(session, user_input, interaction, visitor)

        # Unified classification and extraction using single prompt
        try:
            # Build context for unified prompt
            context = await self._build_classification_context(session)

            prompt = self.action.interview_prompt.format(
                user_input=user_input,
                current_state=context["current_state"],
                answered_fields=context["answered_fields"],
                entities_to_extract=context["entities_to_extract"],
                required_fields_info=context["required_fields_info"]
            )

            # Get model action
            model_action = await self.action.get_model_action(required=True)
            if not model_action:
                logger.warning(f"{self.action.get_class_name()}: Could not get model action for unified classification")
                return ClassificationResult(intent=Intent.NONE)

            # Get conversation history if needed
            conversation_history = None
            if self.action.use_history:
                conversation_history = await self.action._get_conversation_history(
                    interaction,
                    self.action.history_limit,
                    with_utterance=True,
                    with_response=True,
                    with_interpretation=False,
                    with_event=False,
                    max_statement_length=self.action.max_statement_length,
                )

            # Call LLM with unified prompt
            # Use interpretation as primary text when available (already in user_input)
            primary_text = interaction.interpretation if interpretation_available else utterance
            response = await model_action.generate(
                prompt=primary_text,
                stream=False,
                system=prompt,
                history=conversation_history,
                calling_action_name=self.action.get_class_name(),
                model=self.action.model,
                temperature=self.action.model_temperature,
                max_tokens=self.action.model_max_tokens,
                response_format={"type": "json_object"},
            )

            # Parse JSON response
            if isinstance(response, str):
                result = self.action._extract_json(response)
            else:
                result = response

            if not result:
                return ClassificationResult(intent=Intent.NONE)

            # Extract intent
            intent = result.get("intent", Intent.NONE).upper()
            confidence = result.get("confidence", 1.0)

            # Build ClassificationResult
            # Normalize field - handle string "null" from JSON
            field_value = result.get("field")
            if field_value and isinstance(field_value, str):
                field_str = field_value.strip().lower()
                if field_str == "null" or field_str == "none":
                    field_value = None

            classification_result = ClassificationResult(
                intent=intent,
                confidence=confidence,
                field=field_value,
                value=result.get("value")
            )

            # Handle SUBMISSION intent - extract field values
            if intent == Intent.SUBMISSION:
                # Extract field values (exclude intent-related keys)
                intent_keys = {"intent", "confidence", "field", "value"}
                extracted_data = {k: v for k, v in result.items() if k not in intent_keys}

                # Filter out empty/None/whitespace-only values
                filtered_data = {}
                for field, value in extracted_data.items():
                    if value is not None and isinstance(value, str) and value.strip():
                        filtered_data[field] = value
                    elif value is not None and not isinstance(value, str):
                        filtered_data[field] = value

                if filtered_data:
                    classification_result.extracted_data = filtered_data

            return classification_result

        except json.JSONDecodeError as e:
            logger.error(f"{self.action.get_class_name()}: Failed to parse unified classification JSON: {e}", exc_info=True)
            return ClassificationResult(intent=Intent.NONE)
        except Exception as e:
            logger.error(f"{self.action.get_class_name()}: Failed to classify/extract via unified prompt: {e}", exc_info=True)
            return ClassificationResult(intent=Intent.NONE)

    async def _build_classification_context(
        self,
        session: InterviewSession
    ) -> Dict[str, str]:
        """Build context for classification with all reachable unanswered questions.

        Args:
            session: Interview session

        Returns:
            Dictionary with current_state, answered_fields, entities_to_extract, required_fields_info
        """
        from .question_walker import QuestionWalker
        
        current_state = session.state.value

        # Format answered fields (minimal - just field names)
        answered_fields = session.get_answered_questions()
        answered_fields_str = ", ".join(answered_fields) if answered_fields else "None"

        # Get all reachable unanswered questions on the walk path
        question_walker = QuestionWalker()
        reachable_unanswered = await question_walker.get_reachable_unanswered_questions(
            session, self.action
        )
        
        # Build a map of question names to configs for quick lookup
        question_map = {q.get("name"): q for q in session.question_index if q.get("name")}
        
        # Filter to only include reachable unanswered questions
        active_questions = [
            question_map.get(name) for name in reachable_unanswered 
            if name in question_map
        ]
        # Filter out None values
        active_questions = [q for q in active_questions if q is not None]

        # Build entities list for extraction with required field information
        entities_list = []
        required_fields = set(session.get_required_questions())

        for item in active_questions:
            key = item.get('name')
            constraints = item.get('constraints', {})
            if not key or not constraints:
                continue
            desc = constraints.get('description', '')
            other_constraints = {k: v for k, v in constraints.items() if k != 'description'}
            constraint_strs = [f"{k}: {v}" for k, v in other_constraints.items()]
            constraint_part = f" ({', '.join(constraint_strs)})" if constraint_strs else ""
            is_required = key in required_fields
            required_marker = " [REQUIRED]" if is_required else " [OPTIONAL]"
            entities_list.append(f"- {key}: {desc}{constraint_part}{required_marker}")

        entities_to_extract = "\n".join(entities_list) if entities_list else "None (all questions answered)"

        # Build required fields info (simplified - comma-separated)
        required_fields_info = ", ".join(sorted(required_fields)) if required_fields else "None"

        return {
            "current_state": current_state,
            "answered_fields": answered_fields_str,
            "entities_to_extract": entities_to_extract,
            "required_fields_info": required_fields_info,
        }

    async def _classify_with_dspy(
        self,
        session: InterviewSession,
        user_input: str,
        interaction: "Interaction",
        visitor: "InteractWalker"
    ) -> "ClassificationResult":
        """DSPy-based classification and extraction routine.

        Uses DSPy modules with typed signatures for classification, enabling
        optimization via DSPy teleprompters (BootstrapFewShot, MIPROv2, etc.)
        and evaluation with dspy.Evaluate.

        Args:
            session: Interview session
            user_input: User's input (typically with reasoning)
            interaction: Current interaction
            visitor: InteractWalker

        Returns:
            ClassificationResult with unified intent and extracted data
        """
        from jvagent.action.interview.interview_interact_action import ClassificationResult
        
        try:
            # Import DSPy components
            import dspy
            from jvagent.action.model.dspy import DSPyLM
            from jvagent.action.interview.dspy import InterviewClassifier as DSPyInterviewClassifier

            # Build context for classification
            context = await self._build_classification_context(session)

            # Get conversation history if needed
            conversation_history = None
            formatted_history = None
            if self.action.use_history:
                conversation_history = await self.action._get_conversation_history(
                    interaction,
                    self.action.history_limit,
                    with_utterance=True,
                    with_response=True,
                    with_interpretation=False,
                    with_event=False,
                    max_statement_length=self.action.max_statement_length,
                )
                # Format history for DSPy signature
                from jvagent.action.model.dspy import format_conversation_history_for_dspy
                formatted_history = format_conversation_history_for_dspy(conversation_history)

            # Get model action
            model_action = await self.action.get_model_action(required=True)
            if not model_action:
                logger.warning(f"{self.action.get_class_name()}: Could not get model action for DSPy classification")
                return ClassificationResult(intent=Intent.NONE)

            # Create DSPy LM adapter
            lm = DSPyLM(
                model_action=model_action,
                model_type="chat",
                model=self.action.model,
                temperature=self.action.model_temperature,
                max_tokens=self.action.model_max_tokens,
            )

            # Configure DSPy with the adapter
            with dspy.context(lm=lm):
                # Create classifier instance with action instance for signature docstring
                classifier = DSPyInterviewClassifier(action_instance=self.action)

                # Build kwargs for classifier, include history if available
                classifier_kwargs = {
                    "user_input": user_input,
                    "current_state": context["current_state"],
                    "answered_fields": context["answered_fields"],
                    "entities_to_extract": context["entities_to_extract"],
                    "required_fields_info": context["required_fields_info"],
                }
                if formatted_history:
                    classifier_kwargs["conversation_history"] = formatted_history

                # Call classifier with async forward
                classification_result = await classifier.aforward(**classifier_kwargs)

                return classification_result

        except Exception as e:
            logger.error(
                f"{self.action.get_class_name()}: Failed to classify/extract via DSPy: {e}",
                exc_info=True
            )
            return ClassificationResult(intent="NONE")
