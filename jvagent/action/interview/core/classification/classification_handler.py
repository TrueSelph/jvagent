"""Classification handler for interview action.

Extracted classification logic from interview_interact_action.py for better
separation of concerns and maintainability.
"""

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

from ..foundation.enums import Intent, InterviewState
from ..session.interview_session import InterviewSession
from ..graph.question_node import QuestionNode

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.memory import Interaction
    from jvagent.action.interview.interview_interact_action import InterviewInteractAction

logger = logging.getLogger(__name__)

# Keys in LLM response that are metadata, not extracted field values
EXTRACTION_METADATA_KEYS = frozenset({"intent", "confidence", "field", "value", "reasoning", "extracted"})


@dataclass
class ClassificationResult:
    """Result of unified classification and extraction routine.

    Uses unified intent types: CANCELLATION, CONFIRMATION, UPDATE, DECLINE, SUBMISSION, NONE
    
    This structure is used for both LLM-based extraction and data_input_field extraction,
    ensuring consistent payload shape for downstream consumers.
    """
    intent: str  # "CANCELLATION", "CONFIRMATION", "UPDATE", "DECLINE", "SUBMISSION", "NONE"
    confidence: float = 1.0  # Confidence score for the classification

    # Unified field/value structure (used for UPDATE, DECLINE, and SUBMISSION)
    field: Optional[str] = None  # Field name (for UPDATE/DECLINE intent) or null
    value: Optional[Any] = None  # Field value (for UPDATE intent) or null

    # For SUBMISSION intent - extracted field values (multiple fields)
    extracted_data: Optional[Dict[str, Any]] = None  # Extracted responses for "SUBMISSION" intent
    
    # Metadata for tracking extraction source
    from_data_input_field: bool = False  # True if data_input_field contributed to this result


