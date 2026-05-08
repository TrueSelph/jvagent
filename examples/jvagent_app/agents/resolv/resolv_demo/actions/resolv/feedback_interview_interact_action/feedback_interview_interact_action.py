"""Feedback interview for feedback submission."""
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


class FeedbackInterviewInteractAction(InterviewInteractAction):
    """Feedback Interview action is used to provide **feedback, updates, or follow-ups** on an existing report, project, or completed work.

    This is a concrete implementation of InterviewInteractAction that defines
    a specific interview flow. Sessions are identified by
    interview_type='FeedbackInterviewInteractAction' and attached to Conversation nodes.

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

    description: str = (
        "Feedback Interview action is used to provide **feedback, updates, or follow-ups** on an existing report, project, or completed work."
    )

    # REQUIRED when using InteractRouter: Anchors for intelligent routing
    # Must cover both initial entry and intermediate states (when answering questions)
    anchors: List[str] = attribute(
        default_factory=lambda: [
            "User provides a reference number.",
            "User follows up on a previously submitted issue.",
            "User gives feedback on completed work or a resolved report.",
            "User provides additional details after a report was confirmed.",
            "User uploads new evidence or details for an existing report.",
            "User corrects previously submitted feedback.",
        ],
        description="Anchor statements for InteractRouter routing",
    )

    question_graph: List[Dict[str, Any]] = attribute(
        default_factory=lambda: [
            {
                "name": "feedback_content",
                "question": "Please share your feedback.",
                "constraints": {
                    "description": "Full details about the feedback the user wants to provide.",
                    "instruction": "Feedback content is not a request to give feedback. It is just the content of the feedback.",
                    "type": "string",
                },
                "branches": [
                    {
                        "condition": {"function": "can_ask_for_media"},
                        "target": "feedback_media",
                    }
                ],
                "default_next": "reference_number",
                "required": True,
            },
            {
                "name": "feedback_media",  # capture media if user provides it, do not ask for media
                "question": "Do you have any media to upload along with your feedback?",
                "constraints": {
                    "description": "Media of feedback uploaded by user.",
                    "type": "list",
                    "data_input_field": "whatsapp_media",
                },
                "default_next": "reference_number",
                "required": False,
            },
            {
                "name": "reference_number",
                "question": "Do you have a report reference number for a report you want to give your feedback on? If not, you can skip this to provide your feedback on the project.",
                "constraints": {
                    "description": "Reference number of the report the user wants to provide feedback on.",
                    "instruction": "Reference number can be skipped to provide feedback on the project.",
                    "type": "string",
                },
                "default_next": "REVIEW",
                "required": False,
            }
        ],
        description="List of question configurations defining the interview graph. Can be overridden in agent.yaml. "
                    "Supports conditional branching via 'branches' and 'default_next'. "
                    "Enhanced condition operators are supported (==, !=, >, >=, <, <=, in, contains, exists, matches). "
                    "Example: {\"condition\": {\"question\": \"age\", \"operator\": \">=\", \"value\": 18}, \"target\": \"next_question\"} "
                    "Handlers, validators, and directive overrides can be registered via decorators "
                    "(@input_handler, @input_validator, @input_directive_override) or specified as string "
                    "references in constraints (input_handler, input_validator)."
    )

    can_ask_for_media_prompt: str = "You are an assistant deciding if it's appropriate to ask for media (photos, videos, audio) based on feedback provided by a user. If the feedback describes violence, abuse, threats, emergencies, physical issues, damage, evidence, or any situation where media (including voice recordings) could provide evidence or context, it is appropriate to ask. Analyze the feedback for any direct or indirect references to these situations. Return a JSON object with a single boolean field 'should_ask', set to true if any of these conditions are met, otherwise false."


    # Helper function
    async def _call_model(self, user_prompt: str, system_prompt: str, json_response: bool = False):
        """
        Call the language model and return the response.

        Args:
            user_prompt: The user's input/question
            system_prompt: System instruction defining model behavior
            json_response: If True, parse response as JSON (default: False)

        Returns:
            - If json_response=True: Parsed JSON dict on success
            - If json_response=False: Raw string response
            - False if model action unavailable
            - None if exception occurs

        Example:
            # Text response
            response = await self._call_model(
                user_prompt="What is Python?",
                system_prompt="You are a programming expert."
            )

            # JSON response
            data = await self._call_model(
                user_prompt="List 3 Python frameworks",
                system_prompt="Return JSON",
                json_response=True
            )
        """
        try:
            model_action = await self.get_model_action()
            if not model_action:
                return False

            if json_response:
                result_str = await model_action.generate(
                    prompt=user_prompt,
                    stream=False,
                    system=system_prompt,
                    model=self.model,
                    temperature=self.model_temperature,
                    max_tokens=self.model_max_tokens,
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
                    model=self.model,
                    temperature=self.model_temperature,
                    max_tokens=self.model_max_tokens,
                )
        except Exception as e:
            logger.error(f"Error in LLM helper: {e}")
            return None


    @branch_function()
    async def can_ask_for_media(
        session: InterviewSession,
        visitor: Optional[InteractWalker] = None,
        interview_action: Optional[Any] = None,
    ) -> bool:
        """Check if the user can be asked for media using LLM reasoning."""

        feedback_content = session.responses.get("feedback_content", "")
        if feedback_content:
            user_prompt = f"Feedback content: {feedback_content}"

            try:
                result_json = await interview_action._call_model(user_prompt, interview_action.can_ask_for_media_prompt, json_response=True)
                if result_json and isinstance(result_json, dict):
                    logger.warning(f"Should ask for media: {result_json}")
                    should_ask = result_json.get("should_ask", False)
                    logger.warning(f"Should ask for media: {type(should_ask)}")
                    return should_ask
            except Exception as e:
                logger.error(f"Error in can_ask_for_media: {e}")

        return False


    # input validator
    @input_validator("feedback_content")
    async def validate_feedback_content(
        value: str,
        session: InterviewSession,
        visitor: Optional[InteractWalker] = None,
        interview_action: Optional[Any] = None,
    ) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate feedback content is detailed and constructive."""

        if not value or not isinstance(value, str):
            return ValidationStatus.INVALID, "Ask: Please share your feedback."

        value = value.strip()
        if len(value) < 10:
            return (
                ValidationStatus.INVALID,
                "Ask: Please provide more detailed feedback about your experience",
            )
        return ValidationStatus.VALID, None

    @input_validator("reference_number")
    async def validate_reference_number(
        value: str,
        session: InterviewSession,
        visitor: Optional[InteractWalker] = None,
        interview_action: Optional[Any] = None,
    ) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate reference number is detailed and constructive."""
        logger = logging.getLogger(__name__)

        if not value or not isinstance(value, str):
            return ValidationStatus.INVALID, "Ask: Please share your feedback."

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
        elif field_name == "feedback_media" and value:
            media_links = ""
            for link in value:
                media_links += f"{link}\n"
            result_ending[field_name] = media_links
        else:
            result[field_name] = value

    # add items at the end of the dict
    result.update(result_ending)

    return result



@on_interview_complete('FeedbackInterviewInteractAction')
async def handle_interview_completion(
    session: InterviewSession,
    visitor: InteractWalker,
    action: InteractAction
) -> None:
    """Handle completion of feedback interview."""

    # logger.warning(f"Interview responses: {json.dumps(session.responses, indent=4)}")

    feedback_content = session.responses.get('feedback_content', '')
    reference_number = session.responses.get('reference_number', '')
    if reference_number == "N/A":
        reference_number = ""

    feedback_media = session.responses.get('feedback_media')
    if not isinstance(feedback_media, list):
        feedback_media = []


    completion_message = "Tell the user: Sorry, I was unable to submit your feedback. Please try again later!"
    resolv_api_action = await action.get_action("ResolvAPIAction")
    if resolv_api_action:
        result = await resolv_api_action.submit_comment(
            content=feedback_content,
            report_id=reference_number,
            attachments=feedback_media
        )
        if result:
            completion_message = "Tell the user: Thank you for your feedback! Your input helps us improve our services."
    else:
        logger.error("ResolvAPIAction not found for feedback submission")

    await action.respond(visitor, directives=[completion_message])
    await session.cleanup()


