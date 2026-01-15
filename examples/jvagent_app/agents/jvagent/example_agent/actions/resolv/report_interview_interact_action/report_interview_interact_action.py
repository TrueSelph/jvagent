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
from jvagent.action.interview.core.interview_session import InterviewSession
from jvagent.action.interview.core.enums import ValidationStatus
from jvagent.memory import Interaction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.interact.base import InteractAction
from jvspatial.core.annotations import attribute


class ReportInterviewInteractAction(InterviewInteractAction):
    """Report interview for report submission.

    This is a concrete implementation of InterviewInteractAction that defines
    a specific interview flow. Sessions are identified by
    interview_type='ReportInterviewInteractAction' and attached to Conversation nodes.

    The question_index can be overridden in agent.yaml to customize questions.

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
        description="Use DSPy module for classification (enables optimization via DSPy teleprompters)"
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
            "User wants to change, update, or correct their report description, location, media, anonymous report option, reporting_on_behalf, stakeholder_name, stakeholder_address, stakeholder_phone, reporter_name, or reporter_address.",
            "User is revising, editing, or updating incident report information",
        ],
        description="Anchor statements for InteractRouter routing"
    )

    question_index: List[Dict[str, Any]] = attribute(
        default_factory=lambda: [
            {
                "name": "report_description",
                "question": "Describe the incident you'd like to report in a single message.",
                "constraints": {
                    "description": "A full description of the incident or grievance being reported. Capture only what happened, not requests or opinions.",
                    "type": "string",
                },
                "required": True
            },
            {
                "name": "report_location",
                "question": "What is the exact address where the incident occurred, or can you share a WhatsApp location pin?",
                "constraints": {
                    "description": "The precise location of the incident, including street and area name. Ignore vague references such as 'my area' or 'nearby'.",
                    "type": "string",
                },
                "required": True
            },
            # {
            #     "name": "report_media",
            #     "question": "Please upload any images or videos you may have related to your report.",
            #     "constraints": {
            #         "description": "Images or videos related to the reported incident.",
            #         "instructions": "Only extract if the user explicitly provides media or clearly states they do not have or do not wish to provide any.",
            #         "type": "array",
            #     },
            #     "required": True
            # },
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
                        "condition": {"question": "is_sensitive", "equals": "yes"},
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
                "required": True
            },
            {
                "name": "stakeholder_name",
                "question": "What is the full name of the person you're reporting on behalf of?",
                "constraints": {
                    "description": "Full legal name of the person the report concerns.",
                    "type": "string",
                },
                "required": True,
                "conditional": {"reporting_on_behalf": "yes"}
            },
            {
                "name": "stakeholder_address",
                "question": "What is the address of the person you're reporting on behalf of?",
                "constraints": {
                    "description": "Residential address of the stakeholder.",
                    "type": "string",
                },
                "required": True,
                "conditional": {"reporting_on_behalf": "yes"}
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
                "conditional": {"reporting_on_behalf": "yes"}
            },
            {
                "name": "reporter_name",
                "question": "What is your full name?",
                "constraints": {
                    "description": "The full name of the person submitting the report.",
                    "type": "string",
                },
                "required": True
            },
            {
                "name": "reporter_address",
                "question": "What is your residential address?",
                "constraints": {
                    "description": "The home address of the person submitting the report, not the incident location.",
                    "type": "string",
                },
                "required": True,
                "conditional": {"reporting_on_behalf": "no"}
            }
        ],
        description="List of question configurations. Can be overridden in agent.yaml. "
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
        return ValidationStatus.INVALID, "Ask: Please provide a more detailed description of the report"

    return ValidationStatus.VALID, None


@input_validator('report_location')
def validate_report_location(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
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


@input_validator('is_sensitive')
def validate_is_sensitive(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the is sensitive is either yes or no.

    Args:
        value: The is sensitive string to validate
        session: Interview session (for context)

    Returns:
        Tuple of (ValidationStatus, optional error message)
    """
    if not value or not isinstance(value, str):
        return ValidationStatus.INVALID, "Ask: Please indicate whether the report is sensitive"

    # Remove extra whitespace
    value = value.strip()

    # Check for valid options
    if value not in ["yes", "no"]:
        return ValidationStatus.INVALID, "Ask: Please indicate whether the report is sensitive"

    return ValidationStatus.VALID, None


