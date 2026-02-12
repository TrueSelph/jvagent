"""Feedback interview for feedback submission."""
from __future__ import annotations
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import re
from datetime import datetime

from jvagent.action.interview import (
    InterviewInteractAction,
    input_context_provider,
    input_directive_override,
    input_review_override,
    input_handler,
    input_validator,
    branch_function,
    on_interview_complete,
)
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.foundation.enums import ValidationStatus
from jvagent.memory import Interaction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.interact.base import InteractAction
from jvspatial.core.annotations import attribute


logger = logging.getLogger(__name__)


# # input review override
# @input_review_override()
# def adapt_review(
#     session: InterviewSession,
#     data: Dict[str, Any],
#     visitor: Optional[InteractWalker] = None,
#     interview_action: Optional[Any] = None,
# ) -> Dict[str, Any]:
#     """Omit or format values shown in the Review state."""
#     result: Dict[str, Any] = {}
#     for field_name, value in data.items():

#         if field_name in ["selected_report_id"]:
#             continue
#         elif (
#             value is None
#             or value == ""
#             or (isinstance(value, str) and value.strip().lower() in ("n/a", "na"))
#         ):
#             continue
#         elif field_name == "feedback_media" and value:
#             media_links = ""
#             for link in value:
#                 media_links += f"{link}\n"
#             result[field_name] = media_links
#         else:
#             result[field_name] = value

#     return result



# input context 
@input_context_provider()
async def get_matching_reports(
    session: InterviewSession, visitor: InteractWalker
) -> Dict[str, Any]:
    """Provide matching reports dynamically."""
    matching_reports = session.context.get("matching_reports", [])
    if not matching_reports:
        logger.warning("No matching reports found in session context")
        return {}

    report_list = []
    for i, report in enumerate(matching_reports):
        report_list.append(
            f"Report ID: {report['id']} - {report['description'][:200]}..."
        )

    return {
        "reports": "\n".join(report_list),
        "note": "Please select the report ID (not the number) that matches the report you are providing your feedback on.",
    }




# branch function
@branch_function()
def search_for_report(
    session: InterviewSession, visitor: Optional[InteractWalker] = None
) -> bool:
    """Search for completed reports matching the user's description."""
    report_details = session.context.get("matching_reports")

    if report_details:
        return True

    return False


@branch_function()
async def can_ask_for_media(
    session: InterviewSession, visitor: Optional[InteractWalker] = None
) -> bool:
    """Check if the user can be asked for media using LLM reasoning."""

    logger.warning("can_ask_for_media: Checking if media can be asked for")
    feedback_content = session.responses.get("feedback_content", "")

    if not feedback_content:
        logger.warning("can_ask_for_media: No feedback content yet")
        return False

    system_prompt = (
        "You are an assistant deciding if it's appropriate to ask for media (photos/videos/audio) "
        "based on feedback provided by a user. If the feedback describes a physical issue, "
        "damage, or something visual, it is appropriate to ask. "
        "Return a JSON object with a single boolean field 'should_ask'."
    )

    user_prompt = f"Feedback content: {feedback_content}"

    try:
        # Standard pattern for LLM in branch function
        # Using session.interview_action (if available) or the visitor
        interview_action = getattr(session, "interview_action", None)
        if interview_action:
            model_action = await interview_action.get_model_action()
            if model_action:
                result_str = await model_action.generate(
                    prompt=user_prompt,
                    stream=False,
                    system=system_prompt,
                    model=interview_action.config.model.model,
                    temperature=interview_action.config.model.model_temperature,
                    max_tokens=interview_action.config.model.model_max_tokens,
                    response_format={"type": "json_object"},
                )

                json_match = re.search(
                    r"```(?:json)?\s*({.*?})\s*```", result_str, re.DOTALL
                )
                if json_match:
                    result_str = json_match.group(1)
                elif result_str.strip().startswith("{"):
                    result_str = result_str.strip()
                else:
                    json_match = re.search(r"{.*}", result_str, re.DOTALL)
                    result_str = (
                        json_match.group(0) if json_match else result_str.strip()
                    )

                result_json = json.loads(result_str)
                if result_json and isinstance(result_json, dict):
                    should_ask = result_json.get("should_ask", False)
                    return bool(should_ask)
    except Exception as e:
        logger.error(f"Error in can_ask_for_media: {e}")

    return False


