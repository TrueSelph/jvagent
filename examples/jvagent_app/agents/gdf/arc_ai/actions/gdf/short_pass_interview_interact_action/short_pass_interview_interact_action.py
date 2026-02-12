"""Short Pass Interview action lets ranks request a short pass."""

import re
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union

from jvagent.action.interview import (
    InterviewInteractAction,
    input_handler,
    input_validator,
    input_directive_override,
    on_interview_complete,
    input_context_provider,
    branch_function,
    input_review_override
)
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.foundation.enums import ValidationStatus
from jvagent.memory import Interaction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.interact.base import InteractAction
from jvspatial.core.annotations import attribute

from jvagent.action.interview.core.session.interview_service import InterviewService

logger = logging.getLogger(__name__)

# directive override
@input_review_override
def adapt_short_pass_review_for_display(
    session: InterviewSession,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    """Omit or format values shown in the Review state."""
    result: Dict[str, Any] = {}
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
            result[key] = value

    return result


# input validator 
@input_validator("start_date")
def validate_start_date(
    value: str, session: InterviewSession
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the start date is not empty."""

    if not value or not isinstance(value, str):
        return ValidationStatus.INVALID, "Ask: Please provide the start date"

    return ValidationStatus.VALID, None


@input_validator("end_date")
def validate_end_date(
    value: str, session: InterviewSession
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the end date is not empty."""

    if not value or not isinstance(value, str):
        return ValidationStatus.INVALID, "Ask: Please provide the end date"

    return ValidationStatus.VALID, None


@input_validator("overseas_travel")
def validate_overseas_travel(
    value: str, session: InterviewSession
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the overseas travel is not empty."""

    if not value or not isinstance(value, str):
        return ValidationStatus.INVALID, "Ask: Please provide the overseas travel"

    value = value.strip()
    if value.lower() not in ["yes", "no"]:
        return (
            ValidationStatus.INVALID,
            "Ask: Please provide 'yes' or 'no' for overseas travel",
        )

    return ValidationStatus.VALID, None


@input_validator("overseas_address")
def validate_overseas_address(
    value: str, session: InterviewSession
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the overseas address is not empty."""

    if not value or not isinstance(value, str):
        return ValidationStatus.INVALID, "Ask: Please provide the overseas address"

    value = value.strip()
    name_parts = value.split()
    if len(name_parts) < 5:
        return (
            ValidationStatus.INVALID,
            "Ask: Please provide your full overseas address",
        )

    return ValidationStatus.VALID, None


@input_validator("overseas_contact_number")
def validate_overseas_contact_number(
    value: str, session: InterviewSession
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the overseas contact number is not empty."""

    if not value or not isinstance(value, str):
        return (
            ValidationStatus.INVALID,
            "Ask: Please provide the overseas contact number",
        )

    value = value.strip()
    if not re.match(r"^\d{10}$", value):
        return (
            ValidationStatus.INVALID,
            "Tell the user: Please provide a valid 10-digit phone number",
        )
    return ValidationStatus.VALID, None


@input_validator("under_confinement")
def validate_under_confinement(
    value: str, session: InterviewSession
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the under confinement is not empty."""

    if not value or not isinstance(value, str):
        return ValidationStatus.INVALID, "Ask: Please provide the under confinement"

    value = value.strip()
    if value.lower() not in ["yes", "no"]:
        return (
            ValidationStatus.INVALID,
            "Ask: Please provide 'yes' or 'no' for under confinement",
        )

    return ValidationStatus.VALID, None


@input_validator("reason_for_pass")
def validate_reason_for_pass(
    value: str, session: InterviewSession
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the reason for pass is not empty."""

    if not value or not isinstance(value, str):
        return ValidationStatus.INVALID, "Ask: Please provide the reason for pass"

    value = value.strip()
    name_parts = value.split()
    if len(name_parts) < 3:
        return (
            ValidationStatus.INVALID,
            "Ask: Please provide a more detailed reason for pass",
        )

    return ValidationStatus.VALID, None


@input_validator("supervisor_name")
def validate_supervisor_name(
    value: str, session: InterviewSession
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the supervisor name is not empty."""

    if not value or not isinstance(value, str):
        return ValidationStatus.INVALID, "Ask: Please provide the supervisor name"

    value = value.strip()
    name_parts = value.split()
    if len(name_parts) < 2:
        return (
            ValidationStatus.INVALID,
            "Ask: Please provide a valid supervisor name",
        )

    return ValidationStatus.VALID, None


@input_validator("supervisor_contact_number")
def validate_supervisor_contact_number(
    value: str, session: InterviewSession
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the supervisor contact number is not empty."""

    if not value or not isinstance(value, str):
        return (
            ValidationStatus.INVALID,
            "Ask: Please provide the supervisor contact number",
        )

    value = value.strip()
    if not re.match(r"^\d{10}$", value):
        return (
            ValidationStatus.INVALID,
            "Tell the user: Please provide a valid 10-digit phone number",
        )
    return ValidationStatus.VALID, None


# input context 
@input_context_provider()
async def get_current_date(
    session: InterviewSession, visitor: InteractWalker
) -> Dict[str, Any]:
    """Provide current date."""
    now = datetime.now()
    date_str = now.strftime("%A, %d %B, %Y")

    return {"date": date_str}


# branch function
@branch_function("can_ask_for_supervisor_name")
async def can_ask_for_supervisor_name(
    session: InterviewSession, visitor: InteractWalker
) -> bool:
    """Search for completed reports matching the user's description."""

    logger = logging.getLogger(__name__)
    logger.warning("can ask for supervisor")

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

    if rank_profile:
        if rank_profile.get("supervisor", {}):
            session.context[
                "supervisor_name"
            ] = f"{rank_profile['supervisor']['first_name']} {rank_profile['supervisor']['last_name']}"
            session.context["supervisor_phone_number"] = rank_profile["supervisor"][
                "phone"
            ]
            session.context["supervisor_rank_number"] = rank_profile["supervisor"][
                "regimental_number"
            ]
            session.context["rank_number"] = rank_profile.get("regimental_number", "")

            if isinstance(rank_profile.get("rank"), dict):
                session.context["rank_name"] = rank_profile.get("rank", {}).get(
                    "name", "Unknown"
                )
                session.context["rank_rank"] = rank_profile.get("rank", {}).get(
                    "full_name", "Unknown"
                )
            else:
                session.context["rank_name"] = "Unknown"
                session.context["rank_rank"] = "Unknown"

            if isinstance(rank_profile.get("unit"), dict):
                session.context["unit"] = rank_profile.get("unit", {}).get(
                    "name", "Unknown"
                )
            else:
                session.context["unit"] = "Unknown"

            if isinstance(rank_profile.get("sub_unit"), dict):
                session.context["sub_unit"] = rank_profile.get("sub_unit", {}).get(
                    "name", "Unknown"
                )
            else:
                session.context["sub_unit"] = "Unknown"
            return False

    return True


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
            # Note: Standard anchors (cancellation, correction, review, etc.)
            # are automatically merged with these implementation-specific anchors
        ],
        description="Anchor statements for InteractRouter routing",
    )

    question_graph: List[Dict[str, Any]] = attribute(
        default_factory=lambda: [
            {
                "name": "start_date",
                "input_context_provider": "get_current_date",
                "question": "What is your proposed start date of your short pass?",
                "constraints": {
                    "description": "A date which marks the beginning of the engagement or leave request.",
                    "instruction": "The current date is <datetime> - use this as a reference to accurately resolve, format and extract a start date specified. Note that the start date and end date are counted as day 1. Format date as '%A, %B %d, %Y'.",
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
                    "instruction": "The current date is <datetime> - use this as a reference to accurately resolve, format and extract an end date specified. Note that the end date and start date are counted as day 1. Format date as '%A, %B %d, %Y'.",
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
                    "instruction": "Only select a 'yes' or 'no' if the user directly responds to traveling overseas or mention if they are traveling or not. leave blank otherwise.",
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
                    "instruction": "The full address must include lot / apartment, street name area / city, state (if applicable) and country",
                    "type": "string",
                },
                "default_next": "overseas_contact_number",
                "required": True,
            },
            {
                "name": "overseas_contact_number",
                "question": "What is your overseas contact number?",
                "constraints": {
                    "description": "A complete phone number in various formats",
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
                    "instruction": "Only select a 'yes' or 'no' if the user directly responds to being under base confinement or mention if they are confined or not. leave blank otherwise.",
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

    short_pass_review_template: str = """*Short Pass Request*

    *Name:* <rank_rank> <rank_name> (<rank_number>)
    *Unit/Subunit:* <unit> <sub_unit>
    *Supervisor:* <supervisor_name> ( <supervisor_rank_number> )

    *Dates:*  <start_date> to <end_date>

    *Reason:* <reason_for_pass>

    % if overseas_travel == "yes" %
    *Overseas Address:* <overseas_address>
    *Overseas Contact:* <overseas_contact_number>
    % endif %
    """




@on_interview_complete('ShortPassInterviewInteractAction')
async def handle_short_pass_completion(
    session: InterviewSession,
    visitor: InteractWalker,
    action: InteractAction
) -> None:
    """Handle completion of short pass interview."""

    arc_api_action = await action.get_action("ArcAPIAction")
    completion_message = "Tell the user: Sorry I was unable to submit your short pass. Please try again later!"
    if arc_api_action:
        logger.warning(json.dumps(session.responses, indent=4))

        # session responses
        under_confinement = session.responses.get('under_confinement', "N/A")
        overseas_travel = session.responses.get('overseas_travel', "N/A")
        start_date = session.responses.get('start_date', "N/A")
        end_date = session.responses.get('end_date', "N/A")
        reason_for_pass = session.responses.get('reason_for_pass', "N/A")
        overseas_address = session.responses.get('overseas_address', "N/A")
        overseas_contact_number = session.responses.get('overseas_contact_number', "N/A")

        # session context 
        supervisor_name = session.responses.get('supervisor_name') or session.context.get('supervisor_name')
        supervisor_phone_number = session.responses.get('supervisor_phone_number') or session.context.get('supervisor_phone_number')
        
        supervisor_rank_number = session.context.get('supervisor_rank_number')
        rank_number = session.context.get('rank_number')
        rank_name = session.context.get('rank_name')
        rank_rank = session.context.get('rank_rank')
        unit = session.context.get('unit')
        sub_unit = session.context.get('sub_unit')


        # generated content 
        now = datetime.now()
        applied_on = now.strftime("%Y-%m-%d")
        reviewed_on = applied_on

        # Infers the type of pass based on interview responses
        if under_confinement == "yes" or overseas_travel == "yes":
            pass_type = "CONFINEMENT" if under_confinement == "yes" else "OVERSEAS"
        else:
            pass_type = "TRADITIONAL"

        # convert content 
        start_date = datetime.strptime(start_date, "%A, %B %d, %Y").strftime("%Y-%m-%d")
        end_date = datetime.strptime(end_date, "%A, %B %d, %Y").strftime("%Y-%m-%d")
        

        short_pass_review_template: str = """*Short Pass Request*

*Name:* <rank_rank> <rank_name> (<rank_number>)
*Unit/Subunit:* <unit> <sub_unit>
*Supervisor:* <supervisor_name> (<supervisor_rank_number>)

*Dates:*  <start_date> to <end_date>

*Reason:* <reason_for_pass>

% if overseas_travel == 'yes' %
*Overseas Address:* <overseas_address>
*Overseas Contact:* <overseas_contact_number>
% endif %
"""

        review_template_str = short_pass_review_template
        if pass_type != "OVERSEAS":
            review_template_str = short_pass_review_template.split("% if overseas_travel == 'yes' %")[0]
            review_template_str = review_template_str.replace("<overseas_address>\n", "")
            review_template_str = review_template_str.replace("<overseas_contact_number>\n", "")
        
        
        review_template_str = review_template_str.replace("\n% if overseas_travel == 'yes' %", "")
        review_template_str = review_template_str.replace("% endif %\n", "")
        review_template_str = review_template_str.replace("<rank_number>", rank_number)
        review_template_str = review_template_str.replace("<rank_name>", rank_name)
        review_template_str = review_template_str.replace("<rank_rank>", rank_rank)
        # review_template_str = review_template_str.replace("<unit>", unit)
        # review_template_str = review_template_str.replace("<sub_unit>", sub_unit)
        if unit != "Unknown" and sub_unit != "Unknown":
            unit_or_sub_unit = f"{unit} {sub_unit}"
        else:
            unit_or_sub_unit = unit if unit != "Unknown" else sub_unit
        review_template_str = review_template_str.replace("<unit> <sub_unit>", unit_or_sub_unit)

        review_template_str = review_template_str.replace("<supervisor_name>", supervisor_name)
        review_template_str = review_template_str.replace("<supervisor_rank_number>", supervisor_rank_number)
        review_template_str = review_template_str.replace("<start_date>", start_date)
        review_template_str = review_template_str.replace("<end_date>", end_date)
        review_template_str = review_template_str.replace("<reason_for_pass>", reason_for_pass)
        review_template_str = review_template_str.replace("<overseas_address>", overseas_address)
        particulars = review_template_str.replace("<overseas_contact_number>", overseas_contact_number)
        


        # short_pass_id = await arc_api_action.create_short_pass(
        #     rank_number=rank_number,
        #     rank_name=rank_name,
        #     rank_phone_number=visitor.user_id,
        #     pass_type=pass_type,
        #     start_date=start_date,
        #     end_date=end_date,
        #     reason_for_pass=reason_for_pass,
        #     particulars=particulars,
        #     supervisor=supervisor_name,
        #     supervisor_phone_number=supervisor_phone_number,
        #     applied_on=applied_on,
        #     reviewed_on=reviewed_on
        # )
        short_pass_id = "95"

        if short_pass_id:
            supervisor_message = particulars.replace("*Short Pass Request*\n", f"*Short Pass Request*\n*Reference Number*: {short_pass_id}\n")
            logger.warning(supervisor_message)
            whatsapp_action = await action.get_action("WhatsAppAction")
            completion_message = f"Tell the user: Thank you for your short pass! Here is your reference number for follow-up: {short_pass_id}"
            # if whatsapp_action:
            #     message_result = await whatsapp_action.api().send_message(
            #         phone=supervisor_phone_number,
            #         message=supervisor_message
            #     )

            #     if not message_result.get("success", False):
            #         completion_message = f"Tell the user: Thank you for your short pass! Here is your reference number for follow-up: {short_pass_id}. However, I was unable to send the message to your supervisor. Please contact them directly."

    else:
        logger.error("ArcAPIAction not found for short pass submission")

    await action.respond(visitor, directives=[completion_message])
    await session.cleanup()
