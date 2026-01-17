"""Report interview for report submission."""

import re
from typing import Any, Dict, List, Optional, Tuple, Union

from jvagent.action.interview import (
    InterviewInteractAction,
    input_handler,
    input_validator,
    input_directive_override,
    on_interview_complete,
)
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.foundation.enums import ValidationStatus
from jvagent.memory import Interaction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.interact.base import InteractAction
from jvspatial.core.annotations import attribute


class ReportInterviewInteractAction(InterviewInteractAction):
    """Report interview for report submission.

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

    description: str = "Report interview action for report submission"

    # DSPy Integration
    use_dspy: bool = attribute(
        default=True,
        description="Use DSPy module for classification (enables optimization via DSPy teleprompters)",
    )

    # REQUIRED when using InteractRouter: Anchors for intelligent routing
    # Must cover both initial entry and intermediate states (when answering questions)
    anchors: List[str] = attribute(
        default_factory=lambda: [
            # Initial entry - starting report
            "User wants to report an incident",
            "User wants to submit an incident report",
            # Intermediate state - answering questions
            "User is providing or answering report questions",
            "User is completing report or providing availability",
            # Revision/edit - changing previously provided information
            "User is revising, editing, or updating incident report information",
        ],
        description="Anchor statements for InteractRouter routing",
    )

    question_graph: List[Dict[str, Any]] = attribute(
        default_factory=lambda: [
            {
                "name": "report_description",
                "question": "Describe the incident you'd like to report in a single message.",
                "constraints": {
                    "description": "A full description of the incident or grievance being reported. Capture only what happened, not requests or opinions.",
                    "type": "string",
                },
                "required": True,
            },
            {
                "name": "report_location",
                "question": "What is the exact address where the incident occurred, or can you share a WhatsApp location pin?",
                "constraints": {
                    "description": "The precise location of the incident, including street and area name. Ignore vague references such as 'my area' or 'nearby'.",
                    "type": "string",
                },
                "required": True,
            },
            {
                "name": "report_media",
                "question": "Please upload any images of the incident if you have them.",
                "constraints": {
                    "description": "Images of the incident uploaded via WhatsApp media.",
                    "type": "list",
                    "data_input_field": "whatsapp_media",
                },
                "required": False,
            },
            {
                "name": "is_sensitive",
                "question": "I noticed that the report includes sensitive information. Would you like to keep it private?",
                "constraints": {
                    "description": "Indicates whether the user wants the report marked as private.",
                    "instructions": "Only return a value if the user explicitly answers this question.",
                    "type": "string",
                    "options": ["yes", "no"],
                },
                "required": True,
                "branches": [
                    {
                        "condition": {"op": "equals", "value": "yes"},
                        "target": "REVIEW"  # Skip to review/confirmation when sensitive
                    }
                ],
                # Continue normally if "no"
            },
            {
                "name": "reporting_on_behalf",
                "question": "Are you submitting this report on behalf of someone else?",
                "constraints": {
                    "description": "Determines whether the report is being filed for another individual.",
                    "instructions": "Do not infer—only extract if explicitly stated.",
                    "type": "string",
                    "options": ["yes", "no"],
                },
                "required": True,
                "branches": [
                    {
                        "condition": {"op": "equals", "value": "yes"},
                        "target": "stakeholder_name"
                    }
                ],
                "default_next": "reporter_name"
            },
            {
                "name": "stakeholder_name",
                "question": "What is the full name of the person you're reporting on behalf of?",
                "constraints": {
                    "description": "Full legal name of the person the report concerns.",
                    "type": "string",
                },
                "required": True,
                "default_next": "stakeholder_address"
            },
            {
                "name": "stakeholder_address",
                "question": "What is the address of the person you're reporting on behalf of?",
                "constraints": {
                    "description": "Residential address of the stakeholder.",
                    "type": "string",
                },
                "required": True,
                "default_next": "stakeholder_phone"
            },
            {
                "name": "stakeholder_phone",
                "question": "What is the phone number of the person you're reporting on behalf of?",
                "constraints": {
                    "description": "Contact number of the stakeholder.",
                    "instructions": "If the user declines, mark as N/A.",
                    "type": "string",
                },
                "required": True,
                "default_next": "reporter_name"
            },
            {
                "name": "reporter_name",
                "question": "What is your full name?",
                "constraints": {
                    "description": "The full name of the person submitting the report.",
                    "type": "string",
                },
                "required": True,
                "default_next": "reporter_address"
            },
            {
                "name": "reporter_address",
                "question": "What is your residential address?",
                "constraints": {
                    "description": "The home address of the person submitting the report, not the incident location.",
                    "type": "string",
                },
                "required": True
            }
        ],
        description="List of question configurations defining the interview graph. Can be overridden in agent.yaml. "
                    "Supports conditional branching via 'branches' and 'default_next'. "
                    "Handlers, validators, and directive overrides can be registered via decorators "
                    "(@input_handler, @input_validator, @input_directive_override) or specified as string "
                    "references in constraints (input_handler, input_validator)."
    )

@input_validator('report_description')
def validate_report_description(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the report description is not empty.

    Args:
        value: The name string to validate
        session: Interview session (for context)

    Returns:
        Tuple of (ValidationStatus, optional error message)
    """

    if not value or not isinstance(value, str):
        return ValidationStatus.INVALID, "Ask: Please provide a description of the report"

    # Remove extra whitespace
    value = value.strip()

    # Check minimum length
    if len(value) < 10:
        return (
            ValidationStatus.INVALID,
            "Ask: Please provide a more detailed description of the report",
        )

    return ValidationStatus.VALID, None


