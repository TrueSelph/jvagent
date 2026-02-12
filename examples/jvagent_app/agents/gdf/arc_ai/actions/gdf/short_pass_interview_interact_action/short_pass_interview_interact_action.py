"""Short Pass Interview action lets ranks request a short pass."""
from __future__ import annotations

# Standard library
import json
import logging
import re
from datetime import datetime
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


class ShortPassInterviewInteractAction(InterviewInteractAction):
    """Short Pass Interview action lets ranks request a short pass.

    This is a concrete implementation of InterviewInteractAction that defines
    a specific interview flow. Sessions are identified by
    interview_type='ShortPassInterviewInteractAction' and attached to Conversation nodes.

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

    description: str = "Short Pass Interview action lets ranks request a short pass."

    # DSPy Integration
    use_dspy: bool = attribute(
        default=True,
        description="Use DSPy module for classification (enables optimization via DSPy teleprompters)",
    )

    # REQUIRED when using InteractRouter: Anchors for intelligent routing
    # Must cover both initial entry and intermediate states (when answering questions)
    anchors: List[str] = attribute(
        default_factory=lambda: [
            "User is requesting a short pass",
            "User needs to file a new short pass request",
            "User is providing details for a new short pass request",
        ],
        description="Anchor statements for InteractRouter routing",
    )

    question_graph: List[Dict[str, Any]] = attribute(
        default_factory=lambda: [
            {
                "name": "start_date",
                "input_context_provider": "get_current_date",
                "question": "What is the proposed start date of your short pass?",
                "constraints": {
                    "description": "A date which marks the beginning of the engagement or leave request.",
                    "instruction": "The current date is <datetime> - use this as a reference to accurately resolve, format, and extract a start date specified. Note that the start date and end date are counted as day 1. Format date as '%A, %B %d, %Y'.",
                    "type": "string",
                },
                "default_next": "end_date",
                "required": True,
            },
            {
                "name": "end_date",
                "input_context_provider": "get_current_date",
                "question": "What's the proposed end date of your short pass?",
                "constraints": {
                    "description": "A date which marks the end of the engagement or leave request.",
                    "instruction": "The current date is <datetime> - use this as a reference to accurately resolve, format, and extract an end date specified. Note that the end date and start date are counted as day 1. Format date as '%A, %B %d, %Y'.",
                    "type": "string",
                },
                "default_next": "overseas_travel",
                "required": False,
            },
            {
                "name": "overseas_travel",
                "question": "Will you be traveling overseas during this short pass?",
                "constraints": {
                    "description": "Whether or not the user will be traveling overseas during this short pass.",
                    "instruction": "Only select a 'yes' or 'no' if the user directly responds to traveling overseas or mentions if they are traveling or not. Leave blank otherwise.",
                    "type": "string",
                    "items": ["yes", "no"],
                },
                "branches": [
                    {
                        "condition": {"op": "equals", "value": "yes"},
                        "target": "overseas_address",
                    },
                    {
                        "condition": {"op": "equals", "value": "no"},
                        "target": "under_confinement",
                    },
                ],
                "default_next": "under_confinement",
                "required": True,
            },
            {
                "name": "overseas_address",
                "question": "What is your overseas address?",
                "constraints": {
                    "description": "The full address where the user will be staying during their overseas travel.",
                    "instruction": "The full address must include lot/apartment, street name, area/city, state (if applicable), and country.",
                    "type": "string",
                },
                "default_next": "overseas_contact_number",
                "required": True,
            },
            {
                "name": "overseas_contact_number",
                "question": "What is your overseas contact number?",
                "constraints": {
                    "description": "A complete phone number in various formats.",
                    "type": "string",
                },
                "default_next": "under_confinement",
                "required": True,
            },
            {
                "name": "under_confinement",
                "question": "Are you currently under base confinement?",
                "constraints": {
                    "description": "Whether or not the user is under base confinement.",
                    "instruction": "Only select a 'yes' or 'no' if the user directly responds to being under base confinement or mentions if they are confined or not. Leave blank otherwise.",
                    "type": "string",
                    "items": ["yes", "no"],
                },
                "default_next": "reason_for_pass",
                "required": True,
            },
            {
                "name": "reason_for_pass",
                "question": "What's the reason you're requesting the short pass?",
                "constraints": {
                    "description": "A comprehensive reason for making the short pass leave request.",
                    "instruction": "Extract the full reason the user stated and then correct all grammatical errors. The reason should be a valid reason for a rank to take a leave, not a request for a pass.",
                    "type": "string",
                },
                "branches": [
                    {
                        "condition": {"function": "can_ask_for_supervisor_name"},
                        "target": "supervisor_name",
                    }
                ],
                "default_next": "REVIEW",
                "required": True,
            },
            {
                "name": "supervisor_name",
                "question": "What's your supervisor's name?",
                "constraints": {
                    "description": "The name of the rank's supervisor.",
                    "type": "string",
                },
                "default_next": "supervisor_phone_number",
                "required": True,
            },
            {
                "name": "supervisor_phone_number",
                "question": "What's your supervisor's contact number?",
                "constraints": {
                    "description": "A full mobile number of the rank's supervisor.",
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

    # Input validator
    @input_validator("start_date")
    def validate_start_date(
        self, value: str, session: InterviewSession, visitor: Optional[InteractWalker] = None, interview_action: Optional[Any] = None
    ) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate that the start date is not empty."""
        if not value or not isinstance(value, str):
            return ValidationStatus.INVALID, "Ask: Please provide the start date."
        return ValidationStatus.VALID, None

    @input_validator("end_date")
    def validate_end_date(
        self, value: str, session: InterviewSession, visitor: Optional[InteractWalker] = None, interview_action: Optional[Any] = None
    ) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate that the end date is not empty."""
        if not value or not isinstance(value, str):
            return ValidationStatus.INVALID, "Ask: Please provide the end date."
        return ValidationStatus.VALID, None

    @input_validator("overseas_travel")
    def validate_overseas_travel(
        self, value: str, session: InterviewSession, visitor: Optional[InteractWalker] = None, interview_action: Optional[Any] = None
    ) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate that the overseas travel response is either yes or no."""
        if not value or not isinstance(value, str):
            return ValidationStatus.INVALID, "Ask: Please indicate if you'll be traveling overseas."

        value = value.strip().lower()
        if value not in ["yes", "no"]:
            return (
                ValidationStatus.INVALID,
                "Ask: Please provide 'yes' or 'no' for overseas travel.",
            )
        return ValidationStatus.VALID, None

    @input_validator("overseas_address")
    def validate_overseas_address(
        self, value: str, session: InterviewSession, visitor: Optional[InteractWalker] = None, interview_action: Optional[Any] = None
    ) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate that the overseas address is sufficiently detailed."""
        if not value or not isinstance(value, str):
            return ValidationStatus.INVALID, "Ask: Please provide your overseas address."

        value = value.strip()
        address_parts = value.split()
        if len(address_parts) < 5:
            return (
                ValidationStatus.INVALID,
                "Ask: Please provide your full overseas address.",
            )
        return ValidationStatus.VALID, None

    @input_validator("overseas_contact_number")
    def validate_overseas_contact_number(
        self, value: str, session: InterviewSession, visitor: Optional[InteractWalker] = None, interview_action: Optional[Any] = None
    ) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate that the overseas contact number is a valid 10-digit number."""
        if not value or not isinstance(value, str):
            return (
                ValidationStatus.INVALID,
                "Ask: Please provide your overseas contact number.",
            )

        value = value.strip()
        if not re.match(r"^\d{10}$", value):
            return (
                ValidationStatus.INVALID,
                "Tell the user: Please provide a valid 10-digit phone number.",
            )
        return ValidationStatus.VALID, None

    @input_validator("under_confinement")
    def validate_under_confinement(
        self, value: str, session: InterviewSession, visitor: Optional[InteractWalker] = None, interview_action: Optional[Any] = None
    ) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate that the confinement response is either yes or no."""
        if not value or not isinstance(value, str):
            return ValidationStatus.INVALID, "Ask: Please indicate if you are under base confinement."

        value = value.strip().lower()
        if value not in ["yes", "no"]:
            return (
                ValidationStatus.INVALID,
                "Ask: Please provide 'yes' or 'no' for under confinement.",
            )
        return ValidationStatus.VALID, None

    @input_validator("reason_for_pass")
    def validate_reason_for_pass(
        self, value: str, session: InterviewSession, visitor: Optional[InteractWalker] = None, interview_action: Optional[Any] = None
    ) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate that the reason for the pass has sufficient detail."""
        if not value or not isinstance(value, str):
            return ValidationStatus.INVALID, "Ask: Please provide the reason for your pass."

        value = value.strip()
        reason_parts = value.split()
        if len(reason_parts) < 3:
            return (
                ValidationStatus.INVALID,
                "Ask: Please provide a more detailed reason for your pass.",
            )
        return ValidationStatus.VALID, None

    @input_validator("supervisor_name")
    def validate_supervisor_name(
        self, value: str, session: InterviewSession, visitor: Optional[InteractWalker] = None, interview_action: Optional[Any] = None
    ) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate that the supervisor name is not empty and formatted correctly."""
        if not value or not isinstance(value, str):
            return ValidationStatus.INVALID, "Ask: Please provide your supervisor's name."

        value = value.strip()
        name_parts = value.split()
        if len(name_parts) < 2:
            return (
                ValidationStatus.INVALID,
                "Ask: Please provide your supervisor's full name.",
            )
        return ValidationStatus.VALID, None

    @input_validator("supervisor_contact_number")
    def validate_supervisor_contact_number(
        self, value: str, session: InterviewSession, visitor: Optional[InteractWalker] = None, interview_action: Optional[Any] = None
    ) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate that the supervisor's contact number is a valid 10-digit number."""
        if not value or not isinstance(value, str):
            return (
                ValidationStatus.INVALID,
                "Ask: Please provide your supervisor's contact number.",
            )

        value = value.strip()
        if not re.match(r"^\d{10}$", value):
            return (
                ValidationStatus.INVALID,
                "Tell the user: Please provide a valid 10-digit phone number.",
            )
        return ValidationStatus.VALID, None

    # Input context provider
    @input_context_provider()
    async def get_current_date(
        self, session: InterviewSession, visitor: InteractWalker
    ) -> Dict[str, Any]:
        """Provide the current date for reference."""
        now = datetime.now()
        date_str = now.strftime("%A, %d %B, %Y")
        return {"date": date_str}

    # Branch function
    @branch_function("can_ask_for_supervisor_name")
    async def can_ask_for_supervisor_name(
        self, session: InterviewSession, visitor: InteractWalker
    ) -> bool:
        """Determine if supervisor details need to be collected from the user."""
        logger.warning("Checking if supervisor details are already available.")

        rank_profile = {
            "ident_code": "MiPWJFWbxqPccfusEygn",
            "regimental_number": "15264",
            "unit": {"id": 4, "name": "Artillery"},
            "sub_unit": None,
            "supervisor": {
                "first_name": "John",
                "last_name": "Brown",
                "regimental_number": "34342",
                "phone": "5926415808",
                "is_unit_supervisor": True,
            },
            "first_name": "Tharick",
            "last_name": "Jairam",
            "is_first_time": False,
            "is_security_question_set": True,
            "is_pin_set": True,
            "rank": {"name": "Lt Col", "full_name": "Lieutenant Colonel"},
        }

        if rank_profile and rank_profile.get("supervisor"):
            session.context["supervisor_name"] = f"{rank_profile['supervisor']['first_name']} {rank_profile['supervisor']['last_name']}"
            session.context["supervisor_phone_number"] = rank_profile["supervisor"]["phone"]
            session.context["supervisor_rank_number"] = rank_profile["supervisor"]["regimental_number"]
            session.context["rank_number"] = rank_profile.get("regimental_number", "")

            if isinstance(rank_profile.get("rank"), dict):
                session.context["rank_name"] = rank_profile.get("rank", {}).get("name", "Unknown")
                session.context["rank_rank"] = rank_profile.get("rank", {}).get("full_name", "Unknown")
            else:
                session.context["rank_name"] = "Unknown"
                session.context["rank_rank"] = "Unknown"

            unit = rank_profile.get("unit")
            session.context["unit"] = unit["name"] if isinstance(unit, dict) else "Unknown"

            sub_unit = rank_profile.get("sub_unit")
            session.context["sub_unit"] = sub_unit["name"] if isinstance(sub_unit, dict) else "Unknown"
            
            return False

        return True