class FeedbackInterviewInteractAction(InterviewInteractAction):
    """Feedback Interview action is used to create feedback for incidents and projects.

    This is a concrete implementation of InterviewInteractAction that defines
    a specific interview flow. Sessions are identified by
    interview_type='FeedbackInterviewInteractAction' and attached to Conversation nodes.

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

    description: str = (
        "Feedback Interview action is used to create feedback for incidents and projects."
    )

    # REQUIRED when using InteractRouter: Anchors for intelligent routing
    # Must cover both initial entry and intermediate states (when answering questions)
    anchors: List[str] = attribute(
        default_factory=lambda: [
            "User wants to provide feedback on a completed report or project",
            "User is giving feedback about work that was done",
            "User is sharing photos or evidence of completed work for feedback",
            # Note: Standard anchors (cancellation, correction, review, etc.)
            # are automatically merged with these implementation-specific anchors
        ],
        description="Anchor statements for InteractRouter routing",
    )

    question_graph: List[Dict[str, Any]] = attribute(
        default_factory=lambda: [
            {
                "name": "feedback_content",
                "question": "Please share your feedback.",
                "constraints": {
                    "description": "Full details about the feedback the user wants to provide.",
                    "instruction": "Feedback content is not a request to give a feedback. It is just a content of feedback.",
                    "type": "string",
                },
                "branches": [
                    {
                        "condition": {"function": "can_ask_for_media"},
                        "target": "feedback_media",
                    }
                ],
                "default_next": "report_details",
                "required": True,
            },
            {
                "name": "feedback_media",  # capture media if user provides it, do not ask for media
                "question": "Do you have any media to upload along with your feedback?",
                "constraints": {
                    "description": "Media of feedback uploaded by user.",
                    "type": "list",
                    "data_input_field": "whatsapp_media",
                },
                "default_next": "report_details",
                "required": True,
            },
            {
                "name": "report_details",
                "question": "Please describe the report or issue you want to provide feedback about.",
                "constraints": {
                    "description": "Details about the report or issue the user wants to provide feedback about. They can skip this to give feedback on the project.",
                    "instruction": "Details is about an existing report or issue that the user wants to provide feedback about. User can skip this question if they want to give feedback on the project.",
                    "type": "string",
                },
                "branches": [
                    {
                        "condition": {"function": "search_for_report"},
                        "target": "selected_report_id",
                    }
                ],
                # "default_next": "REVIEW",
                "required": False,
            },
            {
                "name": "selected_report_id",
                "input_context_provider": "get_matching_reports",
                "question": "I found multiple completed reports that match your description. Please select which one you want to provide feedback about:",
                "constraints": {
                    "description": "The correct report id based on the report details from the list of matching reports the user selected. return the report id(not the index) for the report selected.",
                    "instruction": "return the report id(not the index) for the report selected.",
                    "type": "integer",
                },
                # "default_next": "REVIEW",
                "required": True,
            },
        ],
        description="List of question configurations defining the interview graph. Can be overridden in agent.yaml. "
                    "Supports conditional branching via 'branches' and 'default_next'. "
                    "Enhanced condition operators are supported (==, !=, >, >=, <, <=, in, contains, exists, matches). "
                    "Example: {\"condition\": {\"question\": \"age\", \"operator\": \">=\", \"value\": 18}, \"target\": \"next_question\"} "
                    "Handlers, validators, and directive overrides can be registered via decorators "
                    "(@input_handler, @input_validator, @input_directive_override) or specified as string "
                    "references in constraints (input_handler, input_validator)."
    )



# directive override
@input_directive_override("report_details")
async def custom_report_details_directive(
    field_name: str,
    value: str,
    session: InterviewSession,
    interaction: Interaction,
    visitor: InteractWalker,
    interview_action: Optional[Any] = None,
) -> Optional[Union[str, Tuple[str, str]]]:
    """Custom directive after report_details is answered."""
    matching_reports = session.context.get("matching_reports", [])

    if matching_reports:
        report_list = "\n".join(
            [
                f"Report ID: {report['id']} - {report['description'][:300]}..."
                for i, report in enumerate(matching_reports)
            ]
        )

        return (
            "replace",
            f"Tell the user: I found {len(matching_reports)} reports that match your description. Please select which report you want to provide feedback on:\n{report_list}",
        )

    return (
        "replace",
        f"Tell the user: please select a report below •⁠  Report ID: 211 - Incident at a residence in South Ruimveldt involving abuse.\n Report ID: 223 - Incident at a house in South Ruimveldt involving abuse.",
    )

    return None



# input validator 
@input_validator("feedback_content")
def validate_feedback_content(
    value: str,
    session: InterviewSession,
    visitor: Optional[InteractWalker] = None,
    interview_action: Optional[Any] = None,
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate feedback content is detailed and constructive."""

    if not value or not isinstance(value, str):
        return ValidationStatus.INVALID, "Ask: Please share your feedback."

    value = value.strip()
    if len(value) < 10:
        return (
            ValidationStatus.INVALID,
            "Ask: Please provide more detailed feedback about your experience",
        )
    return ValidationStatus.VALID, None


