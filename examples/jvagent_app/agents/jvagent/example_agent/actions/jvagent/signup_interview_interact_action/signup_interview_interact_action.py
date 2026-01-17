"""Signup interview for user registration and training availability."""

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


class SignupInterviewInteractAction(InterviewInteractAction):
    """Signup interview for user registration and training availability.

    This is a concrete implementation of InterviewInteractAction that defines
    a specific interview flow. Sessions are identified by
    interview_type='SignupInterviewInteractAction' and attached to Conversation nodes.

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

    description: str = "User signup interview flow for registration and training scheduling"

    # DSPy Integration
    use_dspy: bool = attribute(
        default=True,
        description="Use DSPy module for classification (enables optimization via DSPy teleprompters)"
    )

    # REQUIRED when using InteractRouter: Anchors for intelligent routing
    # Must cover both initial entry and intermediate states (when answering questions)
    anchors: List[str] = attribute(
        default_factory=lambda: [
            # Initial entry - starting training signup
            "User wants to sign up or register for jvagent training",
            "User wants to enroll or join jvagent training",
            # Intermediate state - answering questions
            "User is providing or answering training signup questions",
            "User is completing training registration or providing availability",
            # Revision/edit - changing previously provided information
            "User wants to change, update, or correct their name, email, or availability",
            "User is revising, editing, or updating training signup information",
        ],
        description="Anchor statements for InteractRouter routing"
    )

    question_graph: List[Dict[str, Any]] = attribute(
        default_factory=lambda: [
            {
                "name": "user_name",
                "question": "What's your full name?",
                "constraints": {
                    "description": "The user's full name",
                    "instructions": "The user's full name must include their first and last name.",
                    "type": "string",
                },
                "required": True
            },
            {
                "name": "available_times",
                "question": "What times are you available to train?",
                "constraints": {
                    "description": "The user's available times for jvagent training",
                    "instructions": "Please specify your preferred training times. Available slots are: Monday 9-11 AM, Monday 2-4 PM, Wednesday 9-11 AM, Wednesday 2-4 PM, Friday 10 AM-12 PM, Saturday 9 AM-12 PM.",
                    "type": "string",
                },
                "required": True
            },
            {
                "name": "user_email",
                "question": "What is your email?",
                "constraints": {
                    "description": "The user's email address",
                    "instructions": "Please provide a valid email address where we can contact you about training.",
                    "type": "string",
                    "format": "email",
                },
                "required": True
            },
            {
                "name": "phone_number",
                "question": "What is your phone number? (optional)",
                "constraints": {
                    "description": "The user's phone number",
                    "instructions": "Please provide a phone number if you'd like us to contact you via phone. You can skip this question if you prefer not to provide it.",
                    "type": "string",
                },
                "required": False
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


@input_validator('user_name')
def validate_full_name(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that full name contains both first and last name.

    Args:
        value: The name string to validate
        session: Interview session (for context)

    Returns:
        Tuple of (ValidationStatus, optional error message)
    """
    if not value or not isinstance(value, str):
        return ValidationStatus.INVALID, "Ask: Please provide your full name"

    # Remove extra whitespace
    name = value.strip()

    # Check minimum length
    if len(name) < 3:
        return ValidationStatus.INVALID, "Ask: Please provide your complete full name"

    # Split by spaces and check for at least two parts (first and last name)
    name_parts = name.split()
    if len(name_parts) < 2:
        return ValidationStatus.INVALID, "Ask: Please provide both your first and last name"

    # Check that each part has at least 2 characters
    for part in name_parts:
        if len(part) < 2:
            return ValidationStatus.INVALID, "Tell the user: Each name part should be at least 2 characters long"

    # Check for valid characters (letters, hyphens, apostrophes)
    if not re.match(r'^[a-zA-Z\s\-\']+$', name):
        return ValidationStatus.INVALID, "Tell the user: Name should only contain letters, spaces, hyphens, and apostrophes"

    return ValidationStatus.VALID, None


