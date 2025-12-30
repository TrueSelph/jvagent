"""Signup interview for user registration and training availability."""

import re
from typing import Any, Dict, List, Optional, Tuple

from jvagent.action.interview.interview_interact_action import (
    InterviewInteractAction,
    input_handler,
    input_validator,
    on_interview_complete,
)
from jvagent.action.interview.core.interview_session import InterviewSession
from jvagent.action.interview.core.validation import ValidationStatus
from jvagent.memory import Interaction
from jvagent.action.interact.interact_walker import InteractWalker
from jvspatial.core.annotations import attribute


class SignupInterviewInteractAction(InterviewInteractAction):
    """Signup interview for user registration and training availability.
    
    This replaces the hardcoded questions from the original InterviewInteractAction
    with a concrete implementation. Sessions are identified by 
    interview_type='SignupInterviewInteractAction' and attached to Conversation nodes.
    
    The question_index can be overridden in agent.yaml to customize questions.
    """
    
    description: str = "User signup interview flow for registration and training scheduling"

    # REQUIRED when using InteractRouter: Anchors for intelligent routing
    # Must cover both initial entry and intermediate states (when answering questions)
    anchors: List[str] = attribute(
        default_factory=lambda: [
            # Initial entry anchors - when user wants to start training signup
            "User wants to sign up for jvagent training",
            "User requests training signup",
            "User asks to register for training",
            "User wants to enroll in jvagent training",
            "User needs to sign up for training",
            "User wants to join jvagent training",
            "User requests to register for jvagent training",
            # Intermediate state anchors - when user is answering training signup questions
            "User is providing training signup information",
            "User is answering training signup questions",
            "User is completing training registration form",
            "User responds to training signup prompt",
            "User is filling out training signup",
            "User is providing information for jvagent training",
            "User is answering questions for training enrollment",
            "User is providing availability for training",
        ],
        description="Anchor statements for InteractRouter routing"
    )

    question_index: List[Dict[str, Any]] = attribute(
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
        ],
        description="List of question configurations. Can be overridden in agent.yaml. "
                    "Handlers and validators can be registered via decorators (@input_handler, @input_validator) "
                    "or specified as string references in constraints (input_handler, input_validator)."
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
        return ValidationStatus.INVALID, "Please provide your full name"
    
    # Remove extra whitespace
    name = value.strip()
    
    # Check minimum length
    if len(name) < 3:
        return ValidationStatus.INVALID, "Please provide your complete full name"
    
    # Split by spaces and check for at least two parts (first and last name)
    name_parts = name.split()
    if len(name_parts) < 2:
        return ValidationStatus.INVALID, "Please provide both your first and last name"
    
    # Check that each part has at least 2 characters
    for part in name_parts:
        if len(part) < 2:
            return ValidationStatus.INVALID, "Each name part should be at least 2 characters long"
    
    # Check for valid characters (letters, hyphens, apostrophes)
    if not re.match(r'^[a-zA-Z\s\-\']+$', name):
        return ValidationStatus.INVALID, "Name should only contain letters, spaces, hyphens, and apostrophes"
    
    return ValidationStatus.VALID, None


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
        return ValidationStatus.INVALID, "Please provide a valid email address"
    
    email = value.strip().lower()
    
    # Basic email format validation
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        return ValidationStatus.INVALID, "Please provide a valid email address format (e.g., name@example.com)"
    
    # Check for common invalid domains
    invalid_domains = ['example.com', 'test.com', 'invalid.com']
    domain = email.split('@')[1] if '@' in email else ''
    if domain in invalid_domains:
        return ValidationStatus.INVALID, "Please provide a real email address, not a test domain"
    
    # Check for common email providers or valid domain structure
    if len(domain.split('.')) < 2:
        return ValidationStatus.INVALID, "Email domain appears to be invalid"
    
    return ValidationStatus.VALID, None


@input_handler('available_times')
def check_training_availability(
    raw_input: str, 
    session: InterviewSession,
    interaction: Interaction
) -> str:
    """Process and validate training time availability against hardcoded available times.
    
    This handler checks if the user's requested time matches any available training slots.
    It normalizes the input and provides feedback if no match is found.
    
    Args:
        raw_input: Raw user input about availability
        session: Interview session (for context)
        interaction: Interaction node (can access interaction.user_id, interaction.utterance, etc.)
        
    Returns:
        Processed availability string with validation feedback
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
    
    # Check if user input matches any available time (fuzzy matching)
    matched_times = []
    for available_time in AVAILABLE_TRAINING_TIMES:
        normalized_available = re.sub(r'\s+', ' ', available_time.lower())
        
        # Check for exact match or if user input contains key parts of available time
        if normalized_input == normalized_available:
            matched_times.append(available_time)
        elif any(day in normalized_input for day in ['monday', 'wednesday', 'friday', 'saturday']):
            # If user mentions a day, check if time matches
            if any(time_part in normalized_input for time_part in normalized_available.split()):
                matched_times.append(available_time)
    
    # If matches found, return the matched time(s)
    if matched_times:
        # Store matched times in session context for later use
        if not hasattr(session, 'context'):
            session.context = {}
        session.context['matched_training_times'] = matched_times
        return f"Available: {', '.join(matched_times)}"
    
    # If no match, return original input but note available times
    available_list = ', '.join(AVAILABLE_TRAINING_TIMES)
    return f"{user_input} (Note: Available training times are: {available_list})"


@on_interview_complete('SignupInterviewInteractAction')
async def handle_signup_completion(
    session: InterviewSession,
    visitor: InteractWalker
) -> None:
    """Handle completion of signup interview.
    
    This handler is called when the signup interview is completed.
    Process collected data, trigger downstream actions, or perform cleanup.
    
    Args:
        session: The completed interview session with all collected responses
        visitor: The walker for accessing context and responding
    """
    # Extract collected data
    user_name = session.responses.get('user_name', '')
    user_email = session.responses.get('user_email', '')
    available_times = session.responses.get('available_times', '')
    
    # Log completion (in production, you might send notifications, create records, etc.)
    import logging
    logger = logging.getLogger(__name__)
    logger.info(
        f"Signup interview completed: {user_name} ({user_email}) - Available: {available_times}"
    )
    
    # Example: You could trigger downstream actions here
    # For example, create a user record, send a confirmation email, etc.
    # action = await visitor.get_action(SomeOtherAction)
    # if action:
    #     await action.process_signup(user_name, user_email, available_times)

