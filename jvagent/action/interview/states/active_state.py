"""ActiveStateInteractAction for active question-answering phase."""

import logging
import json
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from jvagent.action.interact.base import InteractAction
from jvagent.memory import Interaction
from jvspatial.core.annotations import attribute

from ..core.interview_session import InterviewSession
from ..core.question_node import QuestionNode
from ..core.validation import InterviewState, ValidationStatus

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


class ActiveStateInteractAction(InteractAction):
    """Active state action - handles question asking and response validation.
    
    This action:
    - Extracts responses via LLM
    - Validates using QuestionNode validation
    - Stores VALID responses immediately
    - Handles VALID_WITH_FLAG (store + clarify)
    - Re-prompts for INVALID
    - Detects revision intent (keyword + LLM)
    - Transitions to REVIEW when questions complete
    """
    
    description: str = "Active state action for question-answering phase"
    
    weight: int = attribute(
        default=-40,
        description="Execution weight (runs after InteractRouter but before PersonaAction)",
    )
    
    always_execute: bool = attribute(
        default=True,
        description="Always execute when in ACTIVE state",
    )
    
    # Model Configuration
    model_action_type: str = attribute(
        default="OpenAILanguageModelAction",
        description="Entity type of the LanguageModelAction to use",
    )
    
    model: str = attribute(
        default="gpt-4o", 
        description="Default model name"
    )
    
    model_temperature: float = attribute(
        default=0.3, 
        description="Temperature for LLM generation"
    )
    
    model_max_tokens: int = attribute(
        default=4096, 
        description="Max tokens for LLM generation"
    )
    
    use_history: bool = attribute(
        default=True, 
        description="Use conversation history for LLM generation"
    )
    
    max_statement_length: int = attribute(
        default=100, 
        description="Max length of statement to include in history"
    )
    
    history_limit: int = attribute(
        default=5, 
        description="Max number of statements to include in history"
    )
    
    extraction_prompt: str = attribute(
        default="""
        Review the user's message and the conversation history to accurately extract the following entities.
        Be strict on the constraints specified for each entity. Return a JSON object with keys exactly as listed below.
        Include only keys for which you could extract a valid value adhering to all constraints.

        Entities to extract:
        {entities}

        Return ONLY the JSON object with the extracted entities, no delimiters. Do not include any other text or explanation.
        The JSON must have the following structure (only include keys with valid values):
        {sample_json}
        """,
        description="Prompt template for extraction",
    )
    
    revision_detection_keywords: List[str] = attribute(
        default_factory=lambda: ["change", "update", "actually", "correction", "edit", "modify", "instead"],
        description="Keywords that suggest revision intent",
    )
    
    cancellation_keywords: List[str] = attribute(
        default_factory=lambda: ["cancel", "abort", "stop", "nevermind", "forget it", "don't want"],
        description="Keywords that indicate cancellation",
    )

    async def on_register(self) -> None:
        """Called when action is registered."""
        logger.debug("ActiveStateInteractAction registered")

    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute active state action.
        
        Args:
            visitor: The InteractWalker visiting this action
        """
        try:
            interaction = visitor.interaction
            if not interaction:
                logger.warning("ActiveStateInteractAction: No interaction available")
                return
            
            # Get session from visitor (set by parent interview action)
            session = getattr(visitor, 'interview_session', None)
            
            if not session:
                logger.warning(f"{self.get_class_name()}: No session available on visitor")
                return
            
            # Only execute if session is in ACTIVE state
            if session.state != InterviewState.ACTIVE:
                logger.debug(f"ActiveStateInteractAction: Session is in {session.state} state, skipping")
                return
        
            # Check for cancellation first
            utterance_lower = visitor.utterance.lower()
            if any(keyword in utterance_lower for keyword in self.cancellation_keywords):
                try:
                    session.transition_to(InterviewState.CANCELLED)
                    await session.save()
                    await self.respond_with_directive(visitor, "Interview cancelled. Let me know if you'd like to start over.")
                except Exception as e:
                    logger.error(f"ActiveStateInteractAction: Failed to handle cancellation: {e}", exc_info=True)
                return
            
            # Check for revision intent
            try:
                revision_field = await self.detect_revision_intent(visitor.utterance, session)
                if revision_field:
                    await self.handle_revision(revision_field, visitor, session, interaction)
                    return
            except Exception as e:
                logger.error(f"ActiveStateInteractAction: Failed to detect/handle revision: {e}", exc_info=True)
                # Continue with normal flow
            
            # Get current unanswered questions
            unanswered = session.get_unanswered_questions()
            if not unanswered:
                # All questions answered, transition to REVIEW
                try:
                    session.transition_to(InterviewState.REVIEW)
                    await session.save()
                    logger.debug("ActiveStateInteractAction: All questions answered, transitioning to REVIEW")
                except Exception as e:
                    logger.error(f"ActiveStateInteractAction: Failed to transition to REVIEW: {e}", exc_info=True)
                return
            
            # We have some responses, so this is a continuation - try to extract answers
            # Generate extraction prompt for unanswered questions
            active_questions = [q for q in session.question_index if q.get("name") in unanswered]
            prompt = self.generate_extraction_prompt(active_questions)
            
            # Extract responses via LLM
            try:
                response = await self.call_llm(prompt, visitor, use_history=self.use_history, json_only=True)
                if isinstance(response, str):
                    response = self.extract_json(response)
            except Exception as e:
                logger.error(f"ActiveStateInteractAction: Failed to extract responses via LLM: {e}", exc_info=True)
                # Continue to get directive for next question
                response = None
            
            if not response:
                # No extraction, get directive for next question
                try:
                    directive = await self.get_directive(interaction, session)
                    if directive:
                        await self.respond_with_directive(visitor, directive)
                except Exception as e:
                    logger.error(f"ActiveStateInteractAction: Failed to get directive: {e}", exc_info=True)
                return
            
            # Validate and store responses
            try:
                await self.process_responses(response, session, visitor, interaction)
            except Exception as e:
                logger.error(f"ActiveStateInteractAction: Failed to process responses: {e}", exc_info=True)
                return
            
            # Check if all required questions are answered
            if session.has_all_required_answers():
                try:
                    session.transition_to(InterviewState.REVIEW)
                    await session.save()
                    logger.debug("ActiveStateInteractAction: All required questions answered, transitioning to REVIEW")
                except Exception as e:
                    logger.error(f"ActiveStateInteractAction: Failed to transition to REVIEW: {e}", exc_info=True)
            else:
                # Get directive for next question
                try:
                    directive = await self.get_directive(interaction, session)
                    if directive:
                        await self.respond_with_directive(visitor, directive)
                except Exception as e:
                    logger.error(f"ActiveStateInteractAction: Failed to get directive: {e}", exc_info=True)
        
        except Exception as e:
            logger.error(f"ActiveStateInteractAction: Unexpected error in execute(): {e}", exc_info=True)
            raise  # Re-raise to let InteractWalker handle it

    async def detect_revision_intent(
        self, 
        utterance: str, 
        session: InterviewSession
    ) -> Optional[str]:
        """Detect if user wants to revise a previously answered question.
        
        Uses hybrid approach: keyword matching + LLM verification.
        
        Args:
            utterance: User's utterance
            session: Interview session
            
        Returns:
            Field name if revision detected, None otherwise
        """
        utterance_lower = utterance.lower()
        
        # Step 1: Keyword matching
        has_revision_keyword = any(
            keyword in utterance_lower 
            for keyword in self.revision_detection_keywords
        )
        
        if not has_revision_keyword:
            return None
        
        # Step 2: LLM extraction to identify which field
        revision_prompt = f"""
        The user wants to revise or change a previously provided answer.
        Identify which field they want to change from their message.
        
        User message: {utterance}
        
        Available fields: {', '.join(session.get_answered_questions())}
        
        Return ONLY the field name (key) that should be revised, or "none" if unclear.
        """
        
        # Use a simple extraction - in production, this would use the LLM
        # For now, do pattern matching
        answered_fields = session.get_answered_questions()
        for field in answered_fields:
            # Check if field name or related terms appear in utterance
            field_lower = field.lower().replace("_", " ")
            if field_lower in utterance_lower or field in utterance_lower:
                return field
        
        return None

    async def handle_revision(
        self,
        field: str,
        visitor: "InteractWalker",
        session: InterviewSession,
        interaction: Interaction
    ) -> None:
        """Handle revision of a previously answered question.
        
        Args:
            field: Field name to revise
            visitor: InteractWalker
            session: Interview session
            interaction: Current interaction
        """
        # Extract new value from utterance
        question_config = next(
            (q for q in session.question_index if q.get("name") == field),
            None
        )
        
        if not question_config:
            logger.warning(f"ActiveStateInteractAction: Field {field} not found in question_index")
            return
        
        # Extract new value
        prompt = self.generate_extraction_prompt([question_config])
        response = await self.call_llm(prompt, visitor, use_history=self.use_history, json_only=True)
        if isinstance(response, str):
            response = self.extract_json(response)
        
        if response and field in response:
            new_value = response[field]
            
            # Process input first (handles custom transformations)
            question_node = await self._get_question_node(field, session)
            if question_node:
                if isinstance(new_value, str):
                    new_value = await question_node.process_input(new_value, session)
                
                # Then validate
                validation_status, feedback = await question_node.validate_response(new_value, session)
                
                if validation_status == ValidationStatus.VALID:
                    session.set_response(field, new_value)
                    session.set_validation_status(field, validation_status)
                    await session.save()
                    
                    directive = f"Updated {field} to {new_value}. "
                    if session.has_all_required_answers():
                        directive += "All information collected. Let me show you a summary."
                    else:
                        directive += "Now, let me continue with the next question."
                    
                    await self.respond_with_directive(visitor, directive)
                else:
                    # Invalid revision, ask again
                    directive = feedback or f"Please provide a valid value for {field}."
                    await self.respond_with_directive(visitor, directive)

    async def process_responses(
        self,
        responses: Dict[str, Any],
        session: InterviewSession,
        visitor: "InteractWalker",
        interaction: Interaction
    ) -> None:
        """Process and validate extracted responses.
        
        Args:
            responses: Extracted responses dictionary
            session: Interview session
            visitor: InteractWalker
            interaction: Current interaction
        """
        for field, value in responses.items():
            # Find question node for validation
            question_node = await self._get_question_node(field, session)
            if not question_node:
                logger.warning(f"ActiveStateInteractAction: Question node not found for {field}")
                continue
            
            # Process input first (handles custom transformations)
            if isinstance(value, str):
                value = await question_node.process_input(value, session, interaction)
            
            # Then validate
            validation_status, feedback = await question_node.validate_response(value, session)
            session.set_validation_status(field, validation_status)
            
            if validation_status == ValidationStatus.VALID:
                # Store immediately and continue
                session.set_response(field, value)
                await session.save()
                logger.debug(f"ActiveStateInteractAction: Stored VALID response for {field}")
            
            elif validation_status == ValidationStatus.VALID_WITH_FLAG:
                # Store but ask for clarification
                session.set_response(field, value)
                await session.save()
                logger.debug(f"ActiveStateInteractAction: Stored VALID_WITH_FLAG response for {field}")
                
                # Ask clarifying question
                if feedback:
                    await self.respond_with_directive(visitor, feedback)
            
            else:  # INVALID
                # Don't store, ask for correction
                logger.debug(f"ActiveStateInteractAction: INVALID response for {field}")
                error_msg = feedback or f"Please provide a valid value for {field}."
                await self.respond_with_directive(visitor, error_msg)

    async def _get_question_node(
        self, 
        field: str, 
        session: InterviewSession
    ) -> Optional[QuestionNode]:
        """Get QuestionNode for a specific field.
        
        Args:
            field: Field name
            session: Interview session
            
        Returns:
            QuestionNode if found, None otherwise
        """
        # Find question config
        question_config = next(
            (q for q in session.question_index if q.get("name") == field),
            None
        )
        
        if not question_config:
            return None
        
        # Question nodes should be created during on_register in InterviewInteractAction
        # For now, create on-demand if not found
        # Try to find via edge connection from ActiveStateInteractAction
        question_nodes = await self.nodes(direction="out", node=QuestionNode)
        question_node = next(
            (n for n in question_nodes if n.label == field),
            None
        )
        
        if not question_node:
            # Create on-demand if not found (shouldn't happen in normal flow)
            question_node = await QuestionNode.create(
                agent_id=self.agent_id,
                state=question_config,
                label=field,
            )
            await self.connect(question_node)
        
        return question_node

    def generate_extraction_prompt(self, question_index: List[Dict[str, Any]]) -> str:
        """Generate extraction prompt from question index.
        
        Args:
            question_index: List of question configurations
            
        Returns:
            Formatted extraction prompt
        """
        entities_list = []
        sample_json_lines = []

        for item in question_index:
            key = item.get('name')
            constraints = item.get('constraints', {})

            if not key or not constraints:
                continue

            desc = constraints.get('description', '')
            other_constraints = {k: v for k, v in constraints.items() if k != 'description'}
            constraint_strs = [f"{k}: {v}" for k, v in other_constraints.items()]
            constraint_part = f" ({', '.join(constraint_strs)})" if constraint_strs else ""
            entities_list.append(f"- {key}: {desc}{constraint_part}")
            sample_json_lines.append(f"  '{key}': '<extracted value>'")

        entities = "\n".join(entities_list)
        sample_json = '{\n' + ',\n'.join(sample_json_lines) + '\n}'

        return self.extraction_prompt.format(entities=entities, sample_json=sample_json)

    async def get_directive(
        self, 
        interaction: Interaction, 
        session: InterviewSession
    ) -> str:
        """Get directive from walking through QuestionNodes to find unanswered questions.
        
        Args:
            interaction: Current interaction
            session: Interview session
            
        Returns:
            Directive string or empty string
        """
        # Find first unanswered question
        unanswered = session.get_unanswered_questions()
        if not unanswered:
            return ""
        
        # Get first unanswered question name
        first_unanswered = unanswered[0]
        
        # Find corresponding question node
        question_nodes = await self.nodes(direction="out", node=QuestionNode)
        question_node = next(
            (n for n in question_nodes if n.label == first_unanswered),
            None
        )
        
        if question_node:
            # Create a simple walker-like object to pass to execute
            class SimpleWalker:
                def __init__(self, session):
                    self.interview_session = session
            
            walker = SimpleWalker(session)
            directive = await question_node.execute(walker)
            return directive if directive else ""
        
        return ""

    async def respond_with_directive(
        self,
        visitor: "InteractWalker",
        directive: str
    ) -> None:
        """Respond with a directive.
        
        Args:
            visitor: InteractWalker
            directive: Directive string
        """
        if directive:
            await visitor.add_event("gathering information for interview")
            await self.respond(
                visitor,
                directives=[directive],
                parameters=self.parameters if self.parameters else None
            )

    def extract_json(self, response: str) -> Dict[str, Any]:
        """Extract JSON from response string.
        
        Args:
            response: Response string
            
        Returns:
            Parsed JSON dictionary
        """
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            # Try to extract JSON from text
            json_match = re.search(r'\{[^{}]*\}', response)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass
            logger.warning("ActiveStateInteractAction: Failed to extract JSON from response")
            return {}

    async def call_llm(
        self, 
        prompt: str, 
        visitor: "InteractWalker", 
        use_history: bool = True, 
        json_only: bool = True
    ) -> str:
        """Call LLM with extraction prompt.
        
        Args:
            prompt: System prompt
            visitor: InteractWalker
            use_history: Whether to use conversation history
            json_only: Whether to request JSON format
            
        Returns:
            LLM response
        """
        model_action = await self.get_model_action(required=True)
        
        conversation_history = None
        if use_history and visitor.interaction:
            conversation_history = await self._get_conversation_history(
                visitor.interaction,
                self.history_limit,
                with_utterance=True,
                with_response=False,
                with_interpretation=False,
                with_event=True,
                max_statement_length=self.max_statement_length,
            )
        
        try:
            response = await model_action.generate(
                prompt=visitor.utterance,
                stream=False,
                system=prompt,
                history=conversation_history,
                calling_action_name=self.get_class_name(),
                model=self.model,
                temperature=self.model_temperature,
                max_tokens=self.model_max_tokens,
                response_format={"type": "json_object"} if json_only else None,
            )
        except Exception as e:
            logger.error(f"ActiveStateInteractAction: Failed to extract entities: {e}", exc_info=True)
            raise
        
        return response

    async def _get_conversation_history(
        self,
        interaction: Interaction,
        history_limit: int,
        with_utterance: bool = True,
        with_response: bool = False,
        with_interpretation: bool = False,
        with_event: bool = True,
        max_statement_length: Optional[int] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Get formatted conversation history for the language model.
        
        Args:
            interaction: Current interaction
            history_limit: Number of past interactions to include
            with_utterance: Include user utterances
            with_response: Include AI responses
            with_interpretation: Include interpretations
            with_event: Include events
            max_statement_length: Truncate to this length
            
        Returns:
            List of message dictionaries or None
        """
        if history_limit <= 0:
            return None

        from jvagent.memory.conversation import Conversation

        conversation = await Conversation.get(interaction.conversation_id)
        if not conversation:
            return []

        history = await conversation.get_interaction_history(
            limit=history_limit,
            excluded=interaction.id,
            with_utterance=with_utterance,
            with_response=with_response,
            with_interpretation=with_interpretation,
            with_event=with_event,
            formatted=True,
            max_statement_length=max_statement_length,
        )

        def _truncate(content: str) -> str:
            if max_statement_length and len(content) > max_statement_length:
                return content[:max_statement_length] + "..."
            return content

        if with_utterance and with_response and interaction.response:
            history.append({
                "role": "user",
                "content": _truncate(interaction.utterance),
            })
            history.append({
                "role": "assistant",
                "content": _truncate(interaction.response),
            })
        elif with_response and interaction.response:
            history.append({
                "role": "assistant",
                "content": _truncate(interaction.response),
            })

        return history if history else []

