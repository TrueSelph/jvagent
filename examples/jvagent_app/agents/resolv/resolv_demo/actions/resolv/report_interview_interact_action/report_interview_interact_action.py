"""Report interview for report submission."""
from __future__ import annotations

# Standard library
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Union

# Third-party / external packages
from jvspatial.core.annotations import attribute

# Local application imports
from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.interview import (
    InterviewInteractAction,
    branch_function,
    input_context_provider,
    input_directive_override,
    input_review_override,
    input_validator,
    on_interview_complete,
)
from jvagent.action.interview.core.foundation.enums import ValidationStatus
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.memory import Interaction


logger = logging.getLogger(__name__)


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
            "User is providing details for a new incident report",
            "User is uploading photos or evidence for a new incident report",
            "User is revising, canceling, updating, or confirming an active incident report being created",
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
                "required": False,
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
                "required": False,
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
                "required": False,
                "default_next": "reporter_name",
            },
            {
                "name": "reporter_name",
                "question": "What is your full name?",
                "constraints": {
                    "description": "The full name of the person submitting the report.",
                    "instruction": "The name should not be the same as the name of the person the report is being filed on behalf of.",
                    "type": "string",
                },
                "required": False,
                "default_next": "reporter_address",
            },
            {
                "name": "reporter_address",
                "question": "What is your residential address?",
                "constraints": {
                    "description": "The home address of the person submitting the report.",
                    "instruction": "The address should not be the incident location or the address of the person the report is being filed on behalf of.",
                    "type": "string",
                },
                "required": False,
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


    # branch function
    @branch_function("detect_sensitive_content")
    def detect_sensitive_content(
        session: InterviewSession, visitor: Optional[InteractWalker] = None, interview_action: Optional[Any] = None
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

        # Check for presence of media - handle N/A, skip, empty list, None
        # Optional questions may be set to "N/A" or similar when skipped
        has_media = False
        if media:
            # Skip common placeholder values for optional questions
            if isinstance(media, str):
                media_lower = media.lower().strip()
                has_media = media_lower not in ["n/a", "na", "skip", "none", ""]
            elif isinstance(media, list):
                has_media = len(media) > 0
            else:
                has_media = True

        result = has_sensitive_text or has_media
        return result


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
                "Ask: Please provide a description of the incident.",
            )

        value = value.strip()
        if len(value) < 10:
            return (
                ValidationStatus.INVALID,
                "Ask: Please provide a more detailed description of what happened.",
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
                "Ask: Please provide the location where the incident occurred.",
            )

        value = value.strip()
        if len(value) < 10:
            return ValidationStatus.INVALID, "Ask: Please provide a more specific location."

        return ValidationStatus.VALID, None


    @input_validator("is_sensitive")
    def validate_is_sensitive(
        value: str,
        session: InterviewSession,
        visitor: Optional[InteractWalker] = None,
        interview_action: Optional[Any] = None,
    ) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate that is_sensitive is either yes or no."""
        if not value or not isinstance(value, str):
            return (
                ValidationStatus.INVALID,
                "Ask: Please indicate whether the report is sensitive.",
            )

        value = value.strip().lower()
        if value not in ["yes", "no"]:
            return (
                ValidationStatus.INVALID,
                "Ask: Please indicate whether the report is sensitive.",
            )
        return ValidationStatus.VALID, None

    @input_validator("reporting_on_behalf")
    def validate_reporting_on_behalf(
        value: str,
        session: InterviewSession,
        visitor: Optional[InteractWalker] = None,
        interview_action: Optional[Any] = None,
    ) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate that reporting_on_behalf is either yes or no."""
        if not value or not isinstance(value, str):
            return (
                ValidationStatus.INVALID,
                "Ask: Please indicate whether you are reporting on behalf of someone else.",
            )

        value = value.strip().lower()
        if value not in ["yes", "no"]:
            return (
                ValidationStatus.INVALID,
                "Ask: Please indicate whether you are reporting on behalf of someone else.",
            )
        return ValidationStatus.VALID, None

    @input_validator("stakeholder_name")
    def validate_stakeholder_name(
        value: str,
        session: InterviewSession,
        visitor: Optional[InteractWalker] = None,
        interview_action: Optional[Any] = None,
    ) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate that the stakeholder name is not empty and formatted correctly."""
        if not value or not isinstance(value, str):
            return (
                ValidationStatus.INVALID,
                "Ask: Please provide the name of the stakeholder.",
            )

        value = value.strip()
        name_parts = value.split()
        if len(name_parts) < 2:
            return (
                ValidationStatus.INVALID,
                "Ask: Please provide both your first and last name.",
            )

        for part in name_parts:
            if len(part) < 2:
                return (
                    ValidationStatus.INVALID,
                    "Tell the user: Each name part should be at least 2 characters long.",
                )

        if not re.match(r"^[a-zA-Z\s\-\']+$", value):
            return (
                ValidationStatus.INVALID,
                "Tell the user: Name should only contain letters, spaces, hyphens, and apostrophes.",
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
                "Ask: Please provide the address of the stakeholder.",
            )

        value = value.strip()
        if len(value) < 10:
            return (
                ValidationStatus.INVALID,
                "Ask: Please provide the full address of the stakeholder.",
            )

        return ValidationStatus.VALID, None

    @input_validator("stakeholder_phone")
    def validate_stakeholder_phone(
        value: str,
        session: InterviewSession,
        visitor: Optional[InteractWalker] = None,
        interview_action: Optional[Any] = None,
    ) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate that the stakeholder phone is not empty and formatted correctly."""
        if not value or not isinstance(value, str):
            return (
                ValidationStatus.INVALID,
                "Ask: Please provide the contact number of the stakeholder.",
            )

        value = value.strip()
        if not re.match(r"^\d{10}$", value):
            return (
                ValidationStatus.INVALID,
                "Tell the user: Please provide a valid 10-digit phone number.",
            )
        return ValidationStatus.VALID, None

    @input_validator("reporter_name")
    def validate_reporter_name(
        value: str,
        session: InterviewSession,
        visitor: Optional[InteractWalker] = None,
        interview_action: Optional[Any] = None,
    ) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate that the reporter name is not empty and formatted correctly."""
        if not value or not isinstance(value, str):
            return (
                ValidationStatus.INVALID,
                "Ask: Please provide the name of the reporter.",
            )

        value = value.strip()
        name_parts = value.split()
        if len(name_parts) < 2:
            return (
                ValidationStatus.INVALID,
                "Ask: Please provide both the first and last name of the reporter.",
            )

        for part in name_parts:
            if len(part) < 2:
                return (
                    ValidationStatus.INVALID,
                    "Tell the user: Each name part should be at least 2 characters long.",
                )

        if not re.match(r"^[a-zA-Z\s\-\']+$", value):
            return (
                ValidationStatus.INVALID,
                "Tell the user: Name should only contain letters, spaces, hyphens, and apostrophes.",
            )
        return ValidationStatus.VALID, None

    @input_validator("reporter_address")
    def validate_reporter_address(
        value: str,
        session: InterviewSession,
        visitor: Optional[InteractWalker] = None,
        interview_action: Optional[Any] = None,
    ) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate that the reporter address is not empty."""
        if not value or not isinstance(value, str):
            return (
                ValidationStatus.INVALID,
                "Ask: Please provide your full address.",
            )

        value = value.strip()
        if len(value) < 10:
            return (
                ValidationStatus.INVALID,
                "Ask: Please provide your full address.",
            )

        return ValidationStatus.VALID, None


# input review override
@input_review_override
def adapt_review(
    session: InterviewSession,
    data: Dict[str, Any],
    visitor: Optional[InteractWalker] = None,
    interview_action: Optional[Any] = None,
) -> Dict[str, Any]:
    """Omit or format values shown in the Review state."""
    result: Dict[str, Any] = {}
    result_ending: Dict[str, Any] = {}
    for field_name, value in data.items():

        if (
            value is None
            or value == ""
            or (isinstance(value, str) and value.strip().lower() in ("n/a", "na"))
        ):
            continue
        elif field_name == "incident_media" and value:
            media_links = ""
            for link in value:
                media_links += f"{link}\n"
            result_ending[field_name] = media_links
        else:
            result[field_name] = value

    # add items at the end of the dict 
    result.update(result_ending)

    return result


@on_interview_complete('ReportInterviewInteractAction')
async def handle_interview_completion(
    session: InterviewSession,
    visitor: InteractWalker,
    action: InteractAction
) -> None:
    """Handle completion of report interview."""

    logger.warning(f"Interview responses:\n{json.dumps(session.responses, indent=4)}")

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

    is_anonymous = is_sensitive == 'yes'
    reporter_phone = visitor.user_id

    # generated data
    # title = "Suspected Ongoing Domestic Violence - Immediate Safety Risk"
    # generated_description = "Reports indicate an ongoing domestic violence situation at a residence in South Ruimveldt. Neighbours have repeatedly heard loud arguments, physical assaults, and serious verbal threats, including threats to kill, occurring late at night over several weeks. The incidents appear frequent and escalating, suggesting a loss of control by the alleged perpetrator.\n\nThe victim is believed to be at high risk of serious injury or death if intervention does not occur promptly. The continued absence of action despite clear warning signs presents a critical safety concern. Immediate investigation and protective measures are strongly recommended to ensure the safety of the individual involved and to prevent further harm."
    # priority = "high"
    # category_id = 1
    # ai_overview = ""


    completion_message = f"Tell the user: Sorry, {reporter_name}! I was unable to submit your report. Please try again later!"
    resolv_api_action = await action.get_action("ResolvAPIAction")
    if resolv_api_action:
        result = await resolv_api_action.submit_report(
            # title=title,
            is_anonymous=is_anonymous,
            # description=generated_description,
            original_description=incident_description,
            attachments=incident_media,
            # priority=priority,
            # category_id=category_id,
            reporting_on_behalf=reporting_on_behalf,
            stakeholder_name=stakeholder_name,
            stakeholder_address=stakeholder_address,
            stakeholder_phone=stakeholder_phone,
            reporter_name=reporter_name,
            reporter_phone=reporter_phone,
            reporter_address=reporter_address,
            # ai_overview=ai_overview
        )

        if result:
            logger.warning("result")
            logger.warning(result)
            reference_number = result.get("id")
            completion_message = f"Tell the user: Thank you, {reporter_name}! Your report has been submitted successfully. Here is your {reference_number} for follow up."
    else:
        logger.warning("ResolvAPIAction not found for report submission")

    # Send completion message
    await action.respond(visitor, directives=[completion_message])

    # Clean up the session after processing
    await session.cleanup()