@input_validator("report_location")
def validate_report_location(
    value: str, session: InterviewSession
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the report location is not empty.

    Args:
        value: The location string to validate
        session: Interview session (for context)

    Returns:
        Tuple of (ValidationStatus, optional error message)
    """

    if not value or not isinstance(value, str):
        return ValidationStatus.INVALID, "Ask: Please provide the location of the report"

    return ValidationStatus.VALID, None


# @input_validator('report_media')
# def validate_report_media(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
#     """Validate that the report media is not empty.

#     Args:
#         value: The media string to validate
#         session: Interview session (for context)

#     Returns:
#         Tuple of (ValidationStatus, optional error message)
#     """

#     if not value or not isinstance(value, str):
#         return ValidationStatus.INVALID, "Ask: Please provide the media of the report"

#     return ValidationStatus.VALID, None


# @input_validator('is_sensitive')
# def validate_is_sensitive(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
#     """Validate that the is sensitive is either yes or no.

#     Args:
#         value: The is sensitive string to validate
#         session: Interview session (for context)

#     Returns:
#         Tuple of (ValidationStatus, optional error message)
#     """
#     if not value or not isinstance(value, str):
#         return ValidationStatus.INVALID, "Ask: Please indicate whether the report is sensitive"

#     # Remove extra whitespace
#     value = value.strip()

#     # Check for valid options
#     if value not in ["yes", "no"]:
#         return ValidationStatus.INVALID, "Ask: Please indicate whether the report is sensitive"

#     return ValidationStatus.VALID, None


@input_validator("reporting_on_behalf")
def validate_reporting_on_behalf(
    value: str, session: InterviewSession
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the reporting on behalf is either yes or no.

    Args:
        value: The reporting on behalf string to validate
        session: Interview session (for context)

    Returns:
        Tuple of (ValidationStatus, optional error message)
    """
    if not value or not isinstance(value, str):
        return (
            ValidationStatus.INVALID,
            "Ask: Please indicate whether you are reporting on behalf of someone else",
        )

    # Remove extra whitespace
    value = value.strip()

    # Check for valid options
    if value not in ["yes", "no"]:
        return (
            ValidationStatus.INVALID,
            "Ask: Please indicate whether you are reporting on behalf of someone else",
        )

    return ValidationStatus.VALID, None


@input_validator("stakeholder_name")
def validate_stakeholder_name(
    value: str, session: InterviewSession
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the stakeholder name is not empty.

    Args:
        value: The stakeholder name string to validate
        session: Interview session (for context)

    Returns:
        Tuple of (ValidationStatus, optional error message)
    """
    if not value or not isinstance(value, str):
        return ValidationStatus.INVALID, "Ask: Please provide the name of the stakeholder"

    # Remove extra whitespace
    value = value.strip()

    # Split by spaces and check for at least two parts (first and last name)
    name_parts = value.split()
    if len(name_parts) < 2:
        return ValidationStatus.INVALID, "Ask: Please provide both your first and last name"

    # Check that each part has at least 2 characters
    for part in name_parts:
        if len(part) < 2:
            return (
                ValidationStatus.INVALID,
                "Tell the user: Each name part should be at least 2 characters long",
            )

    # Check for valid characters (letters, hyphens, apostrophes)
    if not re.match(r"^[a-zA-Z\s\-\']+$", value):
        return (
            ValidationStatus.INVALID,
            "Tell the user: Name should only contain letters, spaces, hyphens, and apostrophes",
        )

    return ValidationStatus.VALID, None


@input_validator("stakeholder_address")
def validate_stakeholder_address(
    value: str, session: InterviewSession
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the stakeholder address is not empty.

    Args:
        value: The stakeholder address string to validate
        session: Interview session (for context)

    Returns:
        Tuple of (ValidationStatus, optional error message)
    """
    if not value or not isinstance(value, str):
        return ValidationStatus.INVALID, "Ask: Please provide the address of the stakeholder"

    # Remove extra whitespace
    value = value.strip()

    return ValidationStatus.VALID, None


@input_validator("stakeholder_phone")
def validate_stakeholder_phone(
    value: str, session: InterviewSession
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the stakeholder phone is not empty.

    Args:
        value: The stakeholder phone string to validate
        session: Interview session (for context)

    Returns:
        Tuple of (ValidationStatus, optional error message)
    """
    if not value or not isinstance(value, str):
        return ValidationStatus.INVALID, "Ask: Please provide the contact number of the stakeholder"

    # Remove extra whitespace
    value = value.strip()

    # Check for valid phone number format
    if not re.match(r"^\d{10}$", value):
        return (
            ValidationStatus.INVALID,
            "Tell the user: Please provide a valid 10-digit phone number",
        )

    return ValidationStatus.VALID, None


@input_validator("reporter_name")
def validate_reporter_name(
    value: str, session: InterviewSession
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the reporter name is not empty.

    Args:
        value: The reporter name string to validate
        session: Interview session (for context)

    Returns:
        Tuple of (ValidationStatus, optional error message)
    """
    if not value or not isinstance(value, str):
        return ValidationStatus.INVALID, "Ask: Please provide the name of the reporter"

    # Remove extra whitespace
    value = value.strip()

    # Split by spaces and check for at least two parts (first and last name)
    name_parts = value.split()
    if len(name_parts) < 2:
        return (
            ValidationStatus.INVALID,
            "Ask: Please provide both the first and last name of the reporter",
        )

    # Check that each part has at least 2 characters
    for part in name_parts:
        if len(part) < 2:
            return (
                ValidationStatus.INVALID,
                "Tell the user: Each name part should be at least 2 characters long",
            )

    # Check for valid characters (letters, hyphens, apostrophes)
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
    """Validate that the reporter address is not empty.

    Args:
        value: The reporter address string to validate
        session: Interview session (for context)

    Returns:
        Tuple of (ValidationStatus, optional error message)
    """
    if not value or not isinstance(value, str):
        return ValidationStatus.INVALID, "Ask: Please provide the address of the reporter"

    # Remove extra whitespace
    value = value.strip()

    return ValidationStatus.VALID, None








@on_interview_complete("ReportInterviewInteractAction")
async def handle_report_completion(
    session: InterviewSession, visitor: InteractWalker, action: InteractAction
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
    report_description = session.responses.get("report_description", "")
    report_location = session.responses.get("report_location", "")
    incident_images = session.responses.get("incident_images", "")
    is_sensitive = session.responses.get("is_sensitive", "")
    reporting_on_behalf = session.responses.get("reporting_on_behalf", "")
    stakeholder_name = session.responses.get("stakeholder_name", "")
    stakeholder_address = session.responses.get("stakeholder_address", "")
    stakeholder_phone = session.responses.get("stakeholder_phone", "")
    reporter_name = session.responses.get("reporter_name", "")
    reporter_address = session.responses.get("reporter_address", "")

    # Log completion (in production, you might send notifications, create records, etc.)
    import logging

    logger = logging.getLogger(__name__)
    logger.info(
        f"Report interview completed:\n description: {report_description}\n location: {report_location}\n incident_images: {incident_images}\n is_sensitive: {is_sensitive}\n reporting_on_behalf: {reporting_on_behalf}\n stakeholder_name: {stakeholder_name}\n stakeholder_address: {stakeholder_address}\n stakeholder_phone: {stakeholder_phone}\n reporter_name: {reporter_name}\n reporter_address: {reporter_address}"
    )

    # Send completion message
    completion_message = (
        f"Tell the user: Thank you, {reporter_name}! Your report for jvagent training is complete. "
    )
    await action.respond(visitor, directives=[completion_message])

    # Clean up the session after processing
    await session.cleanup()
