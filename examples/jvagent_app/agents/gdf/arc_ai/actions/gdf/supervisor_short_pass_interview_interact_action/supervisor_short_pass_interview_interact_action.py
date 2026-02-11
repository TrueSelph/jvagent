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
from jvagent.action.arc_api_action import ArcApiAction # Added import for ArcApiAction

logger = logging.getLogger(__name__)


# input validator 
@input_validator("short_pass_reference_number")
async def validate_short_pass_reference_number(
    value: str, session: InterviewSession
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate and fetch short pass details."""
    if not value:
        return (
            ValidationStatus.INVALID,
            "Ask: Please provide the short pass reference number",
        )

    # Note: Using action locally or from context if needed.
    # Typically, API actions can be instantiated directly.
    arc_api_action = ArcApiAction()
    short_pass = await arc_api_action.get_short_pass(value)

    if short_pass:
        session.context["short_pass_details"] = short_pass
        return ValidationStatus.VALID, None

    return (
        ValidationStatus.INVALID,
        f"Tell the user: No short pass found for reference number {value}. Please check and try again.",
    )


@input_validator("supervisor_feedback")
def validate_supervisor_feedback(
    value: str, session: InterviewSession
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate supervisor feedback is not empty."""
    if not value or not isinstance(value, str):
        return ValidationStatus.INVALID, "Ask: Please provide your feedback/remarks"

    value = value.strip()
    if len(value) < 10:
        return (
            ValidationStatus.INVALID,
            "Ask: Please provide more detailed remarks (at least 10 characters)",
        )

    return ValidationStatus.VALID, None


@input_validator("approval_status")
def validate_approval_status(
    value: str, session: InterviewSession
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate approval status is 'approved' or 'denied'."""
    if not value or not isinstance(value, str):
        return (
            ValidationStatus.INVALID,
            "Ask: Do you approve or deny this short pass request?",
        )

    value = value.strip().lower()
    if value not in ["approved", "denied"]:
        return (
            ValidationStatus.INVALID,
            "Ask: Please respond with either 'approved' or 'denied'",
        )

    return ValidationStatus.VALID, None


# branch function
@branch_function("get_reference_number")
async def get_reference_number(
    session: InterviewSession, visitor: Optional[InteractWalker] = None
) -> bool:
    """Helper to determine if reference number is already in context."""
    # This might be used to skip the first question if reference is passed in context
    return "short_pass_reference_number" not in session.context


class SupervisorShortPassInterviewInteractAction(InterviewInteractAction):
    """Supervisor Short Pass Interview action lets supervisors review short pass requests.

    This is a concrete implementation of InterviewInteractAction that defines
    a specific interview flow. Sessions are identified by
    interview_type='SupervisorShortPassInterviewInteractAction' and attached to Conversation nodes.

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
        "Supervisor Short Pass Interview action lets supervisors review short pass requests."
    )

    # DSPy Integration
    use_dspy: bool = attribute(
        default=True,
        description="Use DSPy module for classification (enables optimization via DSPy teleprompters)"
    )

    # REQUIRED when using InteractRouter: Anchors for intelligent routing
    anchors: List[str] = attribute(
        default_factory=lambda: [
            "Supervisor wants to review a short pass request",
            "Supervisor is approving or denying a short pass",
            "Supervisor is providing remarks on a short pass",
            # Note: Standard anchors (cancellation, correction, review, etc.)
            # are automatically merged with these implementation-specific anchors
        ],
        description="Anchor statements for InteractRouter routing",
    )

    question_graph: List[Dict[str, Any]] = attribute(
        default_factory=lambda: [
            {
                "name": "short_pass_reference_number",
                "question": "Please provide the short pass reference number you wish to review.",
                "constraints": {
                    "description": "The reference number for the short pass request.",
                    "type": "string",
                },
                "branches": [
                    {
                        "condition": {"function": "get_reference_number"},
                        "target": "short_pass_reference_number",
                    }
                ],
                "default_next": "approval_status",
                "required": True,
            },
            {
                "name": "approval_status",
                "question": "Do you approve or deny this short pass request?",
                "constraints": {
                    "description": "The approval status of the short pass request, either 'approved' or 'denied'.",
                    "type": "string",
                    "items": ["approved", "denied"],
                },
                "default_next": "supervisor_feedback",
                "required": True,
            },
            {
                "name": "supervisor_feedback",
                "question": "Please provide your feedback or remarks regarding this decision.",
                "constraints": {
                    "description": "Supervisor's remarks or feedback on the short pass request.",
                    "type": "string",
                },
                "default_next": "REVIEW",
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

    confirmation_directive: str = """Perform the following steps to confirm user submission:
    a. Directly and concisely state: 'Should I submit as *{short_pass_status}* the {type_of_pass} short pass request made by *{rank_name}* for the dates: *{start_date} to {end_date}* ?'
    b. If short pass comment is not empty or is not 'N/A', include in a new paragraph:
    'With comment:
    _{short_pass_comment}_'
    c. Finally, in a new paragraph state: 'Feel free to amend your decision or cancel altogether.'
    """
    _visitor: InteractWalker = None

    # Helper function
    async def _get_model_action(self, user_prompt: str, system_prompt: str, json_response: bool = False):
        try:

            model_action = await self.get_model_action()
            if not model_action:
                logger.warning("No model action found")
                return False
            
            if json_response:
                result_str = await model_action.generate(
                    prompt=user_prompt,
                    stream=False,
                    system=system_prompt,
                    model=self.config.model.model,
                    temperature=self.config.model.model_temperature,
                    max_tokens=self.config.model.model_max_tokens,
                    response_format={"type": "json_object"}
                )

                json_match = re.search(r'```(?:json)?\s*({.*?})\s*```', result_str, re.DOTALL)
                if json_match:
                    result_str = json_match.group(1)
                elif result_str.strip().startswith('{'):
                    result_str = result_str.strip()
                else:
                    json_match = re.search(r'{.*}', result_str, re.DOTALL)
                    result_str = json_match.group(0) if json_match else result_str.strip()
                    
                return json.loads(result_str)
            else:
                return await model_action.generate(
                    prompt=user_prompt,
                    stream=False,
                    system=system_prompt,
                    model=action.config.model.model,
                    temperature=action.config.model.model_temperature,
                    max_tokens=action.config.model.model_max_tokens,
                )
        except Exception as e:
            logger.error(f"Error in LLM helper: {e}")
            return None


    # validators 
    @input_validator('short_pass_reference_number')
    async def validate_short_pass_reference_number(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate that the short pass reference number is not empty."""
        
        if not value or not isinstance(value, str):
            return ValidationStatus.INVALID, "Ask: Please provide the short pass reference number"

        arc_api_action = ArcApiAction()
        short_pass = await arc_api_action.get_short_pass(value)
        if short_pass:
            session.context["short_pass_details"] = short_pass
            return ValidationStatus.VALID, None
            
        return ValidationStatus.INVALID, f"Ask: Sorry I was unable to find the short pass using the reference number: {value}. Please provide a valid short pass reference number"
        
    @input_validator('short_pass_status')
    def validate_short_pass_status(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate that the short pass status is not empty."""
        
        if value.lower() not in ["approve", "reject"]:
            return ValidationStatus.INVALID, "Ask: Please provide 'approve' or 'reject' for short pass status"

        return ValidationStatus.VALID, None

    
    # Branch Functions
    @branch_function('get_reference_number')
    async def get_reference_number(
        session: InterviewSession,
        visitor: InteractWalker
    ) -> bool:
        """Extract reference number from quoted message."""
        quoted_message = visitor.data.get("quoted_message", "")
        short_pass_reference_number = quoted_message.split("*Reference Number*: ")[1].split()[0]
        if short_pass_reference_number:
            arc_api_action = ArcApiAction()
            short_pass = await arc_api_action.get_short_pass(short_pass_reference_number)
            if short_pass:
                session.context["short_pass_reference_number"] = short_pass_reference_number
                session.context["short_pass_details"] = short_pass
                return True
        return False


    # Review Overrides
    @input_review_override
    def adapt_short_pass_review_for_display(
        session: InterviewSession,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Omit or format values shown in the Review state (display only; session unchanged).

        Applies only to ShortPassInterviewInteractAction in this module.
        """

        result: Dict[str, Any] = {}
        for field_name, value in data.items():
            if value is None or value == "" or (isinstance(value, str) and value.strip().lower() in ("n/a", "na")):
                continue
            else:
                result[field_name] = value

        for key, value in session.context.items():
            if key in ['short_pass_reference_number'] and "short_pass_reference_number" not in result:
                result[key] = value
                
        return result



@on_interview_complete('ShortPassInterviewInteractAction')
async def handle_short_pass_completion(
    session: InterviewSession,
    visitor: InteractWalker,
    action: InteractAction
) -> None:
    """Handle completion of short pass interview."""

    logger.warning(json.dumps(session.responses, indent=4))

    arc_api_action = await action.get_action("ArcAPIAction")
    completion_message = "Tell the user: Sorry I was unable to process your request at this time. Please try again later!"
    if arc_api_action:
        short_pass_status = session.responses.get('short_pass_status')
        short_pass_reference_number = session.responses.get('short_pass_reference_number') or session.context.get('short_pass_reference_number')
        comments = session.responses.get('comments', 'no comments')

        short_pass_details = session.context.get('short_pass_details')
        completion_message = f"Tell the user: Sorry, your request to {short_pass_status} the short pass for rank {short_pass_reference_number} could not be processed at this time. Please try again later."


        # get short pass using ref number
        

        rank_update_message: str = """*{supervisor}* has *{short_pass_status}* your {type_of_pass} short pass request for the dates: *{start_date} to {end_date}*

{short_pass_comment}"""
        rank_update_message_str = rank_update_message.format(
            supervisor=short_pass_details.get('supervisor'),
            short_pass_status=short_pass_status,
            type_of_pass=short_pass_details.get('pass_type'),
            start_date=short_pass_details.get('start_date'),
            end_date=short_pass_details.get('end_date'),
            short_pass_comment=comments
        )
        rank_update_message_str = rank_update_message_str.replace("\nno comments", "")

        result = await arc_api_action.update_short_pass(
            reference_number=short_pass_reference_number,
            status=short_pass_status,
            comments=comments
        )
        if result:
            completion_message = f"Tell the user: Your request to {short_pass_status} the short pass for rank {short_pass_reference_number} has been processed successfully. The rank will be notified shortly."
            whatsapp_action = await action.get_action("WhatsAppAction")
            if whatsapp_action:
                message_result = await whatsapp_action.api().send_message(
                    phone=visitor.user_id,
                    message=supervisor_message
                )

                if not message_result.get("success", False):
                    completion_message = f"Tell the user: Thank you for your short pass! Here is your reference number for follow-up: {short_pass_id}. However, I was unable to send the message to your supervisor. Please contact them directly."

    else:
        logger.error("ArcAPIAction not found for short pass submission")
    await action.respond(visitor, directives=[completion_message])
    await session.cleanup()
