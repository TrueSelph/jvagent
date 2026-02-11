"""Report interview for report submission."""

import re
import json
from typing import Any, Dict, List, Optional, Tuple, Union

from jvagent.action.interview import (
    InterviewInteractAction,
    input_handler,
    input_validator,
    input_directive_override,
    on_interview_complete,
    branch_function,
)
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.foundation.enums import ValidationStatus
from jvagent.memory import Interaction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.interact.base import InteractAction
from jvspatial.core.annotations import attribute


class FeedbackInterviewInteractAction(InterviewInteractAction):
    """Feedback Interview action is used to create feedback for incidents and projects.

    This is a concrete implementation of InterviewInteractAction that defines
    a specific interview flow. Sessions are identified by
    interview_type='ReportInterviewInteractAction' and attached to Conversation nodes.

    The question_graph can be overridden in agent.yaml to customize questions.

    Architecture:
        The interview system uses a unified classification and extraction approach
        that detects user intent (CANCELLATION, CONFIRMATION, UPDATE, SUBMISSION, NONE)
        and extracts field values in a single LLM call. All state management and
        directive generation is handled within the main InterviewInteractAction.

    Model Configuration:
        Model settings (model_action_type, model, model_temperature, model_max_tokens,
        use_history, max_statement_length, history_limit) are inherited from
        InterviewInteractAction and can be configured in agent.yaml. These settings
        control the unified classification/extraction LLM call and all directive
        generation prompts.
    """

    description: str = "Feedback Interview action is used to create feedback for incidents and projects."

    # REQUIRED when using InteractRouter: Anchors for intelligent routing
    # Must cover both initial entry and intermediate states (when answering questions)
    anchors: List[str] = attribute(
        default_factory=lambda: [
            # Initial entry - specific to feedback on existing reports/projects
            "User wants to provide feedback on a completed report or project",
            "User is giving feedback about work that was done",
            "User wants to comment on the resolution of a previous report",
            "User is providing an update on a previously reported issue",
            "User wants to evaluate service quality or contractor performance",
            
            # Providing details - specific to feedback
            "User is providing feedback details about completed work",
            "User is answering questions about their experience with a resolved issue",
            "User is describing the outcome or quality of work performed",
            "User is sharing photos or evidence of completed work for feedback",
            
            # Follow-up / update
            "User is providing an update or follow-up on previously submitted feedback",
            "User is adding additional comments to existing feedback",
            "User wants to amend or supplement previously given feedback",
            
            # Revision/cancel/edit/confirm - active feedback only
            "User is revising, canceling, updating or confirming active feedback being submitted",
            "User wants to modify feedback that is currently being submitted",
            "User needs to change ratings or comments in an incomplete feedback form"
        ],
        description="Anchor statements for InteractRouter routing"
    )

    question_graph: List[Dict[str, Any]] = attribute(
        default_factory=lambda: [
            {
                "name": "feedback_content",
                "question": "Please share your feedback.",
                "constraints": {
                    "description": "Full details about the feedback the user wants to provide.",
                    "type": "string"
                },
                "branches": [
                    {
                        "condition": {"function": "can_ask_for_media"},
                        "target": "feedback_media"
                    }
                ],
                "default_next": "report_details",
                "required": True
            },
            {
                "name": "feedback_media", # capture media if user provides it, do not ask for media
                "question": "Do you have any media to upload along with your feedback?",
                "constraints": {
                    "description": "Media of feedback uploaded by user.",
                    "type": "list",
                    "data_input_field": "whatsapp_media",
                },
                "default_next": "report_details",
                "required": False
            },
            {
                "name": "report_details",
                "question": "Please describe the report or issue you want to provide feedback about.",
                "constraints": {
                    "description": "Details about the report or issue the user wants to provide feedback about.",
                    "type": "string"
                },
                "branches": [
                    {
                        "condition": {"function": "search_for_report"},
                        "target": "selected_report_id"
                    }
                ],
                "default_next": "REVIEW",
                "required": False
            },
            {
                "name": "selected_report_id",
                "question": "I found multiple completed reports that match your description. Please select which one you want to provide feedback about:",
                "constraints": {
                    "description": "The correct report id from the list of matching reports the user selected. return the report id(not the index) for the report selected.",
                    "type": "int"
                },
                "default_next": "REVIEW",
                "required": False
            }
        ],
        description="List of question configurations defining the interview graph. Can be overridden in agent.yaml. "
                    "Supports conditional branching via 'branches' and 'default_next'. "
                    "Handlers, validators, and directive overrides can be registered via decorators "
                    "(@input_handler, @input_validator, @input_directive_override) or specified as string "
                    "references in constraints (input_handler, input_validator). "
                    "Branch functions can be registered with @branch_function decorator for complex branching logic."
    )

    # custom functions 
    async def _get_model_action(user_prompt:str, system_prompt:str, json_response:bool=False):
        import logging
        logger = logging.getLogger(__name__)
        try:

            # Find the action instance from the graph
            action = await FeedbackInterviewInteractAction.find_one({
                "context.enabled": True
            })

            if not action:
                logger.warning("FeedbackInterviewInteractAction: Could not find enabled action instance in graph.")
                return False

            model_action = await action.get_model_action()
            if not model_action:
                logger.warning("No model action found")
                return False
            
            if json_response:
                
                result_str = await model_action.generate(
                    prompt=user_prompt,
                    stream=False,
                    system=system_prompt,
                    model=action.model,
                    temperature=action.model_temperature,
                    max_tokens=action.model_max_tokens,
                    response_format={"type": "json_object"}
                )

                # Parse JSON response more efficiently
                # Remove markdown code blocks and extract JSON
                json_match = re.search(r'```(?:json)?\s*({.*?})\s*```', result_str, re.DOTALL)
                if json_match:
                    result_str = json_match.group(1)
                elif result_str.strip().startswith('{'):
                    # Already clean JSON
                    result_str = result_str.strip()
                else:
                    # Fallback: try to find JSON object in the string
                    json_match = re.search(r'{.*}', result_str, re.DOTALL)
                    result_str = json_match.group(0) if json_match else result_str.strip()
                    
                result_json = json.loads(result_str)
                return result_json

            else:
                result_str = await model_action.generate(
                    prompt=user_prompt,
                    stream=False,
                    system=system_prompt,
                    model=action.model,
                    temperature=action.model_temperature,
                    max_tokens=action.model_max_tokens,
                )

                return result_str

        except Exception as e:
            logger.error(f"Error in media check: {e}")
            return None



    @input_validator('feedback_content')
    def validate_feedback_content(
        value: str,
        session: InterviewSession,
        visitor: Optional[InteractWalker] = None,
        interview_action: Optional[Any] = None,
    ) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate feedback content is detailed and constructive.

        Args:
            value: The feedback content string to validate
            session: Interview session (for context)
            visitor: Walker for context (optional)
            interview_action: Interview action for context (optional)

        Returns:
            Tuple of (ValidationStatus, optional error message)
        """

        if not value or not isinstance(value, str):
            return ValidationStatus.INVALID, "Ask: Please share your feedback."

        # Remove extra whitespace and check minimum length
        value = value.strip()
        if len(value) < 10:
            return ValidationStatus.INVALID, "Ask: Please provide more detailed feedback about your experience"

        return ValidationStatus.VALID, None



    # search for report if report_details is provided
    @branch_function()
    def search_for_report(
        session: InterviewSession,
        visitor: Optional[InteractWalker] = None
    ) -> bool:
        """Search for completed reports matching the user's description.
        
        Returns True if matching reports found, False to continue to feedback.
        This helps users provide feedback on the correct completed work.
        """
        report_details = session.responses.get('report_details', '').lower()
        
        # Mock data - in production this would query completed reports database
        session.context['matching_reports'] = [
            {
                "id": "RL2FG12V", 
                "description": "At a residence in South Ruimveldt, a woman is repeatedly being verbally and physically abused by her partner. Neighbours have heard loud shouting, threats such as “ah gon kill you,” and sounds of slapping and objects being thrown late at night. This has been happening for weeks. People hearing the noise and frighten because this man does lose control. The failure to intervene despite obvious warning signs places the victim at high risk of serious injury or death. Urgent protective action is required.",
            },
            {
                "id": "RL1FG12W", 
                "description": "A deh one house in South Ruimveldt, a woman been gettin cuss out and beat regular by she partner. Neighbours hear plenty loud shouting, serious threats like “ah gon kill you”, an sounds like slap, beat, and tings fling ’bout late night. Dis na one-time thing — dis been goin on fuh weeks now. People round de area frighten because de man does lose control real bad. De fact that nobody ain’t step in yet, even when de signs clear, put de woman life in serious danger. She could get bad hurt or even dead if something ain’t do quick. Immediate action need fuh protect she and stop dis abuse before it turn into something worse.",
            }
        ]
        
        return True


    @branch_function()
    async def can_ask_for_media(session: InterviewSession, visitor: Optional[InteractWalker] = None) -> bool:
        """Check if the user can be asked for media using LLM reasoning."""
        
        import logging
        logger = logging.getLogger(__name__)

        user_input = ""
        if visitor and "utterance" in visitor:
            user_input = visitor.utterance
        feedback_content = session.responses.get('feedback_content', '')
        
        system_prompt = (
            "You are an assistant deciding if it's appropriate to ask for media (photos/videos/audio) "
            "based on feedback provided by a user. If the feedback describes a physical issue, "
            "damage, or something visual, it is appropriate to ask. "
            "Return a JSON object with a single boolean field 'should_ask'."
        )
        
        user_prompt = f"Feedback content: {feedback_content}\nUser input: {user_input}"

        result_json = await _get_model_action(user_prompt, system_prompt, json_response=True)
        should_ask = result_json.get("should_ask", False)

        return should_ask

        


    # # override directive by providing the similar reports found and ask user to select the report
    # @input_directive_override('report_details')
    # async def custom_report_details_directive(
    #     field_name: str,
    #     value: str,
    #     session: InterviewSession,
    #     interaction: Interaction,
    #     visitor: InteractWalker
    # ) -> Optional[Union[str, Tuple[str, str]]]:
    #     """Custom directive after report_details is answered."""
    #     matching_reports = session.context.get("matching_reports", [])
    #     if matching_reports:
    #         report_list = "\n".join([
    #             f"[{i+1}] Report ID: {report['id']} - {report['description'][:300]}..."
    #             for i, report in enumerate(matching_reports)
    #         ])
    #         return ("replace", f"Let the user know that you found {len(matching_reports)} reports that match their description. and ask them which report they want to provide feedback on. {report_list}")
        
    #     return None


    # @input_directive_override('feedback_content')
    # async def custom_feedback_content_directive(
    #     field_name: str,
    #     value: str,
    #     session: InterviewSession,
    #     interaction: Interaction,
    #     visitor: InteractWalker
    # ) -> Optional[Union[str, Tuple[str, str]]]:
    #     """Custom directive after incident_location is answered."""
    #     matching_reports = session.context.get("matching_reports")
    #     if matching_reports:
    #         report_str = ""
    #         for report in matching_reports:
    #             report_str += f"___\nReport ID: {report['id']}\n{report['description'][:300]}..."
            
    #         return ("replace", f"Let the user know that you found {len(matching_reports)} reports that match their description. and ask them if they want to continue with the interview. {report_str}")
    #     return None  # Use default directive


    # @on_confirmation('FeedbackInterviewInteractAction')
    # async def handle_feedback_confirmation(
    #     session: InterviewSession,
    #     visitor: InteractWalker,
    #     action: InteractAction
    # ) -> Optional[bool]:
    #     """Handle confirmation of feedback interview override.

    #     This handler is called when the user confirms the interview summary.
    #     If it returns True, the default transition to COMPLETED is skipped.

    #     Args:
    #         session: The current interview session
    #         visitor: The walker for accessing context and responding
    #         action: The InteractAction instance
    #     """
    #     import logging
    #     logger = logging.getLogger(__name__)
    #     logger.info("Feedback confirmation override triggered")
    #     responses_str = ""
    #     responses = session.responses
    #     for key, val in responses.items():
    #         if type(val) == list:
    #             val = "\n- ".join(val)
    #         responses_str += f"{key}: {val}\n"

    #     logger.warning("responses_str")
    #     logger.warning(responses_str)


    #     # Example: Add a custom message before completion
    #     confirmation_prompt = "Here's what I have so far:\n\n{responses}\n\nPrompt: Just let me know if everything looks good, or tell me what you'd like to change. You can also let me know if you'd like to cancel altogether."
    #     await action.respond(visitor, directives=[confirmation_prompt.format(responses=responses_str)])

    #     # Return False to allow the default transition to COMPLETED and on_interview_complete to run
    #     # Return True if you want to fully take over the confirmation logic
    #     return False


    @on_interview_complete('FeedbackInterviewInteractAction')
    async def handle_feedback_completion(
        session: InterviewSession,
        visitor: InteractWalker,
        action: InteractAction
    ) -> None:
        """Handle completion of feedback interview.

        This handler is called when the report interview is completed.
        Process collected data, trigger downstream actions, or perform cleanup.

        Args:
            session: The completed interview session with all collected responses
            visitor: The walker for accessing context and responding
            action: The InteractAction instance (use action.respond() to send responses)
        """
        # Extract collected data
        report_details = session.responses.get('report_details', '')
        feedback_content = session.responses.get('feedback_content', '')
        selected_report_id = session.responses.get('selected_report_id', '')
        feedback_media = session.responses.get('feedback_media', '')

        # Log completion (in production, you might send notifications, create records, etc.)
        import logging
        logger = logging.getLogger(__name__)
        logger.info(
            f"Feedback interview completed:\n"
            f"Project details: {report_details}\n"
            f"Feedback content: {feedback_content}\n"
            f"Selected report ID: {selected_report_id}\n"
        )

        # Send completion message with context
        if selected_report_id:
            completion_message = f"Tell the user: Thank you for your feedback on report {selected_report_id}! Your input helps us improve our services."
        else:
            completion_message = "Tell the user: Thank you for your feedback! Your input helps us improve our services."
        
        await action.respond(visitor, directives=[completion_message])

        # Clean up the session after processing
        await session.cleanup()