# Input review override
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
        else:
            result[field_name] = value

    for key, value in session.context.items():
        if key in ["supervisor_name", "supervisor_phone_number"]:
            result_ending[key] = value

    # Add context items at the end
    result.update(result_ending)
    return result


@on_interview_complete('ShortPassInterviewInteractAction')
async def handle_interview_completion(
    session: InterviewSession,
    visitor: InteractWalker,
    action: InteractAction
) -> None:
    """Handle completion of short pass interview."""
    logger.warning(f"Interview responses: {json.dumps(session.responses, indent=4)}")

    arc_api_action = await action.get_action("ArcAPIAction")
    completion_message = "Tell the user: Sorry, I was unable to submit your short pass. Please try again later!"
    
    if arc_api_action:
        # Session responses
        under_confinement = session.responses.get('under_confinement', "N/A")
        overseas_travel = session.responses.get('overseas_travel', "N/A")
        start_date_str = session.responses.get('start_date', "N/A")
        end_date_str = session.responses.get('end_date', "N/A")
        reason_for_pass = session.responses.get('reason_for_pass', "N/A")
        overseas_address = session.responses.get('overseas_address', "N/A")
        overseas_contact_number = session.responses.get('overseas_contact_number', "N/A")

        # Session context 
        supervisor_name = session.responses.get('supervisor_name') or session.context.get('supervisor_name')
        supervisor_phone_number = (session.responses.get('supervisor_phone_number') or 
                                    session.responses.get('supervisor_contact_number') or 
                                    session.context.get('supervisor_phone_number'))
        
        supervisor_rank_number = session.context.get('supervisor_rank_number', "Unknown")
        rank_number = session.context.get('rank_number', "Unknown")
        rank_name = session.context.get('rank_name', "Unknown")
        rank_rank = session.context.get('rank_rank', "Unknown")
        unit = session.context.get('unit', "Unknown")
        sub_unit = session.context.get('sub_unit', "Unknown")

        # Determine pass type
        if under_confinement == "yes":
            pass_type = "CONFINEMENT"
        elif overseas_travel == "yes":
            pass_type = "OVERSEAS"
        else:
            pass_type = "TRADITIONAL"

        # Convert date formats
        try:
            start_date = datetime.strptime(start_date_str, "%A, %B %d, %Y").strftime("%Y-%m-%d")
            end_date = datetime.strptime(end_date_str, "%A, %B %d, %Y").strftime("%Y-%m-%d")
        except ValueError:
            start_date = start_date_str
            end_date = end_date_str

        # Format review / supervisor message
        unit_or_sub_unit = f"{unit} {sub_unit}" if unit != "Unknown" and sub_unit != "Unknown" else (unit if unit != "Unknown" else sub_unit)
        
        particulars = (
            f"*Short Pass Request*\n\n"
            f"*Name:* {rank_rank} {rank_name} ({rank_number})\n"
            f"*Unit/Subunit:* {unit_or_sub_unit}\n"
            f"*Supervisor:* {supervisor_name} ({supervisor_rank_number})\n\n"
            f"*Dates:* {start_date} to {end_date}\n\n"
            f"*Reason:* {reason_for_pass}"
        )

        if pass_type == "OVERSEAS":
            particulars += f"\n\n*Overseas Address:* {overseas_address}\n*Overseas Contact:* {overseas_contact_number}"

        # Mock ID for demo
        short_pass_id = "95"

        if short_pass_id:
            completion_message = f"Tell the user: Thank you for your short pass submission! Your reference number for follow-up is: {short_pass_id}."
            supervisor_notification = particulars.replace("*Short Pass Request*", f"*Short Pass Request*\n*Reference Number*: {short_pass_id}")
            logger.warning(f"Supervisor Notification: {supervisor_notification}")
            
            # Logic for sending via WhatsApp would go here
    else:
        logger.error("ArcAPIAction not found for short pass submission.")

    await action.respond(visitor, directives=[completion_message])
    await session.cleanup()
