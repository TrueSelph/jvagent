"""Report interview for report submission."""

import re
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

    # DSPy Integration
    use_dspy: bool = attribute(
        default=True,
        description="Use DSPy module for classification (enables optimization via DSPy teleprompters)"
    )

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
                "name": "report_details",
                "question": "Please describe the report or issue you want to provide feedback about.",
                "constraints": {
                    "description": "Details about the report or issue the user wants to provide feedback about.",
                    "type": "string"
                },
                "branches": [
                    {
                        "condition": {"function": "search_for_report"},
                        "target": "select_report_id"
                    }
                ],
                "default_next": "feedback_content",
                "required": False
            },
            {
                "name": "select_report_id",
                "question": "I found multiple completed reports that match your description. Please select which one you want to provide feedback about:",
                "constraints": {
                    "description": "The correct report id selected from the list of matching the user choice.",
                    "type": "int"
                },
                "default_next": "feedback_content",
                "required": False
            },
            {
                "name": "feedback_content",
                "question": "Please share your feedback.",
                "constraints": {
                    "description": "Full details about the feedback the user wants to provide.",
                    "type": "string"
                },
                "default_next": "REVIEW",
                "required": True
            },
            {
                "name": "feedback_media", # capture media if user provides it, do not ask for media
                "question": "Do you have any media to upload?",
                "constraints": {
                    "description": "Media of feedback uploaded via WhatsApp media.",
                    "type": "list",
                    "data_input_field": "whatsapp_media",
                },
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


@input_validator('feedback_content')
def validate_feedback_content(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate feedback content is detailed and constructive.

    Args:
        value: The feedback content string to validate
        session: Interview session (for context)

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
@branch_function('search_for_report')
def search_for_report(
    session: InterviewSession,
    visitor: InteractWalker
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
            "title": "Pothole repair completed on Main Street",
        },
        {
            "id": "RL1FG12W", 
            "title": "Street light installation finished on Oak Avenue",
        }
    ]
    
    return True



# override directive by providing the similar reports found and ask user to select the report
@input_directive_override('report_details')
async def handle_report_details_response(
    field_name: str,
    value: str,
    session: InterviewSession,
    interaction: Interaction,
    visitor: InteractWalker
) -> Optional[Union[str, Tuple[str, str]]]:
    """Handle response to project details and show matching reports if found."""
    matching_reports = session.context.get("matching_reports", [])
    if matching_reports:
        report_list = "\n".join([
            f"[{i+1}] Report ID: {report['id']} - {report['title'][:100]}..."
            for i, report in enumerate(matching_reports)
        ])
        return ("replace", f"I found these reports that match your description:\n\n{report_list}\n\nPlease let me know which report you want to provide feedback on.")
    return None




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
    select_report_id = session.responses.get('select_report_id', '')
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