@input_validator("report_details")
def validate_report_details(
    value: str,
    session: InterviewSession,
    visitor: Optional[InteractWalker] = None,
    interview_action: Optional[Any] = None,
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate report details are detailed and constructive."""

    if value:
        session.context["matching_reports"] = [
            {
                "id": 211,
                "description": "At a residence in South Ruimveldt, a woman is repeatedly being verbally and physically abused by her partner. Neighbours have heard loud shouting, threats such as “ah gon kill you,” and sounds of slapping and objects being thrown late at night. This has been happening for weeks. People hearing the noise and frighten because this man does lose control. The failure to intervene despite obvious warning signs places the victim at high risk of serious injury or death. Urgent protective action is required.",
            },
            {
                "id": 223,
                "description": "A deh one house in South Ruimveldt, a woman been gettin cuss out and beat regular by she partner. Neighbours hear plenty loud shouting, serious threats like “ah gon kill you”, an sounds like slap, beat, and tings fling ’bout late night. Dis na one-time thing — dis been goin on fuh weeks now. People round de area frighten because de man does lose control real bad. De fact that nobody ain’t step in yet, even when de signs clear, put de woman life in serious danger. She could get bad hurt or even dead if something ain’t do quick. Immediate action need fuh protect she and stop dis abuse before it turn into something worse.",
            },
        ]
        
    return ValidationStatus.VALID, None

@input_validator("selected_report_id")
def validate_selected_report_id(
    value: int,
    session: InterviewSession,
    visitor: Optional[InteractWalker] = None,
    interview_action: Optional[Any] = None,
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate selected report ID matches one of the available reports."""
    if not value:
        return (
            ValidationStatus.INVALID,
            "Ask: Please select a report ID from the list provided",
        )

    # Convert to integer
    try:
        report_id = int(value)
    except (ValueError, TypeError):
        return ValidationStatus.INVALID, "Ask: Please enter a valid numeric report ID"

    if report_id <= 0:
        return ValidationStatus.INVALID, "Ask: Report ID must be a positive number"

    matching_reports = session.context.get("matching_reports", [])
    if not matching_reports:
        return ValidationStatus.INVALID, "Ask: No reports available for selection"

    available_ids = [report["id"] for report in matching_reports]
    if report_id not in available_ids:
        return (
            ValidationStatus.INVALID,
            f"Ask: Please select a valid report ID from: {', '.join(map(str, available_ids))}",
        )

    # Store the validated integer ID back in the session if needed
    session.context["selected_report_id"] = report_id

    return ValidationStatus.VALID, None



@on_interview_complete('FeedbackInterviewInteractAction')
async def handle_feedback_completion(
    session: InterviewSession,
    visitor: InteractWalker,
    action: InteractAction
) -> None:
    """Handle completion of feedback interview."""
    # report_details = session.responses.get('report_details', '')
    # feedback_content = session.responses.get('feedback_content', '')
    # selected_report_id = session.responses.get('selected_report_id', '')
    # feedback_media = session.responses.get('feedback_media', '')

    # logger.info(
    #     f"\n\nFeedback interview completed:\n"
    #     f"Project details: {report_details}\n"
    #     f"Feedback content: {feedback_content}\n"
    #     f"Selected report ID: {selected_report_id}\n"
    #     f"Media files: {feedback_media}\n"
    # )

    # resolv_api_action = await action.get_action("ResolvAPIAction")
    completion_message = "Tell the user: Sorry I was unable to submit your feedback. Please try again later!"
    # if resolv_api_action:
    #     result = await resolv_api_action.submit_comment(
    #         content=feedback_content,
    #         report_id=selected_report_id,
    #         attachments=feedback_media
    #     )
    #     logger.warning("resolv_api_action.submit_comment")
    #     logger.warning(result)
    #     if result:
    #         completion_message = "Tell the user: Thank you for your feedback! Your input helps us improve our services."
    # else:
    #     logger.error("ResolvAPIAction not found for feedback submission")

    await action.respond(visitor, directives=[completion_message])
    await session.cleanup()