@input_validator('available_times')
def validate_available_times(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str], Optional[str]]:
    """Validate that available times match one of the available training slots.

    Autocorrects close matches using fuzzy matching. If a value mostly matches
    an available option, it returns the corrected value for saving.

    Args:
        value: The availability string to validate
        session: Interview session (for context)

    Returns:
        Tuple of (ValidationStatus, optional error message, optional corrected value)
        If corrected value is provided, it will be saved instead of the original value
    """
    if not value or not isinstance(value, str):
        return ValidationStatus.INVALID, "Ask: Please provide your available training times", None

    value = value.strip()

    # Available training times
    AVAILABLE_TRAINING_TIMES = [
        "Monday 9:00 AM - 11:00 AM",
        "Monday 2:00 PM - 4:00 PM",
        "Wednesday 9:00 AM - 11:00 AM",
        "Wednesday 2:00 PM - 4:00 PM",
        "Friday 10:00 AM - 12:00 PM",
        "Saturday 9:00 AM - 12:00 PM",
    ]

    # Normalize input for comparison
    normalized_value = re.sub(r'\s+', ' ', value.lower())

    # First, check for exact match (case-insensitive, flexible spacing)
    for available_time in AVAILABLE_TRAINING_TIMES:
        normalized_available = re.sub(r'\s+', ' ', available_time.lower())
        if normalized_value == normalized_available:
            return ValidationStatus.VALID, None, available_time

    # Check if input handler stored matched times in context
    matched_times = session.context.get('matched_training_times', [])
    if matched_times:
        return ValidationStatus.VALID, None, matched_times[0]

    # Strict matching: day of week AND first time must match
    # Extract day from input
    input_day = None
    for day in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']:
        if day in normalized_value:
            input_day = day
            break

    if not input_day:
        available_list = ', '.join(AVAILABLE_TRAINING_TIMES)
        return ValidationStatus.INVALID, f"Tell the user that their choice is not available and advise them to select from the available training times: {available_list}", None

    # Extract start time from input (look for patterns like "9", "9:00", "9 am", "9:00 am", etc.)
    # Common patterns: hour with optional minutes and am/pm
    time_pattern = r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b'
    time_matches = re.findall(time_pattern, normalized_value)

    if not time_matches:
        available_list = ', '.join(AVAILABLE_TRAINING_TIMES)
        return ValidationStatus.INVALID, f"Tell the user that their choice is not available and advise them to select from the available training times: {available_list}", None

    # Get the first time mentioned in the input
    input_hour = int(time_matches[0][0])
    input_period = time_matches[0][2] if time_matches[0][2] else None

    # Try to find a matching available time slot
    best_match = None
    for available_time in AVAILABLE_TRAINING_TIMES:
        normalized_available = re.sub(r'\s+', ' ', available_time.lower())

        # Check if day matches
        if input_day not in normalized_available:
            continue

        # Extract start time from available time
        # Format: "monday 9:00 am - 11:00 am"
        available_time_match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)', normalized_available)
        if not available_time_match:
            continue

        available_hour = int(available_time_match.group(1))
        available_period = available_time_match.group(3)

        # Check if start time matches
        hour_matches = (input_hour == available_hour)

        # If period was specified in input, it must match too
        if input_period:
            period_matches = (input_period == available_period)
        else:
            # If no period specified, just match the hour
            period_matches = True

        if hour_matches and period_matches:
            best_match = available_time
            break

    # If we found a match, return it
    if best_match:
        return ValidationStatus.VALID, None, best_match

    # No match found - value is invalid
    available_list = ', '.join(AVAILABLE_TRAINING_TIMES)
    return ValidationStatus.INVALID, f"Tell the user that their choice is not available and advise them to select from the available training times: {available_list}", None


@input_validator('user_email')
def validate_email(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate email address format and common domains.

    Args:
        value: The email string to validate
        session: Interview session (for context)

    Returns:
        Tuple of (ValidationStatus, optional error message)
    """
    if not value or not isinstance(value, str):
        return ValidationStatus.INVALID, "Ask: Please provide a valid email address"

    email = value.strip().lower()

    # Basic email format validation
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        return ValidationStatus.INVALID, "Tell the user: Please provide a valid email address format (e.g., name@example.com)"

    # Check for common invalid domains
    invalid_domains = ['example.com', 'test.com', 'invalid.com']
    domain = email.split('@')[1] if '@' in email else ''
    if domain in invalid_domains:
        return ValidationStatus.INVALID, "Tell the user: Please provide a real email address, not a test domain"

    # Check for common email providers or valid domain structure
    if len(domain.split('.')) < 2:
        return ValidationStatus.INVALID, "Tell the user: Email domain appears to be invalid"

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
        return ("replace", "Tell the user: Thank you for using your work email! We'll send you special updates about jvagent training.")
    
    # Return None to use default directive for other emails
    return None


@on_interview_complete('SignupInterviewInteractAction')
async def handle_signup_completion(
    session: InterviewSession,
    visitor: InteractWalker,
    action: InteractAction
) -> None:
    """Handle completion of signup interview.

    This handler is called when the signup interview is completed.
    Process collected data, trigger downstream actions, or perform cleanup.

    Args:
        session: The completed interview session with all collected responses
        visitor: The walker for accessing context and responding
        action: The InteractAction instance (use action.respond() to send responses)
    """
    # Extract collected data
    user_name = session.responses.get('user_name', '')
    user_email = session.responses.get('user_email', '')
    available_times = session.responses.get('available_times', '')
    matched_times = session.context.get('matched_training_times', [])

    # Log completion (in production, you might send notifications, create records, etc.)
    import logging
    logger = logging.getLogger(__name__)
    logger.info(
        f"Signup interview completed: {user_name} ({user_email}) - Available: {available_times}"
    )

    # Send completion message
    completion_message = (
        f"Tell the user: Thank you, {user_name}! Your signup for jvagent training is complete. "
        f"We will contact you at {user_email}. "
    )
    if matched_times:
        completion_message += f"Your preferred times were: {', '.join(matched_times)}."
    else:
        completion_message += f"Your availability: {available_times}."

    await action.respond(visitor, directives=[completion_message])

    # Clean up the session after processing
    await session.cleanup()

    # Example: You could trigger downstream actions here
    # For example, create a user record, send a confirmation email, etc.
    # action = await visitor.get_action(SomeOtherAction)
    # if action:
    #     await action.process_signup(user_name, user_email, available_times)
