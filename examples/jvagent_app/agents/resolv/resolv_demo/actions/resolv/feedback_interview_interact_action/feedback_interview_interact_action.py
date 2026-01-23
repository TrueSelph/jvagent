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
            # Initial entry
            "User wants to give feedback",
            "User wants to submit feedback",
            "User wants to comment on a project",
            "User wants to follow up on a report",
            "User wants to provide feedback on a report they created",
            "User wants to evaluate service quality or contractor performance",
            
            # Providing details
            "User is providing feedback details",
            "User is answering feedback questions such as report details and feedback content",
            "User is giving feedback on a report they created",
            
            # Follow-up / update
            "User is providing an update or follow-up related to previously submitted feedback",
            "User is adding additional comments to existing feedback",
            "User wants to amend or supplement previously given feedback",
            
            # Revision/cancel/edit/confirm
            "User is revising, canceling, updating or confirming an active feedback.",
            "User wants to modify feedback that is currently being submitted",
            "User needs to change ratings or comments in an incomplete feedback form"
        ],
        description="Anchor statements for InteractRouter routing"
    )

    question_graph: List[Dict[str, Any]] = attribute(
        default_factory=lambda: [
            {
                "name": "report_details",
                "question": "Please describe the report or project you are providing feedback on. Include what happened and any relevant details.",
                "constraints": {
                    "description": "Detailed context about the report or project, including background, scope, and impact if applicable.",
                    "type": "string"
                },
                "branches": [
                    {
                        "condition": {"function": "find_similar_report"},
                        "target": "feedback_content"
                    }
                ],
                "default_next": "feedback_content",
                "required": False
            },
            {
                "name": "feedback_content",
                "question": "Please provide your feedback.",
                "constraints": {
                    "description": "Clear and complete feedback related to the issue or project described.",
                    "type": "string"
                },
                "required": True
            }
        ],
        description="List of question configurations defining the interview graph. Can be overridden in agent.yaml. "
    )


@branch_function('find_similar_report')
def find_similar_report(
    session: InterviewSession,
    visitor: InteractWalker
) -> bool:
    """Check if there are any similar reports.
    Returns id of similar report if found, None otherwise.
    """
    session.responses["similar_report"] = "R230580235"
    # description = session.responses.get('report_description', '').lower()
    # sensitive_keywords = ['abuse', 'assault', 'violence', 'threat', 'harassment']
    
    # # Use session.context to store analysis for later use
    # has_sensitive = any(keyword in description for keyword in sensitive_keywords)
    # session.context['contains_sensitive_keywords'] = has_sensitive
    
    return True



@input_validator('feedback_content')
def validate_feedback_content(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the feedback content is not empty.

    Args:
        value: The feedback content string to validate
        session: Interview session (for context)

    Returns:
        Tuple of (ValidationStatus, optional error message)
    """

    if not value or not isinstance(value, str):
        return ValidationStatus.INVALID, "Ask: Please provide their feedback"

    return ValidationStatus.VALID, None



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
    similar_report = session.responses.get('similar_report', '')

    # Log completion (in production, you might send notifications, create records, etc.)
    import logging
    logger = logging.getLogger(__name__)
    logger.info(
        f"Feedback interview completed:\n report_details: {report_details}\n feedback_content: {feedback_content}\n similar_report: {similar_report}"
    )

    # Send completion message
    completion_message = (
        f"Tell the user: Thank you for your feedback!"
    )
    await action.respond(visitor, directives=[completion_message])

    # Clean up the session after processing
    await session.cleanup()
