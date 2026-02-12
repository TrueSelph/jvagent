"""Supervisor Short Pass Interview action lets supervisors review short pass requests."""
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
    input_directive_override,
    input_review_override,
    input_validator,
    on_interview_complete,
)
from jvagent.action.interview.core.foundation.enums import ValidationStatus
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.memory import Interaction


logger = logging.getLogger(__name__)


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
                        "condition": {"function": "skip_ref_if_known"},
                        "target": "approval_status",
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
        description="List of question configurations defining the interview graph.",
    )

    # Helper function
    # async def _get_()


    # Validators
    @input_validator("short_pass_reference_number")
    async def validate_short_pass_reference_number(
        self, value: str, session: InterviewSession, visitor: Optional[InteractWalker] = None, interview_action: Optional[Any] = None
    ) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate and fetch short pass details using the reference number."""
        if not value:
            return (
                ValidationStatus.INVALID,
                "Ask: Please provide the short pass reference number.",
            )

        arc_api_action = await self.get_action("ArcAPIAction")
        short_pass = await arc_api_action.get_short_pass(value)

        if short_pass:
            session.context["short_pass_details"] = short_pass
            return ValidationStatus.VALID, None

        return (
            ValidationStatus.INVALID,
            f"Tell the user: I couldn't find a short pass with the reference number {value}. Please check and try again.",
        )

    @input_validator("approval_status")
    def validate_approval_status(
        self, value: str, session: InterviewSession, visitor: Optional[InteractWalker] = None, interview_action: Optional[Any] = None
    ) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate that approval status is either 'approved' or 'denied'."""
        if not value or not isinstance(value, str):
            return (
                ValidationStatus.INVALID,
                "Ask: Do you approve or deny this short pass request?",
            )

        value = value.strip().lower()
        if value not in ["approved", "denied"]:
            return (
                ValidationStatus.INVALID,
                "Ask: Please respond with either 'approved' or 'denied'.",
            )

        return ValidationStatus.VALID, None

    @input_validator("supervisor_feedback")
    def validate_supervisor_feedback(
        self, value: str, session: InterviewSession, visitor: Optional[InteractWalker] = None, interview_action: Optional[Any] = None
    ) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate that supervisor feedback is not empty and has sufficient length."""
        if not value or not isinstance(value, str):
            return ValidationStatus.INVALID, "Ask: Please provide your feedback or remarks."

        value = value.strip()
        if len(value) < 5:
            return (
                ValidationStatus.INVALID,
                "Ask: Please provide more detailed remarks.",
            )

        return ValidationStatus.VALID, None

    # Branch functions
    @branch_function("skip_ref_if_known")
    async def skip_ref_if_known(
        session: InterviewSession, visitor: Optional[InteractWalker] = None
    ) -> bool:
        """Determine if we can skip the reference number question."""
        # Check if reference is already in context from a quoted message
        quoted_message = visitor.data.get("quoted_message", "") if visitor else ""
        
        if "*Reference Number*:" in quoted_message:
            try:
                ref_num = quoted_message.split("*Reference Number*:")[1].split()[0].strip()
                if ref_num:
                    arc_api_action = await ArcAPIAction.find_one({
                        "context.enabled": True
                    })
                    short_pass = await arc_api_action.get_short_pass(ref_num)
                    if short_pass:
                        session.context["short_pass_reference_number"] = ref_num
                        session.context["short_pass_details"] = short_pass
                        session.responses["short_pass_reference_number"] = ref_num
                        return True
            except (IndexError, Exception) as e:
                logger.warning(f"Failed to extract ref number from quote: {e}")

        return "short_pass_reference_number" in session.context or "short_pass_reference_number" in session.responses

    # Helper function
    async def _get_model_action(self, user_prompt: str, system_prompt: str, json_response: bool = False):
        """Internal helper to interact with the LLM."""
        try:
            model_action = await self.get_model_action()
            if not model_action:
                logger.warning("No model action found.")
                return None
            
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
                    model=self.config.model.model,
                    temperature=self.config.model.model_temperature,
                    max_tokens=self.config.model.model_max_tokens,
                )
        except Exception as e:
            logger.error(f"Error in LLM helper: {e}")
            return None


# Review override
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
        if key == 'short_pass_reference_number' and key not in result:
            result_ending[key] = value
            
    result.update(result_ending)
    return result


@on_interview_complete('SupervisorShortPassInterviewInteractAction')
async def handle_interview_completion(
    session: InterviewSession,
    visitor: InteractWalker,
    action: InteractAction
) -> None:
    """Handle completion of supervisor's short pass review."""
    logger.warning(f"Supervisor responses: {json.dumps(session.responses, indent=4)}")

    arc_api_action = await action.get_action("ArcAPIAction")
    completion_message = "Tell the user: Sorry, I was unable to process your decision at this time. Please try again later!"
    
    if arc_api_action:
        approval_status = session.responses.get('approval_status')
        short_pass_reference_number = (session.responses.get('short_pass_reference_number') or 
                                       session.context.get('short_pass_reference_number'))
        comments = session.responses.get('supervisor_feedback', 'No remarks provided.')
        short_pass_details = session.context.get('short_pass_details', {})

        result = await arc_api_action.update_short_pass(
            reference_number=short_pass_reference_number,
            status=approval_status,
            comments=comments
        )

        if result:
            completion_message = (
                f"Tell the user: Your decision to {approval_status} the short pass request "
                f"({short_pass_reference_number}) has been processed successfully. The rank will be notified."
            )
            
            # Logic for notifying the rank via WhatsApp would go here
            # rank_notification = f"Your supervisor has {approval_status} your short pass request. Remarks: {comments}"
            
    else:
        logger.error("ArcAPIAction not found for short pass update.")

    await action.respond(visitor, directives=[completion_message])
    await session.cleanup()
