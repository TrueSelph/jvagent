"""Report interview for report submission."""


import re
import logging
from typing import Any, Dict, List, Optional, Tuple, Union

from jvagent.action.interview import (
    InterviewInteractAction,
    input_handler,
    input_validator,
    input_directive_override,
    on_interview_complete,
    on_interview_cancelled,
    on_interview_review,
    branch_function,
    input_context_provider,
    input_review_override,
    InterviewSession,
    ValidationStatus,
)
from jvagent.memory import Interaction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.interact.base import InteractAction
from jvspatial.core.annotations import attribute

logger = logging.getLogger(__name__)

# directive override
@input_directive_override
async def custom_location_directive(
    field_name: str,
    value: str,
    session: InterviewSession,
    interaction: Interaction,
    visitor: InteractWalker,
    interview_action: Optional[Any] = None,
) -> Optional[Union[str, Tuple[str, str]]]:
    """Custom directive after incident_location is answered."""
    matching_reports = session.context.get("matching_reports")
    logger = logging.getLogger(__name__)
    logger.warning("custom_location_directive")
    if matching_reports:
        report_str = ""
        for report in matching_reports:
            report_str += (
                f"___\nReport ID: {report['id']}\n{report['description'][:300]}..."
            )

        return (
            "replace",
            f"Let the user know that you found {len(matching_reports)} reports that match their description. and ask them if they want to continue with the interview. {report_str}",
        )
    return None  # Use default directive


@input_directive_override
async def custom_continue_directive(
    field_name: str,
    value: str,
    session: InterviewSession,
    interaction: Interaction,
    visitor: InteractWalker,
    interview_action: Optional[Any] = None,
) -> Optional[Union[str, Tuple[str, str]]]:
    """Custom directive after continue_report is answered."""
    if value == "no":
        return ("replace", "Thank you for your time. Your report was cancelled.")
    return None  # Use default directive

