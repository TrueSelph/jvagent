"""Classification handler for interview action.

Extracted classification logic from interview_interact_action.py for better
separation of concerns and maintainability.
"""

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional, Set, Tuple

from ..foundation.enums import Intent, InterviewState
from ..session.interview_session import InterviewSession

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.memory import Interaction
    from jvagent.action.interview.interview_interact_action import InterviewInteractAction

logger = logging.getLogger(__name__)


@dataclass
class ClassificationResult:
    """Result of unified classification and extraction routine.

    Uses unified intent types: CANCELLATION, CONFIRMATION, UPDATE, DECLINE, SUBMISSION, NONE
    """
    intent: str  # "CANCELLATION", "CONFIRMATION", "UPDATE", "DECLINE", "SUBMISSION", "NONE"
    confidence: float = 1.0  # Confidence score for the classification

    # Unified field/value structure (used for UPDATE, DECLINE, and SUBMISSION)
    field: Optional[str] = None  # Field name (for UPDATE/DECLINE intent) or null
    value: Optional[Any] = None  # Field value (for UPDATE intent) or null

    # For SUBMISSION intent - extracted field values (multiple fields)
    extracted_data: Optional[Dict[str, Any]] = None  # Extracted responses for "SUBMISSION" intent


class ClassificationHandler:
    """Handles classification and extraction logic for interview actions."""
    
    def __init__(self, action: "InterviewInteractAction"):
        """Initialize classification handler with action instance.
        
        Args:
            action: InterviewInteractAction instance
        """
        self.action = action
    
    def extract_data_input_values(
        self,
        session: InterviewSession,
        visitor: "InteractWalker"
    ) -> Tuple[Dict[str, Any], Set[str]]:
        """Extract values from visitor.data for fields with data_input_field configured.
        
        Scans question_graph for fields with data_input_field in constraints and checks
        visitor.data dictionary for matching keys. Returns both the extracted values
        and the set of field names that have data_input_field (for exclusion from LLM).
        
        Args:
            session: Interview session
            visitor: InteractWalker with data property
            
        Returns:
            Tuple of (extracted_values_dict, excluded_field_names_set):
            - extracted_values_dict: Maps question names to values found in visitor.data
            - excluded_field_names_set: Set of question names that have data_input_field
        """
        extracted_values = {}
        excluded_fields = set()
        
        # Get question graph from action
        question_graph = self.action._get_question_graph()
        
        # Check visitor.data exists and is a dict
        if not hasattr(visitor, 'data') or not isinstance(visitor.data, dict):
            return extracted_values, excluded_fields
        
        # Scan question graph for data_input_field entries
        for question_config in question_graph:
            question_name = question_config.get("name")
            if not question_name:
                continue
            
            constraints = question_config.get("constraints", {})
            data_input_field = constraints.get("data_input_field")
            
            if data_input_field:
                # This field should be excluded from LLM extraction
                excluded_fields.add(question_name)
                
                # Check if the data_input_field key exists in visitor.data
                if data_input_field in visitor.data:
                    value = visitor.data[data_input_field]
                    # Only include if value is not None
                    if value is not None:
                        extracted_values[question_name] = value
        
        return extracted_values, excluded_fields
    
    async def _get_context_data_note(
        self,
        question_config: Dict[str, Any],
        session: InterviewSession
    ) -> str:
        """Get a note about context data for inclusion in entities_to_extract.
        
        This provides the LLM with information about available options or context
        when extracting values, improving extraction accuracy.
        
        Args:
            question_config: Question configuration dictionary
            session: Interview session
            
        Returns:
            String note about context data, or empty string if none available
        """
        # Check for static input_context
        context_data = question_config.get("input_context", {})
        
        # Check for dynamic input_context_provider
        provider_name = question_config.get("input_context_provider")
        if provider_name and session:
            try:
                from ..foundation.decorators import get_input_context_provider
                func = get_input_context_provider(session.interview_type, provider_name)
                if func:
                    # Execute provider to get dynamic context (Note: visitor not available here, pass None)
                    import inspect
                    if inspect.iscoroutinefunction(func):
                        dynamic_context = await func(session, None)
                    else:
                        dynamic_context = func(session, None)
                    
                    if dynamic_context and isinstance(dynamic_context, dict):
                        # Merge with static context (dynamic takes precedence)
                        context_data = {**context_data, **dynamic_context}
            except Exception as e:
                logger.debug(f"Could not fetch context data from provider '{provider_name}': {e}")
        
        if not context_data:
            return ""
        
        # Format context data for LLM - focus on lists of options
        # Use configurable threshold for compact display
        classification_config = self.action.config.classification
        compact_threshold = classification_config.context_list_compact_threshold
        options_text = classification_config.context_options_text
        
        context_notes = []
        for key, value in context_data.items():
            if isinstance(value, list) and value:
                # Format lists compactly
                if len(value) <= compact_threshold:
                    items_str = ", ".join(str(v) for v in value)
                    context_notes.append(f"{key.replace('_', ' ')}: {items_str}")
                else:
                    # For long lists, just indicate count
                    context_notes.append(f"{key.replace('_', ' ')}: {len(value)} {options_text}")
            elif not isinstance(value, dict):
                # Simple values
                context_notes.append(f"{key.replace('_', ' ')}: {value}")
        
        if context_notes:
            return f" [Context: {'; '.join(context_notes)}]"
        
        return ""
    
    async def build_classification_context(
        self,
        session: InterviewSession,
        excluded_fields: Optional[Set[str]] = None
    ) -> Dict[str, str]:
        """Build minimal context for classification.

        Only includes reachable unanswered questions in entities_to_extract to prevent
        premature extraction of questions that haven't been asked yet.

        Args:
            session: Interview session
            excluded_fields: Optional set of field names to exclude from entities_to_extract

        Returns:
            Dictionary with current_state, answered_fields, entities_to_extract, required_fields_info
        """
        current_state = session.state.value

        # Format answered fields (minimal - just field names)
        answered_fields = session.get_answered_questions()
        answered_fields_str = ", ".join(answered_fields) if answered_fields else "None"

        # Get reachable unanswered questions using QuestionWalker
        # This prevents premature extraction of questions that haven't been asked yet
        from ..graph.question_walker import QuestionWalker
        question_walker = QuestionWalker()
        question_walker.interview_session = session
        
        # Get reachable unanswered questions based on current question path
        reachable_unanswered = await question_walker.get_reachable_unanswered_questions(session, self.action)
        reachable_set = set(reachable_unanswered) if reachable_unanswered else set()
        
        # Build question map for quick lookup
        question_map = {q.get("name"): q for q in session.question_graph if q.get("name")}
        
        # Handle edge case: if no questions are answered yet, ensure first question is included
        answered_set = set(session.get_answered_questions())
        if not answered_set and session.question_graph:
            first_question_name = session.question_graph[0].get("name")
            if first_question_name and first_question_name not in reachable_set:
                reachable_set.add(first_question_name)
        
        # Start with reachable questions
        active_questions = [q for q in session.question_graph if q.get("name") in reachable_set]
        
        # Always include the active question if it's unanswered
        # This ensures the current question can be answered even if not yet in reachable list
        if session.active_question_key:
            if (session.active_question_key not in answered_set and 
                session.active_question_key not in reachable_set):
                # Active question is not reachable yet but should be included
                active_question_config = question_map.get(session.active_question_key)
                if active_question_config:
                    active_questions.append(active_question_config)

        excluded_set = excluded_fields or set()
        entities_list = []
        required_fields = set(session.get_required_questions())

        for item in active_questions:
            key = item.get('name')
            constraints = item.get('constraints', {})
            if not key or not constraints:
                continue
            
            # Skip fields that should be excluded from LLM extraction
            # EXCEPT when the field is the active question (user is currently being asked this question)
            # This is necessary for proper DECLINE intent classification when user declines to provide data
            is_active_data_field = False
            if key in excluded_set:
                # Always include the active question for proper DECLINE intent classification
                if key != session.active_question_key:
                    continue
                # If it's the active question, mark it as a data_input_field for special handling
                is_active_data_field = True
            
            desc = constraints.get('description', '')
            other_constraints = {k: v for k, v in constraints.items() if k not in ('description', 'data_input_field')}
            constraint_strs = [f"{k}: {v}" for k, v in other_constraints.items()]
            constraint_part = f" ({', '.join(constraint_strs)})" if constraint_strs else ""
            is_required = key in required_fields
            required_marker = " [REQUIRED]" if is_required else " [OPTIONAL]"
            # Add note for data_input_field questions to help LLM understand it can accept DECLINE
            data_field_note = " (expects data input, but user may decline)" if is_active_data_field else ""
            
            # Add context data if available for this question
            context_data_note = await self._get_context_data_note(item, session)
            
            entities_list.append(f"- {key}: {desc}{constraint_part}{required_marker}{data_field_note}{context_data_note}")

        entities_to_extract = "\n".join(entities_list) if entities_list else "None (all questions answered)"

        # Build required fields info (simplified - comma-separated)
        required_fields_info = ", ".join(sorted(required_fields)) if required_fields else "None"

        return {
            "current_state": current_state,
            "answered_fields": answered_fields_str,
            "entities_to_extract": entities_to_extract,
            "required_fields_info": required_fields_info,
        }
    
    async def classify_and_extract(
        self,
        session: InterviewSession,
        utterance: str,
        interaction: "Interaction",
        visitor: "InteractWalker"
    ) -> ClassificationResult:
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
        # Skip classification for terminal states
        if session.state == InterviewState.COMPLETED or session.state == InterviewState.CANCELLED:
            return ClassificationResult(intent=Intent.NONE)

        # Extract data input values from visitor.data before LLM classification
        data_input_values, excluded_fields = self.extract_data_input_values(session, visitor)
        
        # Build user input - prioritize interpretation when available
        interpretation_available = interaction.interpretation and interaction.interpretation.strip()
        user_input = None
        if interpretation_available:
            # Use interpretation as primary source, include utterance only for context if different
            user_input = interaction.interpretation
            if utterance and utterance.strip() and utterance.strip() != interaction.interpretation.strip():
                # Only include utterance if it adds context (is different from interpretation)
                user_input = f"Interpretation: {interaction.interpretation}\nUser's utterance: {utterance}"
        elif utterance and utterance.strip():
            user_input = utterance
        
        # If no user input but we have data input values, process them without LLM
        if not user_input:
            if data_input_values:
                # Process data input values without LLM classification
                return self._build_result_from_data_inputs(data_input_values, session)
            return ClassificationResult(intent=Intent.NONE)

        # Use DSPy if enabled, otherwise use prompt-based implementation
        if self.action.config.use_dspy:
            return await self._classify_with_dspy(session, user_input, interaction, visitor, data_input_values, excluded_fields)

        # Unified classification and extraction using single prompt
        try:
            # Build context for unified prompt (exclude fields with data_input_field)
            context = await self.build_classification_context(session, excluded_fields=excluded_fields)

            prompt = self.action.config.templates.interview_prompt.format(
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
            if self.action.config.model.use_history:
                conversation_history = await self.action._get_conversation_history(
                    interaction,
                    self.action.config.model.history_limit,
                    with_utterance=True,
                    with_response=True,
                    with_interpretation=False,
                    with_event=False,
                    max_statement_length=self.action.config.model.max_statement_length,
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
                model=self.action.config.model.model,
                temperature=self.action.config.model.model_temperature,
                max_tokens=self.action.config.model.model_max_tokens,
                response_format={"type": "json_object"},
            )

            # Parse JSON response
            if isinstance(response, str):
                result = self._extract_json(response)
            else:
                result = response

            if not result:
                # If no LLM result but we have data input values, process them
                if data_input_values:
                    return self._build_result_from_data_inputs(data_input_values, session)
                return ClassificationResult(intent=Intent.NONE)

            # Extract intent and convert to Intent enum
            intent_str = result.get("intent", Intent.NONE.value).upper()
            try:
                intent = Intent(intent_str)
            except ValueError:
                # Invalid intent value, default to NONE
                logger.warning(f"{self.action.get_class_name()}: Invalid intent value '{intent_str}', defaulting to NONE")
                intent = Intent.NONE
            confidence = result.get("confidence", 1.0)

            # Build ClassificationResult
            # Normalize field - handle string "null" from JSON
            field_value = result.get("field")
            if field_value and isinstance(field_value, str):
                field_str = field_value.strip().lower()
                if field_str == "null" or field_str == "none":
                    field_value = None

            classification_result = ClassificationResult(
                intent=intent.value,  # Store as string value for ClassificationResult
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

            # Merge data input values into classification result
            # Data input values take precedence and determine SUBMISSION vs UPDATE per field
            classification_result = self._merge_data_input_values(
                classification_result, data_input_values, session
            )

            return classification_result

        except json.JSONDecodeError as e:
            logger.error(f"{self.action.get_class_name()}: Failed to parse unified classification JSON: {e}", exc_info=True)
            # If LLM failed but we have data input values, process them
            if data_input_values:
                return self._build_result_from_data_inputs(data_input_values, session)
            return ClassificationResult(intent=Intent.NONE)
        except Exception as e:
            logger.error(f"{self.action.get_class_name()}: Failed to classify/extract via unified prompt: {e}", exc_info=True)
            # If LLM failed but we have data input values, process them
            if data_input_values:
                return self._build_result_from_data_inputs(data_input_values, session)
            return ClassificationResult(intent=Intent.NONE)
    
    def _build_result_from_data_inputs(
        self,
        data_input_values: Dict[str, Any],
        session: InterviewSession
    ) -> ClassificationResult:
        """Build ClassificationResult from data input values only.
        
        Checks if fields already have values in the session:
        - If a field has an existing value, treat as UPDATE (set field and value)
        - If a field doesn't have a value, treat as SUBMISSION (add to extracted_data)
        
        Args:
            data_input_values: Dictionary mapping question names to values from visitor.data
            session: Interview session
            
        Returns:
            ClassificationResult with appropriate intent (UPDATE or SUBMISSION)
        """
        if not data_input_values:
            return ClassificationResult(intent=Intent.NONE)
        
        # Separate fields into UPDATE (existing value) and SUBMISSION (no existing value)
        update_fields = {}  # Fields that already have values - treat as UPDATE
        submission_fields = {}  # Fields without values - treat as SUBMISSION
        
        for field_name, value in data_input_values.items():
            existing_value = session.get_response(field_name)
            if existing_value is not None:
                # Field already has a value - treat as UPDATE
                update_fields[field_name] = value
            else:
                # Field doesn't have a value - treat as SUBMISSION
                submission_fields[field_name] = value
        
        # Handle UPDATE fields (fields with existing values)
        if update_fields:
            # Use the first field for UPDATE (handle one at a time)
            first_update_field = next(iter(update_fields))
            first_update_value = update_fields[first_update_field]
            
            result = ClassificationResult(
                intent=Intent.UPDATE.value,
                field=first_update_field,
                value=first_update_value
            )
            
            # If there are multiple update fields, log a warning
            if len(update_fields) > 1:
                logger.warning(
                    f"{self.action.get_class_name()}: Multiple fields with existing values "
                    f"from data_input_field: {list(update_fields.keys())}. "
                    f"Processing first field '{first_update_field}' as UPDATE. "
                    f"Other fields will need to be updated in subsequent interactions."
                )
            
            return result
        
        # Handle SUBMISSION fields (fields without existing values)
        if submission_fields:
            result = ClassificationResult(
                intent=Intent.SUBMISSION.value,
                extracted_data=submission_fields
            )
            return result
        
        # Should not reach here, but handle gracefully
        return ClassificationResult(intent=Intent.NONE)
    
    def _merge_data_input_values(
        self,
        classification_result: ClassificationResult,
        data_input_values: Dict[str, Any],
        session: InterviewSession
    ) -> ClassificationResult:
        """Merge data input values into classification result.
        
        Checks if fields already have values in the session:
        - If a field has an existing value, treat as UPDATE (set field and value)
        - If a field doesn't have a value, treat as SUBMISSION (add to extracted_data)
        
        Args:
            classification_result: Current classification result from LLM
            data_input_values: Dictionary mapping question names to values from visitor.data
            session: Interview session
            
        Returns:
            Updated ClassificationResult with data input values merged
        """
        if not data_input_values:
            return classification_result
        
        # Separate fields into UPDATE (existing value) and SUBMISSION (no existing value)
        update_fields = {}  # Fields that already have values - treat as UPDATE
        submission_fields = {}  # Fields without values - treat as SUBMISSION
        
        for field_name, value in data_input_values.items():
            existing_value = session.get_response(field_name)
            if existing_value is not None:
                # Field already has a value - treat as UPDATE
                update_fields[field_name] = value
            else:
                # Field doesn't have a value - treat as SUBMISSION
                submission_fields[field_name] = value
        
        # Handle UPDATE fields (fields with existing values)
        if update_fields:
            # If there are fields to update, set UPDATE intent
            # Use the first field for UPDATE (handle one at a time)
            first_update_field = next(iter(update_fields))
            first_update_value = update_fields[first_update_field]
            
            classification_result.intent = Intent.UPDATE.value
            classification_result.field = first_update_field
            classification_result.value = first_update_value
            
            # If there are multiple update fields, log a warning
            # (Subsequent fields could be handled in future interactions)
            if len(update_fields) > 1:
                logger.warning(
                    f"{self.action.get_class_name()}: Multiple fields with existing values "
                    f"from data_input_field: {list(update_fields.keys())}. "
                    f"Processing first field '{first_update_field}' as UPDATE. "
                    f"Other fields will need to be updated in subsequent interactions."
                )
        
        # Handle SUBMISSION fields (fields without existing values)
        if submission_fields:
            if classification_result.extracted_data:
                # Merge with existing extracted_data
                classification_result.extracted_data.update(submission_fields)
            else:
                classification_result.extracted_data = submission_fields
            
            # If we have submission fields but no update fields, ensure intent is SUBMISSION
            if not update_fields:
                classification_result.intent = Intent.SUBMISSION.value
        
        return classification_result
    
    async def _classify_with_dspy(
        self,
        session: InterviewSession,
        user_input: str,
        interaction: "Interaction",
        visitor: "InteractWalker",
        data_input_values: Dict[str, Any],
        excluded_fields: Set[str]
    ) -> ClassificationResult:
        """DSPy-based classification and extraction routine.

        Uses DSPy modules with typed signatures for classification, enabling
        optimization via DSPy teleprompters (BootstrapFewShot, MIPROv2, etc.)
        and evaluation with dspy.Evaluate.

        Args:
            session: Interview session
            user_input: User's input (typically with reasoning)
            interaction: Current interaction
            visitor: InteractWalker
            data_input_values: Dictionary of values extracted from visitor.data
            excluded_fields: Set of field names excluded from LLM extraction

        Returns:
            ClassificationResult with unified intent and extracted data
        """
        try:
            # Import DSPy components
            import dspy
            from jvagent.action.model.dspy import DSPyLM
            from jvagent.action.interview.dspy import InterviewClassifier

            # Build context for classification (exclude fields with data_input_field)
            context = await self.build_classification_context(session, excluded_fields=excluded_fields)

            # Get conversation history if needed
            conversation_history = None
            formatted_history = None
            if self.action.config.model.use_history:
                conversation_history = await self.action._get_conversation_history(
                    interaction,
                    self.action.config.model.history_limit,
                    with_utterance=True,
                    with_response=True,
                    with_interpretation=False,
                    with_event=False,
                    max_statement_length=self.action.config.model.max_statement_length,
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
            # Pass model, temperature, and max_tokens to allow agent.yaml overrides
            lm = DSPyLM(
                model_action=model_action,
                model_type="chat",
                model=self.action.config.model.model,
                temperature=self.action.config.model.model_temperature,
                max_tokens=self.action.config.model.model_max_tokens,
            )

            # Configure DSPy with the adapter
            with dspy.context(lm=lm):
                # Create classifier instance with action instance for signature docstring
                classifier = InterviewClassifier(action_instance=self.action)

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

                # Merge data input values into classification result
                classification_result = self._merge_data_input_values(
                    classification_result, data_input_values, session
                )

                return classification_result

        except Exception as e:
            logger.error(
                f"{self.action.get_class_name()}: Failed to classify/extract via DSPy: {e}",
                exc_info=True
            )
            return ClassificationResult(intent=Intent.NONE)
    
    def _extract_json(self, response: str) -> Dict[str, Any]:
        """Extract JSON from response string.

        Args:
            response: Response string

        Returns:
            Parsed JSON dictionary
        """
        from ..utils import extract_json
        return extract_json(response, context=self.action.get_class_name())