@input_validator('reporting_on_behalf')
def validate_reporting_on_behalf(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the reporting on behalf is either yes or no.

    Args:
        value: The reporting on behalf string to validate
        session: Interview session (for context)

    Returns:
        Tuple of (ValidationStatus, optional error message)
    """
    if not value or not isinstance(value, str):
        return ValidationStatus.INVALID, "Ask: Please indicate whether you are reporting on behalf of someone else"

    # Remove extra whitespace
    value = value.strip()

    # Check for valid options
    if value not in ["yes", "no"]:
        return ValidationStatus.INVALID, "Ask: Please indicate whether you are reporting on behalf of someone else"

    return ValidationStatus.VALID, None


@input_validator('stakeholder_name')
def validate_stakeholder_name(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
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
            return ValidationStatus.INVALID, "Tell the user: Each name part should be at least 2 characters long"

    # Check for valid characters (letters, hyphens, apostrophes)
    if not re.match(r'^[a-zA-Z\s\-\']+$', value):
        return ValidationStatus.INVALID, "Tell the user: Name should only contain letters, spaces, hyphens, and apostrophes"

    return ValidationStatus.VALID, None


@input_validator('stakeholder_address')
def validate_stakeholder_address(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
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


@input_validator('stakeholder_phone')
def validate_stakeholder_phone(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
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
    if not re.match(r'^\d{10}$', value):
        return ValidationStatus.INVALID, "Tell the user: Please provide a valid 10-digit phone number"

    return ValidationStatus.VALID, None


@input_validator('reporter_name')
def validate_reporter_name(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
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
        return ValidationStatus.INVALID, "Ask: Please provide both the first and last name of the reporter"

    # Check that each part has at least 2 characters
    for part in name_parts:
        if len(part) < 2:
            return ValidationStatus.INVALID, "Tell the user: Each name part should be at least 2 characters long"

    # Check for valid characters (letters, hyphens, apostrophes)
    if not re.match(r'^[a-zA-Z\s\-\']+$', value):
        return ValidationStatus.INVALID, "Tell the user: Name should only contain letters, spaces, hyphens, and apostrophes"

    return ValidationStatus.VALID, None


@input_validator('reporter_address')
def validate_reporter_address(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
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


@input_handler('available_times')
async def check_training_availability(
    raw_input: str,
    session: InterviewSession,
    interaction: Interaction
) -> str:
    """Process and autocorrect training time availability against hardcoded available times.

    This handler autocorrects partial inputs (e.g., "Monday at 9" -> "Monday 9:00 AM - 11:00 AM")
    or returns the original input if no match can be determined.

    Args:
        raw_input: Raw user input about availability
        session: Interview session (for context)
        interaction: Interaction node (can access interaction.user_id, interaction.utterance, etc.)

    Returns:
        Autocorrected availability string (full format) or original input if no match
    """

    # Hardcoded available training times
    AVAILABLE_TRAINING_TIMES = [
        "Monday 9:00 AM - 11:00 AM",
        "Monday 2:00 PM - 4:00 PM",
        "Wednesday 9:00 AM - 11:00 AM",
        "Wednesday 2:00 PM - 4:00 PM",
        "Friday 10:00 AM - 12:00 PM",
        "Saturday 9:00 AM - 12:00 PM",
    ]

    if not raw_input or not isinstance(raw_input, str):
        return raw_input

    user_input = raw_input.strip()

    # Normalize user input for comparison (case-insensitive, remove extra spaces)
    normalized_input = re.sub(r'\s+', ' ', user_input.lower())

    # First, check if input is already in correct format (idempotent check)
    # This handles cases where process_input is called multiple times
    for available_time in AVAILABLE_TRAINING_TIMES:
        normalized_available = re.sub(r'\s+', ' ', available_time.lower())
        if normalized_input == normalized_available:
            # Already in correct format
            session.context['matched_training_times'] = [available_time]
            await session.save()
            return available_time  # Return exact format

    # Also check if input starts with "Available:" (from previous processing)
    # This shouldn't happen, but handle it gracefully
    if user_input.startswith("Available:"):
        # Extract the time from "Available: Monday 9:00 AM - 11:00 AM"
        time_part = user_input.replace("Available:", "").strip()
        for available_time in AVAILABLE_TRAINING_TIMES:
            if time_part.lower() == available_time.lower():
                session.context['matched_training_times'] = [available_time]
                await session.save()
                return available_time

    # Try to autocorrect partial inputs
    matched_times = []
    for available_time in AVAILABLE_TRAINING_TIMES:
        normalized_available = re.sub(r'\s+', ' ', available_time.lower())

        # Extract day and times from available time
        day_match = False
        matched_day = None
        for day in ['monday', 'wednesday', 'friday', 'saturday']:
            if day in normalized_available:
                if day in normalized_input:
                    day_match = True
                    matched_day = day
                    break

        if not day_match:
            continue

        # Extract time information from available time
        # Format: "monday 9:00 am - 11:00 am"
        time_match = re.search(r'(\d+):(\d+)\s*(am|pm)\s*-\s*(\d+):(\d+)\s*(am|pm)', normalized_available)
        if not time_match:
            continue

        start_hour = int(time_match.group(1))
        start_min = int(time_match.group(2))
        start_period = time_match.group(3)
        end_hour = int(time_match.group(4))
        end_min = int(time_match.group(5))
        end_period = time_match.group(6)

        # Try to match user input against this time slot
        # User might say: "9", "9 am", "9:00", "9:00 am", "9-11", "9 am - 11 am", "at 9", "monday at 9", etc.

        # Check if user input mentions the start hour (flexible matching)
        # Look for the hour number in the input - be flexible with patterns
        hour_patterns = [
            rf'\b{start_hour}\b',  # Just the hour "9" (word boundary) - matches "9" in "monday at 9"
            rf'at\s+{start_hour}\b',  # "at 9" or "monday at 9"
            rf'{start_hour}\s*(am|pm)\b',  # "9 am" or "9 pm"
            rf'{start_hour}:00',  # "9:00"
            rf'{start_hour}:00\s*(am|pm)',  # "9:00 am"
        ]

        start_time_mentioned = any(re.search(pattern, normalized_input, re.IGNORECASE) for pattern in hour_patterns)

        # Also check for time ranges like "9-11", "9 to 11", "9-11 am"
        range_patterns = [
            rf'{start_hour}\s*-\s*{end_hour}',  # "9-11"
            rf'{start_hour}\s+to\s+{end_hour}',  # "9 to 11"
            rf'{start_hour}\s*-\s*{end_hour}\s*(am|pm)',  # "9-11 am"
        ]
        has_range = any(re.search(pattern, normalized_input, re.IGNORECASE) for pattern in range_patterns)

        # If user mentions the day and start time (or range), autocorrect to full format
        # This handles cases like "Monday at 9" -> "Monday 9:00 AM - 11:00 AM"
        if start_time_mentioned or has_range:
            matched_times.append(available_time)

    # If we found a match, autocorrect to the full format
    if matched_times:
        # If multiple matches, prefer the first one (most specific)
        matched_time = matched_times[0]
        session.context['matched_training_times'] = [matched_time]
        await session.save()
        return matched_time  # Return autocorrected full format

    # No match found - return original input (validator will catch it)
    return user_input


@input_directive_override('user_email')
async def custom_email_directive(
    field_name: str,
    value: str,
    session: InterviewSession,
    interaction: Interaction,
    visitor: InteractWalker
) -> Optional[Union[str, Tuple[str, str]]]:
    """Custom directive after email is collected.
    
    This demonstrates the @input_directive_override decorator, which allows
    customizing the agent's response after a field value is successfully stored.
    
    Args:
        field_name: Name of the field that was just stored
        value: The email value that was stored
        session: Interview session for context
        interaction: Current interaction
        visitor: Walker for context
        
    Returns:
        Optional directive override:
        - None: Use default directive (no override)
        - str: Replace default directive with this string
        - Tuple[str, str]: (mode, directive) where mode is "append" or "replace"
    """
    # Check if email domain matches specific criteria
    if '@mail.com' in value.lower():
        # Replace default directive with custom message for example.com emails
        return ("append", "Tell the user: Thank you for using your work email! We'll send you special updates about jvagent training.")
    
    # Return None to use default directive for other emails
    return None


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
    report_description = session.responses.get('report_description', '')
    report_location = session.responses.get('report_location', '')
    is_sensitive = session.responses.get('is_sensitive', '')
    reporting_on_behalf = session.responses.get('reporting_on_behalf', '')
    stakeholder_name = session.responses.get('stakeholder_name', '')
    stakeholder_address = session.responses.get('stakeholder_address', '')
    stakeholder_phone = session.responses.get('stakeholder_phone', '')
    reporter_name = session.responses.get('reporter_name', '')
    reporter_address = session.responses.get('reporter_address', '')

    # Log completion (in production, you might send notifications, create records, etc.)
    import logging
    logger = logging.getLogger(__name__)
    logger.info(
        f"Report interview completed:\n description: {report_description}\n location: {report_location}\n is_sensitive: {is_sensitive}\n reporting_on_behalf: {reporting_on_behalf}\n stakeholder_name: {stakeholder_name}\n stakeholder_address: {stakeholder_address}\n stakeholder_phone: {stakeholder_phone}\n reporter_name: {reporter_name}\n reporter_address: {reporter_address}"
    )

    # Send completion message
    completion_message = (
        f"Tell the user: Thank you, {reporter_name}! Your report for jvagent training is complete. "
    )
    await action.respond(visitor, directives=[completion_message])

    # Clean up the session after processing
    await session.cleanup()