# input validator 
@input_validator("incident_description")
def validate_incident_description(
    value: str,
    session: InterviewSession,
    visitor: Optional[InteractWalker] = None,
    interview_action: Optional[Any] = None,
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate incident description has sufficient detail."""

    if not value or not isinstance(value, str):
        return (
            ValidationStatus.INVALID,
            "Ask: Please provide a description of the incident",
        )

    value = value.strip()
    if len(value) < 10:
        return (
            ValidationStatus.INVALID,
            "Ask: Please provide a more detailed description of what happened",
        )
    return ValidationStatus.VALID, None


@input_validator("incident_location")
def validate_incident_location(
    value: str,
    session: InterviewSession,
    visitor: Optional[InteractWalker] = None,
    interview_action: Optional[Any] = None,
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate incident location is provided and specific."""

    if not value or not isinstance(value, str):
        return (
            ValidationStatus.INVALID,
            "Ask: Please provide the location where the incident occurred",
        )

    value = value.strip()
    if len(value) < 10:
        return ValidationStatus.INVALID, "Ask: Please provide a more specific location"
    return ValidationStatus.VALID, None


@input_validator("is_sensitive")
def validate_is_sensitive(
    value: str,
    session: InterviewSession,
    visitor: Optional[InteractWalker] = None,
    interview_action: Optional[Any] = None,
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the is sensitive is either yes or no."""
    if not value or not isinstance(value, str):
        return (
            ValidationStatus.INVALID,
            "Ask: Please indicate whether the report is sensitive",
        )

    value = value.strip()
    if value not in ["yes", "no"]:
        return (
            ValidationStatus.INVALID,
            "Ask: Please indicate whether the report is sensitive",
        )
    return ValidationStatus.VALID, None


@input_validator("reporting_on_behalf")
def validate_reporting_on_behalf(
    value: str,
    session: InterviewSession,
    visitor: Optional[InteractWalker] = None,
    interview_action: Optional[Any] = None,
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the reporting on behalf is either yes or no."""
    if not value or not isinstance(value, str):
        return (
            ValidationStatus.INVALID,
            "Ask: Please indicate whether you are reporting on behalf of someone else",
        )

    value = value.strip()
    if value not in ["yes", "no"]:
        return (
            ValidationStatus.INVALID,
            "Ask: Please indicate whether you are reporting on behalf of someone else",
        )
    return ValidationStatus.VALID, None


@input_validator("stakeholder_name")
def validate_stakeholder_name(
    value: str,
    session: InterviewSession,
    visitor: Optional[InteractWalker] = None,
    interview_action: Optional[Any] = None,
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the stakeholder name is not empty."""
    if not value or not isinstance(value, str):
        return (
            ValidationStatus.INVALID,
            "Ask: Please provide the name of the stakeholder",
        )

    value = value.strip()
    name_parts = value.split()
    if len(name_parts) < 2:
        return (
            ValidationStatus.INVALID,
            "Ask: Please provide both your first and last name",
        )

    for part in name_parts:
        if len(part) < 2:
            return (
                ValidationStatus.INVALID,
                "Tell the user: Each name part should be at least 2 characters long",
            )

    if not re.match(r"^[a-zA-Z\s\-\']+$", value):
        return (
            ValidationStatus.INVALID,
            "Tell the user: Name should only contain letters, spaces, hyphens, and apostrophes",
        )
    return ValidationStatus.VALID, None


@input_validator("stakeholder_address")
def validate_stakeholder_address(
    value: str,
    session: InterviewSession,
    visitor: Optional[InteractWalker] = None,
    interview_action: Optional[Any] = None,
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the stakeholder address is not empty."""
    if not value or not isinstance(value, str):
        return (
            ValidationStatus.INVALID,
            "Ask: Please provide the address of the stakeholder",
        )

    if len(value) < 10:
        return (
            ValidationStatus.INVALID,
            "Ask: Please provide the full address of the stakeholder",
        )

    value = value.strip()
    return ValidationStatus.VALID, None


@input_validator("stakeholder_phone")
def validate_stakeholder_phone(
    value: str,
    session: InterviewSession,
    visitor: Optional[InteractWalker] = None,
    interview_action: Optional[Any] = None,
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the stakeholder phone is not empty."""
    if not value or not isinstance(value, str):
        return (
            ValidationStatus.INVALID,
            "Ask: Please provide the contact number of the stakeholder",
        )

    value = value.strip()
    if not re.match(r"^\d{10}$", value):
        return (
            ValidationStatus.INVALID,
            "Tell the user: Please provide a valid 10-digit phone number",
        )
    return ValidationStatus.VALID, None


@input_validator("reporter_name")
def validate_reporter_name(
    value: str,
    session: InterviewSession,
    visitor: Optional[InteractWalker] = None,
    interview_action: Optional[Any] = None,
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the reporter name is not empty."""
    if not value or not isinstance(value, str):
        return (
            ValidationStatus.INVALID,
            "Ask: Please provide the name of the reporter",
        )

    value = value.strip()
    name_parts = value.split()
    if len(name_parts) < 2:
        return (
            ValidationStatus.INVALID,
            "Ask: Please provide both the first and last name of the reporter",
        )

    for part in name_parts:
        if len(part) < 2:
            return (
                ValidationStatus.INVALID,
                "Tell the user: Each name part should be at least 2 characters long",
            )

    if not re.match(r"^[a-zA-Z\s\-\']+$", value):
        return (
            ValidationStatus.INVALID,
            "Tell the user: Name should only contain letters, spaces, hyphens, and apostrophes",
        )
    return ValidationStatus.VALID, None


@input_validator("reporter_address")
def validate_reporter_address(
    value: str, session: InterviewSession
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the reporter address is not empty."""
    if not value or not isinstance(value, str):
        return ValidationStatus.INVALID, "Ask: Please provide your full address"

    if len(value) < 10:
        return ValidationStatus.INVALID, "Ask: Please provide your full address"

    value = value.strip()
    return ValidationStatus.VALID, None


# branch function
@branch_function("detect_sensitive_content")
def detect_sensitive_content(
    session: InterviewSession, visitor: Optional[InteractWalker] = None
) -> bool:
    """Detect if incident report contains sensitive content requiring privacy protection.

    Returns True to branch to privacy question, False to continue normal flow.
    Checks both description and media for sensitive content indicators.
    """
    description = session.responses.get("incident_description", "").lower()
    media = session.responses.get("incident_media")

    sensitive_keywords = [
        "abuse",
        "assault",
        "violence",
        "threat",
        "harassment",
        "domestic",
        "sexual",
    ]

    # Check description for sensitive keywords
    has_sensitive_text = any(keyword in description for keyword in sensitive_keywords)

    # Check for presence of media
    has_media = bool(media)

    return has_sensitive_text or has_media


@branch_function("check_for_similar_incidents")
def check_for_similar_incidents(
    session: InterviewSession, visitor: Optional[InteractWalker] = None
) -> bool:
    """Check for similar incident reports in the same location.

    Returns True if similar incidents found, triggering user confirmation.
    This helps prevent duplicate reports and informs users of existing issues.
    """
    location = session.responses.get("incident_location", "").lower()
    description = session.responses.get("incident_description", "").lower()

    session.context["matching_reports"] = [
        {
            "id": "RL2FG12V",
            "description": "At a residence in South Ruimveldt, a woman is repeatedly being verbally and physically abused by her partner. Neighbours have heard loud shouting, threats such as “ah gon kill you,” and sounds of slapping and objects being thrown late at night. This has been happening for weeks. People hearing the noise and frighten because this man does lose control. The failure to intervene despite obvious warning signs places the victim at high risk of serious injury or death. Urgent protective action is required.",
        },
        {
            "id": "RL1FG12W",
            "description": "A deh one house in South Ruimveldt, a woman been gettin cuss out and beat regular by she partner. Neighbours hear plenty loud shouting, serious threats like “ah gon kill you”, an sounds like slap, beat, and tings fling ’bout late night. Dis na one-time thing — dis been goin on fuh weeks now. People round de area frighten because de man does lose control real bad. De fact that nobody ain’t step in yet, even when de signs clear, put de woman life in serious danger. She could get bad hurt or even dead if something ain’t do quick. Immediate action need fuh protect she and stop dis abuse before it turn into something worse.",
        },
    ]

    # For demo purposes, always return True to show the flow
    # In production, this would query a database of existing reports
    return True

# review override 
@input_review_override
def adapt_feedback_review_for_display(
    session: InterviewSession,
    data: Dict[str, Any],
    visitor: Optional[InteractWalker] = None,
    interview_action: Optional[Any] = None,
) -> Dict[str, Any]:
    """Omit or format values shown in the Review state (display only; session unchanged).

    Applies only to ReportInterviewInteractAction in this module.
    """
    result: Dict[str, Any] = {}
    for field_name, value in data.items():

        if field_name in ["continue_report"]:
            continue
        elif (
            value is None
            or value == ""
            or (isinstance(value, str) and value.strip().lower() in ("n/a", "na"))
        ):
            continue
        elif field_name == "incident_media" and value:
            media_links = ""
            for link in value:
                media_links += f"{link}\n"
            result[field_name] = media_links
        else:
            result[field_name] = value

    return result


class ReportInterviewInteractAction(InterviewInteractAction):
    """Report Interview action is used to create reports.

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

    description: str = "Report Interview action is used to create reports."
    resolv_api_action: str = "ResolvAPIAction"

    # REQUIRED when using InteractRouter: Anchors for intelligent routing
    # Must cover both initial entry and intermediate states (when answering questions)
    anchors: List[str] = attribute(
        default_factory=lambda: [
            "User is reporting a new problem, hazard, or safety issue",
            "User needs to file a new complaint or incident report",
            # Note: Standard anchors (cancellation, correction, review, etc.)
            # are automatically merged with these implementation-specific anchors
        ],
        description="Anchor statements for InteractRouter routing",
    )

    question_graph: List[Dict[str, Any]] = attribute(
        default_factory=lambda: [
            {
                "name": "incident_description",
                "question": "Please describe the incident you want to report. Include what happened, when it occurred, and any other relevant details.",
                "constraints": {
                    "description": "A detailed description of the incident being reported, including facts about what happened without opinions or requests.",
                    "instruction": "The description should have details about what happened.",
                    "type": "string",
                },
                "required": True,
            },
            {
                "name": "incident_location",
                "question": "Where exactly did this incident occur? Please provide the specific address or location details.",
                "constraints": {
                    "description": "The exact location where the incident occurred, including street address, area name, or landmark. Must be specific, not vague references.",
                    "type": "string",
                },
                "branches": [
                    {
                        "condition": {"function": "check_for_similar_incidents"},
                        "target": "continue_report",
                    }
                ],
                "default_next": "incident_media",
                "required": True,
            },
            {
                "name": "continue_report",
                "question": "I found similar incident reports. Would you like to continue creating your new report?",
                "constraints": {
                    "description": "User's decision to continue with their new incident report despite similar existing reports.",
                    "type": "string",
                    "options": ["yes", "no"],
                },
                "branches": [
                    {
                        "condition": {"op": "equals", "value": "yes"},
                        "target": "incident_media",
                    },
                    {
                        "condition": {"op": "equals", "value": "no"},
                        "target": "CANCELLED",
                    },
                ],
                "required": True,
            },
            {
                "name": "incident_media",
                "question": "Do you have any photos or videos of the incident you'd like to include? You can upload them now or skip this step.",
                "constraints": {
                    "description": "Photos, videos, or other media evidence related to the incident.",
                    "type": "list",
                    "data_input_field": "whatsapp_media",
                },
                "branches": [
                    {
                        "condition": {"function": "detect_sensitive_content"},
                        "target": "is_sensitive",
                    }
                ],
                "default_next": "reporting_on_behalf",
                "required": False,
            },
            {
                "name": "is_sensitive",
                "question": "I noticed that the report includes sensitive information. Would you like to keep it private?",
                "constraints": {
                    "description": "User explicit request to keep the report private or not.",
                    "instruction": "Do not infer—only extract if explicitly stated.",
                    "type": "string",
                    "options": ["yes", "no"],
                },
                "branches": [
                    {
                        "condition": {"op": "equals", "value": "yes"},
                        "target": "REVIEW",
                    },
                    {
                        "condition": {"op": "equals", "value": "no"},
                        "target": "reporting_on_behalf",
                    },
                ],
                "required": True,
            },
            {
                "name": "reporting_on_behalf",
                "question": "Are you submitting this report on behalf of someone else?",
                "constraints": {
                    "description": "Determines whether the report is being filed for another individual.",
                    "instruction": "Do not infer—only extract if explicitly stated.",
                    "type": "string",
                    "options": ["yes", "no"],
                },
                "required": True,
                "branches": [
                    {
                        "condition": {"op": "equals", "value": "yes"},
                        "target": "stakeholder_name",
                    }
                ],
                "default_next": "reporter_name",
            },
            {
                "name": "stakeholder_name",
                "question": "What is the full name of the person you're reporting on behalf of?",
                "constraints": {
                    "description": "Full legal name of the person the report concerns.",
                    "instruction": "The name should not be the same as the name of the person submitting the report.",
                    "type": "string",
                },
                "required": True,
                "default_next": "stakeholder_address",
            },
            {
                "name": "stakeholder_address",
                "question": "What is the residential address of the person you're reporting on behalf of?",
                "constraints": {
                    "description": "Residential address of the stakeholder.",
                    "instruction": "The address should not be the same as the address of the person submitting the report.",
                    "type": "string",
                },
                "required": True,
                "default_next": "stakeholder_phone",
            },
            {
                "name": "stakeholder_phone",
                "question": "What is the phone number of the person you're reporting on behalf of?",
                "constraints": {
                    "description": "Contact number of the stakeholder.",
                    "instruction": "The phone number should not be the same as the phone number of the person submitting the report.",
                    "type": "string",
                },
                "required": True,
                "default_next": "reporter_name",
            },
            {
                "name": "reporter_name",
                "question": "What is the full name of the person submitting the report?",
                "constraints": {
                    "description": "The full name of the person submitting the report.",
                    "instruction": "The name should not be the same as the name of the person the report is being filed on behalf of.",
                    "type": "string",
                },
                "required": True,
                "default_next": "reporter_address",
            },
            {
                "name": "reporter_address",
                "question": "What is the residential address of the person submitting the report?",
                "constraints": {
                    "description": "The home address of the person submitting the report.",
                    "instruction": "The address should not be the incident location or the address of the person the report is being filed on behalf of.",
                    "type": "string",
                },
                "required": True,
            },
        ],
        description="List of question configurations defining the interview graph. Can be overridden in agent.yaml. "
        "Supports conditional branching via 'branches' and 'default_next'. "
        "Handlers, validators, and directive overrides can be registered via decorators "
        "(@input_handler, @input_validator, @input_directive_override) or specified as string "
        "references in constraints (input_handler, input_validator). "
        "Branch functions can be registered with @branch_function decorator for complex branching logic.",
    )



@on_interview_complete('ReportInterviewInteractAction')
async def handle_report_completion(
    session: InterviewSession,
    visitor: InteractWalker,
    action: InteractAction
) -> None:
    """Handle completion of report interview.

    This handler is called when the report interview is completed.
    Process collected data, trigger downstream actions, or perform cleanup.

    Args:
        session: The completed interview session with all collected responses
        visitor: The walker for accessing context and responding
        action: The InteractAction instance (use action.respond() to send responses)
    """
    # Extract collected data
    incident_description = session.responses.get('incident_description', '')
    incident_location = session.responses.get('incident_location', '')
    incident_media = session.responses.get('incident_media', '')
    is_sensitive = session.responses.get('is_sensitive', '')
    reporting_on_behalf = session.responses.get('reporting_on_behalf', '')
    stakeholder_name = session.responses.get('stakeholder_name', '')
    stakeholder_address = session.responses.get('stakeholder_address', '')
    stakeholder_phone = session.responses.get('stakeholder_phone', '')
    reporter_name = session.responses.get('reporter_name', '')
    reporter_address = session.responses.get('reporter_address', '')

    is_anonymous = False
    if is_sensitive == 'yes':
        is_anonymous = True

    # generated data 
    title = "default title" 
    generated_description = "default generated description"
    reporter_phone = visitor.user_id
    priority = "low"
    category_id=1
    ai_overview = "Incident Report R657224 documents a high-priority safety concern at 47 Main Street, where heavy construction equipment is being operated without proper safety barriers or signage near a public walkway. Reported by Jivas AI Agent for contact ID 395 on 28 January 2026. The absence of required protective measures poses a serious risk of injury to pedestrians and workers. Report remains open."

    import logging
    logger = logging.getLogger(__name__)

    logger.info(f"Incident description: {incident_description}")
    logger.info(f"Incident location: {incident_location}")
    logger.info(f"Incident media: {incident_media}")
    logger.info(f"Is sensitive: {is_anonymous}")
    logger.info(f"Reporting on behalf: {reporting_on_behalf}")
    logger.info(f"Stakeholder name: {stakeholder_name}")
    logger.info(f"Stakeholder address: {stakeholder_address}")
    logger.info(f"Stakeholder phone: {stakeholder_phone}")
    logger.info(f"Reporter name: {reporter_name}")
    logger.info(f"Reporter address: {reporter_address}")
    logger.info(f"Reporter phone: {reporter_phone}")
    logger.info(f"AI overview: {ai_overview}")

    

    # title = "Incident Report: Construction Safety Violation at 47 Main Street"
    # is_sensitive = True
    # generated_description = "On Monday, 27 January 2026 at approximately 2:15 PM, unsafe working conditions were observed at 47 Main Street. Heavy construction machinery is being operated in close proximity to an unprotected public footpath without installation of safety barriers, warning signs, cones, or flaggers. This violates standard construction safety protocols and creates a high risk of serious injury to passersby, especially vulnerable groups such as children and elderly persons. Immediate intervention and corrective action are strongly recommended to prevent potential accidents and ensure compliance with occupational health and safety regulations."
    # incident_description = "Heavy machinery operating unsafely near public walkway without barriers or signage at construction site."
    # # incident_media = []
    # priority = "high"
    # category_id = 28
    # reporting_on_behalf = "yes"
    # stakeholder_name = "John Doe"
    # stakeholder_address = "123 Main St"
    # stakeholder_phone = "5555555555"
    # reporter_name = "Jane Doe"
    # reporter_address = "123 Main St"
    # reporter_phone = "5926431530"
    # ai_overview = "Incident Report R657224 documents a high-priority safety concern at 47 Main Street, where heavy construction equipment is being operated without proper safety barriers or signage near a public walkway. Reported by Jivas AI Agent for contact ID 395 on 28 January 2026. The absence of required protective measures poses a serious risk of injury to pedestrians and workers. Report remains open."
    

    completion_message = (f"Tell the user: Sorry, {reporter_name}! I was unable to submit your report. Please try again later!")
    resolv_api_action = await action.get_action("ResolvAPIAction")
    if resolv_api_action:
        result = await resolv_api_action.submit_report(
            title=title,
            is_anonymous=is_anonymous,
            description=generated_description,
            original_description=incident_description,
            attachments=incident_media,
            priority=priority,
            category_id=category_id,
            reporting_on_behalf=reporting_on_behalf,
            stakeholder_name=stakeholder_name,
            stakeholder_address=stakeholder_address,
            stakeholder_phone=stakeholder_phone,
            reporter_name=reporter_name,
            reporter_phone=reporter_phone,
            reporter_address=reporter_address,
            ai_overview=ai_overview
        )
        
        logger.warning("Result: ")
        logger.warning(result)
        if result:
            completion_message = (f"Tell the user: Thank you, {reporter_name}! Your report for jvagent training is complete. ")
    else:
        logger.error("ResolvAPIAction not found for report submission")
    

    # Send completion message
    await action.respond(visitor, directives=[completion_message])

    # Clean up the session after processing
    await session.cleanup()