class ClassificationHandler:
    """Handles classification and extraction logic for interview actions."""
    
    def __init__(self, action: "InterviewInteractAction"):
        """Initialize classification handler with action instance.
        
        Args:
            action: InterviewInteractAction instance
        """
        self.action = action

    def _extract_field_values(self, result: Dict[str, Any], intent: Intent) -> Dict[str, Any]:
        """Extract field-value pairs from LLM result using the single canonical format.

        LLM returns all field data in result["extracted"]: a list of one-key dicts.
        SUBMISSION/UPDATE = actual values; DECLINE = value "N/A" (excluded from returned dict).

        Args:
            result: Raw parsed JSON from LLM response
            intent: Classified intent (extraction applies to SUBMISSION, UPDATE; DECLINE "N/A" filtered out)

        Returns:
            Filtered dict of field -> value (excludes empty/None/whitespace-only and "N/A")
        """
        raw_list = result.get("extracted")
        if not isinstance(raw_list, list):
            return {}

        merged: Dict[str, Any] = {}
        for item in raw_list:
            if not isinstance(item, dict) or len(item) != 1:
                continue
            field_name, val = next(iter(item.items()))
            if not isinstance(field_name, str) or not field_name.strip():
                continue
            if val is None:
                continue
            if isinstance(val, str) and (not val.strip() or val.strip().upper() == "N/A"):
                continue
            merged[field_name.strip()] = val

        return merged

    def _get_extraction_mode(self, question_config: Dict[str, Any]) -> str:
        """Infer extraction mode from question configuration.
        
        Determines whether a field should use verbatim, normalized, or select extraction
        based on the field's constraints and description.
        
        Args:
            question_config: Question configuration dictionary
            
        Returns:
            Extraction mode: "verbatim", "normalized", or "select"
        """
        constraints = question_config.get("constraints", {})
        
        # Check for explicit extraction_mode constraint
        explicit_mode = constraints.get("extraction_mode")
        if explicit_mode in ("verbatim", "normalized", "select"):
            return explicit_mode
        
        # Check if field has options (either static or from context provider)
        has_options = bool(constraints.get("options"))
        has_context_provider = bool(question_config.get("input_context_provider"))
        has_input_context = bool(question_config.get("input_context"))
        
        if has_options or has_context_provider or has_input_context:
            return "select"
        
        # Check description for verbatim keywords
        description = constraints.get("description", "").lower()
        verbatim_keywords = [
            "description", "narrative", "details", "incident", "explain", 
            "describe", "story", "account", "report"
        ]
        
        if any(keyword in description for keyword in verbatim_keywords):
            return "verbatim"
        
        # Default to normalized for structured fields
        return "normalized"

    def extract_data_input_values(
        self,
        session: InterviewSession,
        visitor: "InteractWalker"
    ) -> Tuple[Dict[str, Any], Set[str]]:
        """Extract values from visitor.data for fields with data_input_field configured.

        Scans question_graph for fields with data_input_field in constraints and checks
        visitor.data for matching keys. When the key is absent, auto-populates with "N/A"
        only for the current question (first unanswered) to avoid pre-populating future
        questions. Returns both the extracted values and the set of field names that have
        data_input_field (for exclusion from LLM).

        Args:
            session: Interview session
            visitor: InteractWalker with data property

        Returns:
            Tuple of (extracted_values_dict, excluded_field_names_set):
            - extracted_values_dict: Maps question names to values from visitor.data or "N/A"
            - excluded_field_names_set: Set of question names that have data_input_field
        """
        extracted_values = {}
        excluded_fields = set()
        
        # Get question graph from action
        question_graph = self.action._get_question_graph()
        
        # Check visitor.data exists and is a dict
        if not hasattr(visitor, 'data') or not isinstance(visitor.data, dict):
            return extracted_values, excluded_fields

        unanswered = session.get_unanswered_questions()
        current_question = unanswered[0] if unanswered else None

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

                # Value from visitor.data key, or "N/A" for current question when key absent
                if data_input_field in visitor.data:
                    value = visitor.data[data_input_field]
                    if value is not None:
                        extracted_values[question_name] = value
                elif question_name == current_question:
                    extracted_values[question_name] = "N/A"

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
        excluded_fields: Optional[Set[str]] = None,
        visitor: Optional["InteractWalker"] = None
    ) -> Dict[str, str]:
        """Build context for classification.

        Args:
            session: Interview session
            excluded_fields: Optional set of field names to exclude from entities_to_extract
            visitor: Optional InteractWalker for branch function evaluation

        Returns:
            Dictionary with current_state, answered_fields (with values), entities_to_extract
        """
        current_state = session.state.value

        # Format answered fields with their current values for UPDATE context
        answered_fields = session.get_answered_questions()
        if answered_fields:
            answered_pairs = []
            for field_name in answered_fields:
                value = session.get_response(field_name)
                value_str = str(value) if value is not None else "None"
                # Truncate long values to prevent token bloat
                if len(value_str) > 100:
                    value_str = value_str[:97] + "..."
                answered_pairs.append(f"{field_name}: {value_str}")
            answered_fields_str = ", ".join(answered_pairs)
        else:
            answered_fields_str = "None"

        # Get reachable questions from the active branch path
        from ..graph.question_path_walker import QuestionPathWalker
        
        # Get first node to start traversal
        first_node = None
        if session.question_graph:
            first_question_name = session.question_graph[0].get("name")
            if first_question_name:
                try:
                    first_node = await self.action._get_first_question_node(session)
                except Exception:
                    logger.exception("Failed to get first question node for path walker")
        
        # Get reachable questions on active branch path
        if first_node and visitor:
            reachable_names = await QuestionPathWalker.get_reachable_questions(
                session, first_node, visitor
            )
            active_questions = [
                q for q in session.question_graph 
                if q.get("name") in reachable_names
            ]
        else:
            # Fallback: use all questions if we can't determine reachable path
            active_questions = [q for q in session.question_graph]

        excluded_set = excluded_fields or set()
        answered_set = set(answered_fields)  # Mutual exclusion: unanswered only in entities_to_extract
        entities_list = []
        required_fields = set(session.get_required_questions())

        for item in active_questions:
            key = item.get('name')
            constraints = item.get('constraints', {})
            if not key or not constraints:
                continue

            # Skip fields that should be excluded from LLM extraction
            if key in excluded_set:
                continue

            # Skip answered fields so Answered and Unanswered are mutually exclusive
            if key in answered_set:
                continue

            desc = constraints.get('description', '')
            other_constraints = {k: v for k, v in constraints.items() if k not in ('description', 'data_input_field', 'extraction_mode', 'options')}
            constraint_strs = [f"{k}: {v}" for k, v in other_constraints.items()]
            constraints_display = ", ".join(constraint_strs) if constraint_strs else "(none)"
            is_required = key in required_fields
            required_marker = "[REQUIRED]" if is_required else "[OPTIONAL]"
            
            # Determine extraction mode
            extraction_mode = self._get_extraction_mode(item)
            mode_marker = f"[{extraction_mode}]"
            
            # Get inline options for select mode
            options_note = ""
            if extraction_mode == "select":
                # Check for static options in constraints
                static_options = constraints.get("options", [])
                if static_options and isinstance(static_options, list):
                    options_str = ", ".join(str(opt) for opt in static_options[:10])  # Limit to first 10
                    if len(static_options) > 10:
                        options_str += f" (and {len(static_options) - 10} more)"
                    options_note = f" | Options: {options_str}"
                else:
                    # Try to get dynamic context data for options
                    context_data = item.get("input_context", {})
                    provider_name = item.get("input_context_provider")
                    
                    if provider_name:
                        try:
                            from ..foundation.decorators import get_input_context_provider
                            func = get_input_context_provider(session.interview_type, provider_name)
                            if func:
                                import inspect
                                if inspect.iscoroutinefunction(func):
                                    dynamic_context = await func(session, None)
                                else:
                                    dynamic_context = func(session, None)
                                if dynamic_context and isinstance(dynamic_context, dict):
                                    context_data = {**context_data, **dynamic_context}
                        except Exception as e:
                            logger.debug(f"Could not fetch context for options: {e}")
                    
                    # Extract options from context data
                    for ctx_key, ctx_value in context_data.items():
                        if isinstance(ctx_value, list) and ctx_value:
                            options_str = ", ".join(str(v) for v in ctx_value[:10])
                            if len(ctx_value) > 10:
                                options_str += f" (and {len(ctx_value) - 10} more)"
                            options_note = f" | Options: {options_str}"
                            break  # Use first list found

            # Add context data note (for non-select modes or additional context)
            context_data_note = await self._get_context_data_note(item, session)

            entities_list.append(
                f"- {key} {required_marker} {mode_marker} — Expected: \"{desc}\" | Constraints: {constraints_display}{options_note}{context_data_note}"
            )

        entities_to_extract = "\n".join(entities_list) if entities_list else "None (all questions answered)"

        # Get current question (first unanswered) for context
        unanswered = session.get_unanswered_questions()
        current_question = unanswered[0] if unanswered else "None (all questions answered)"

        return {
            "current_state": current_state,
            "current_question": current_question,
            "answered_fields": answered_fields_str,
            "entities_to_extract": entities_to_extract,
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

        # Unified classification and extraction using single prompt
        try:
            # Build context for unified prompt (exclude fields with data_input_field)
            context = await self.build_classification_context(session, excluded_fields=excluded_fields, visitor=visitor)

            # Get conversation history for model API (passed as separate messages, not embedded in prompt)
            conversation_history_list = None
            if self.action.config.model.use_history:
                conversation_history_list = await self.action._get_conversation_history(
                    interaction,
                    self.action.config.model.history_limit,
                    with_utterance=True,
                    with_response=True,
                    with_interpretation=False,
                    with_event=False,
                    max_statement_length=self.action.config.model.max_statement_length,
                )

            # Build classification rules using configuration options
            from ..foundation.prompts import build_classification_rules
            classification_config = self.action.config.classification
            classification_rules_core = build_classification_rules(
                include_reasoning=classification_config.require_structured_reasoning,
                include_examples=classification_config.include_few_shot_examples,
                include_reference_resolution=classification_config.enable_reference_resolution,
                include_composition=classification_config.enable_composition,
                max_examples=classification_config.max_examples
            )
            
            prompt = self.action.config.templates.interview_prompt.format(
                user_input=user_input,
                current_state=context["current_state"],
                current_question=context["current_question"],
                answered_fields=context["answered_fields"],
                entities_to_extract=context["entities_to_extract"],
                classification_rules_core=classification_rules_core,
            )

            # Get model action
            model_action = await self.action.get_model_action(required=True)
            if not model_action:
                logger.warning(f"{self.action.get_class_name()}: Could not get model action for unified classification")
                return ClassificationResult(intent=Intent.NONE)

            # Pass history to generate() for API (model may use as separate messages)
            conversation_history = conversation_history_list

            # Call LLM with unified prompt
            # Use utterance as primary_text when available, since it contains the actual user input
            # The system prompt already has the full user_input (interpretation + utterance) embedded
            # so the model has access to both; we just need to avoid sending interpretation as the
            # user message since it can cause role confusion
            primary_text = utterance if utterance and utterance.strip() else (
                interaction.interpretation if interpretation_available else ""
            )
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

            # Remove field/value from result so they never appear in extraction output (canonical format is intent, confidence, extracted only)
            result.pop("field", None)
            result.pop("value", None)

            # Extract intent and convert to Intent enum
            intent_str = result.get("intent", Intent.NONE.value).upper()
            try:
                intent = Intent(intent_str)
            except ValueError:
                # Invalid intent value, default to NONE
                logger.warning(f"{self.action.get_class_name()}: Invalid intent value '{intent_str}', defaulting to NONE")
                intent = Intent.NONE
            confidence = result.get("confidence", 1.0)

            # Build ClassificationResult (LLM always sends field/value null; we derive from extracted)
            classification_result = ClassificationResult(
                intent=intent.value,
                confidence=confidence,
                field=None,
                value=None,
            )

            # Extract field values from result["extracted"] (list of one-key dicts; N/A filtered out)
            extracted_data = self._extract_field_values(result, intent)
            if extracted_data:
                classification_result.extracted_data = extracted_data

            # DECLINE: derive declined field from extracted entry with value "N/A"
            if intent == Intent.DECLINE:
                raw_list = result.get("extracted")
                if isinstance(raw_list, list):
                    for item in raw_list:
                        if isinstance(item, dict) and len(item) == 1:
                            (fname, v) = next(iter(item.items()))
                            if isinstance(v, str) and v.strip().upper() == "N/A":
                                classification_result.field = fname.strip() if isinstance(fname, str) else None
                                break

            # UPDATE: set field/value from single entry for downstream consumers
            if intent == Intent.UPDATE and extracted_data and len(extracted_data) == 1:
                classification_result.field = next(iter(extracted_data.keys()))
                classification_result.value = next(iter(extracted_data.values()))

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

    def _format_conversation_history_for_prompt(
        self,
        history: Optional[List[Dict[str, Any]]],
    ) -> str:
        """Format conversation history for inclusion in the classification prompt.
        
        Adds turn numbers to help the LLM reason about multi-turn composition
        and reference resolution.

        Args:
            history: List of message dicts with 'role' and 'content' (from get_interaction_history formatted=True).

        Returns:
            String suitable for the conversation_history prompt placeholder; "(none)" if empty.
        """
        if not history:
            return "(none)"
        lines = []
        turn_number = 1
        for msg in history:
            role = (msg.get("role") or "unknown").strip().lower()
            content = (msg.get("content") or "").strip()
            if content:
                lines.append(f"[{turn_number}] {role}: {content}")
                turn_number += 1
        return "\n".join(lines) if lines else "(none)"

    def _build_result_from_data_inputs(
        self,
        data_input_values: Dict[str, Any],
        session: InterviewSession
    ) -> ClassificationResult:
        """Build ClassificationResult from data input values only.
        
        Simulates the extraction payload structure that would be produced by LLM extraction,
        ensuring consistent shape for downstream consumers (execute, InterviewWalker, handlers).
        
        Checks if fields already have values in the session:
        - If a field has an existing value, treat as UPDATE (set field and value)
        - If a field doesn't have a value, treat as SUBMISSION (add to extracted_data)
        
        Args:
            data_input_values: Dictionary mapping question names to values from visitor.data
            session: Interview session
            
        Returns:
            ClassificationResult with appropriate intent (UPDATE or SUBMISSION) and
            from_data_input_field=True to indicate source
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
                value=first_update_value,
                from_data_input_field=True
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
                extracted_data=submission_fields,
                from_data_input_field=True
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
        
        Augments LLM classification result with data_input_field values, ensuring the
        extraction payload includes both LLM-extracted and directly-provided data.
        
        Checks if fields already have values in the session:
        - If a field has an existing value, treat as UPDATE (set field and value)
        - If a field doesn't have a value, treat as SUBMISSION (add to extracted_data)
        
        Args:
            classification_result: Current classification result from LLM
            data_input_values: Dictionary mapping question names to values from visitor.data
            session: Interview session
            
        Returns:
            Updated ClassificationResult with data input values merged and
            from_data_input_field=True when data_input_field contributes
        """
        if not data_input_values:
            return classification_result
        
        # Mark that data_input_field contributed to this result
        classification_result.from_data_input_field = True
        
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

    def _extract_json(self, response: str) -> Dict[str, Any]:
        """Extract JSON from response string.

        Args:
            response: Response string

        Returns:
            Parsed JSON dictionary
        """
        from ..utils import extract_json
        return extract_json(response, context=self.action.get_class_name())
