"""Custom tools for the example_interview reference skill.

Functions are loaded by ``function:`` name in SKILL.md frontmatter ``interview:``. This file
demonstrates every hook type used by live interview skills. Sections:

1. Constants
2. Shared helpers — _get_conversation
3. Validators — validate_rating
4. Input context providers — suggest_email (pre_tool; pattern from onboarding)
5. Custom tools — check_low_rating (post_tool), reset_example_interview
6. Review handler — example_review (pattern from pre_alert)
7. Completion handler — example_complete

Copy this folder to skills/<your_skill_name>/ and adapt function names to
match your frontmatter ``interview:`` references.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional

from jvagent.action.interview_action.core.responses import (
    call_tool_directive,
    interview_tool_response,
    no_session_directive,
    tell_user_directive,
    tell_user_with_followup_directive,
)

logger = logging.getLogger(__name__)

_SKILL_NAME = "example_interview"
_LOW_RATING_THRESHOLD = 2
_ESCALATION_MESSAGE = (
    "Thank you for your honest feedback. A team member will reach out to you "
    "shortly to address your concerns."
)


# ─── Shared helpers ──────────────────────────────────────────────────


async def _get_conversation(visitor: Any) -> Any:
    if visitor is None:
        return None
    if hasattr(visitor, "conversation") and visitor.conversation is not None:
        return visitor.conversation
    interaction = getattr(visitor, "interaction", None)
    if interaction is not None and hasattr(interaction, "get_conversation"):
        try:
            return await interaction.get_conversation()
        except Exception:
            pass
    return None


# ─── Validators ──────────────────────────────────────────────────────


def extract_rating_candidates(user_message: str, **kwargs) -> list:
    """Surface integer rating candidates from the user's latest message."""
    matches = re.findall(r"\b([1-5])\b", user_message or "")
    return list(dict.fromkeys(matches))


async def validate_rating(value: str, **kwargs) -> str:
    """Validate product rating (integer 1–5)."""
    cleaned = re.sub(r"\D", "", str(value or "").strip())
    if not cleaned:
        return json.dumps(
            {
                "valid": False,
                "error": "Please provide a rating from 1 to 5.",
                "value": value,
                "validator": "validate_rating",
            }
        )
    try:
        rating = int(cleaned)
    except ValueError:
        return json.dumps(
            {
                "valid": False,
                "error": "Please provide a whole number from 1 to 5.",
                "value": value,
                "validator": "validate_rating",
            }
        )
    if rating < 1 or rating > 5:
        return json.dumps(
            {
                "valid": False,
                "error": "Rating must be between 1 and 5.",
                "value": value,
                "validator": "validate_rating",
            }
        )
    return json.dumps(
        {
            "valid": True,
            "value": str(rating),
            "validator": "validate_rating",
        }
    )


# ─── Input context providers (pre_tools) ─────────────────────────────


async def suggest_email(
    session: Any = None,
    visitor: Any = None,
    **kwargs,
) -> Dict[str, Any]:
    """Pre-tool for follow_up_email — suggests email from conversation context.

    Pattern from onboarding_interview ``get_phone_number``: return a
    suggested_value and directive; the LLM must confirm before set_field.
    """
    conversation = await _get_conversation(visitor)
    if conversation is None:
        return {}

    ctx = getattr(conversation, "context", None)
    if not isinstance(ctx, dict):
        return {}

    # Example: read a previously stored email from another flow.
    suggested = (ctx.get("last_known_email") or "").strip()
    if not suggested or "@" not in suggested:
        return {}

    return {
        "ok": True,
        "suggested_value": suggested,
        "directive": tell_user_directive(
            f"We have {suggested} on file. Would you like to use this for follow-up?",
            note=(
                "On the user's NEXT message: if they confirm (yes/ok/sure), call "
                f"interview__set_field(field='follow_up_email', value='{suggested}'). "
                "If they provide a different email, save that instead."
            ),
        ),
    }


# ─── Custom tools (post_tools + LLM-callable) ────────────────────────


async def check_low_rating(
    visitor: Any = None,
    interview_action: Any = None,
    session: Any = None,
    **kwargs,
) -> str:
    """Post-tool after product_rating — escalate low ratings to review.

    Pattern from pre_alert_interview ``check_tracking_status``: set session
    context and return skip_to_review so the LLM jumps to review.
    """
    if session is None:
        return interview_tool_response(
            ok=False,
            status="error",
            error_code="NO_SESSION",
            system_message="No active interview session for rating check.",
            skip_to_review=False,
            response_directive=no_session_directive(),
        )

    rating_str = (session.get_value("product_rating") or "").strip()
    try:
        rating = int(rating_str)
    except ValueError:
        return interview_tool_response(
            ok=False,
            status="error",
            error_code="INVALID_RATING",
            system_message="Product rating not yet stored in session.",
            skip_to_review=False,
            response_directive=call_tool_directive("interview__set_field"),
        )

    if rating <= _LOW_RATING_THRESHOLD:
        if not isinstance(session.context, dict):
            session.context = {}
        session.context["escalate"] = True
        session.context["escalation_reason"] = f"low_rating_{rating}"

        if interview_action:
            await interview_action._save_session(session, visitor)

        return interview_tool_response(
            ok=True,
            status="escalation",
            skip_to_review=True,
            system_message=f"Rating {rating} triggers escalation — skip remaining questions.",
            next_tool="interview__review",
            response_directive=call_tool_directive("interview__review"),
        )

    return interview_tool_response(
        ok=True,
        status="ok",
        skip_to_review=False,
        system_message="Rating accepted — continue collecting feedback.",
    )


async def send_followup_reminder(
    visitor: Any = None,
    session: Any = None,
    **kwargs,
) -> str:
    """LLM-callable demo tool — records that a follow-up reminder was queued."""
    email = (session.get_value("follow_up_email") if session else "") or ""
    if not email:
        return interview_tool_response(
            ok=False,
            status="error",
            response_directive=tell_user_directive(
                "I need an email on file before queuing a follow-up reminder."
            ),
        )
    if session is not None and isinstance(session.context, dict):
        session.context["followup_reminder_queued"] = True
    return interview_tool_response(
        ok=True,
        status="ok",
        response_directive=tell_user_directive(
            f"Got it — we'll send a follow-up to {email} if needed."
        ),
    )


async def reset_example_interview(
    visitor: Any = None,
    interview_action: Any = None,
    session: Any = None,
    config: Any = None,
    **kwargs,
) -> Dict[str, Any]:
    """Clear interview progress and re-start from the first question.

    Pattern from onboarding_interview ``reset_onboarding``: clear session,
    close task, re-init interview. Wired via ``interview.reset.function`` —
    the model still calls ``interview__reset_interview()``.
    """
    conversation = await _get_conversation(visitor)
    if conversation is None:
        return {
            "status": "error",
            "response_directive": "No conversation available to reset the interview.",
        }

    ctx = getattr(conversation, "context", None)
    if isinstance(ctx, dict):
        ctx.pop("interview", None)

    try:
        await conversation.save()
    except Exception as exc:
        logger.debug("reset_example_interview: save conversation failed: %s", exc)

    if interview_action is not None and visitor is not None:
        try:
            await interview_action._close_task(visitor, status="cancelled")
        except Exception as exc:
            logger.debug(
                "reset_example_interview: close interview task failed: %s", exc
            )

    if interview_action is not None and visitor is not None:
        try:
            await interview_action._handle_start(
                _SKILL_NAME,
                visitor,
                user_message="",
            )
            next_obs = await interview_action._handle_next_question(visitor)
            first_question = "What is your name?"
            try:
                parsed = json.loads(next_obs)
                next_qs = parsed.get("next_questions") or []
                if next_qs and next_qs[0].get("question"):
                    first_question = str(next_qs[0]["question"])
            except (json.JSONDecodeError, TypeError, IndexError, KeyError):
                pass
            return {
                "status": "restarted",
                "response_directive": tell_user_with_followup_directive(
                    "No problem — let's start over.",
                    first_question,
                ),
            }
        except Exception as exc:
            logger.error("reset_example_interview: re-init failed: %s", exc)

    return {
        "status": "cleared",
        "response_directive": tell_user_directive(
            "Your progress was cleared. Say when you'd like to start again."
        ),
    }


# ─── Review handler ──────────────────────────────────────────────────


async def example_review(
    session: Any = None,
    visitor: Any = None,
    interview_action: Any = None,
    config: Any = None,
    extracted_values: Optional[Dict[str, str]] = None,
    review_data: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Escalation terminate path or standard confirmation summary.

    Pattern from pre_alert_interview ``pre_alert_review``: when escalate is
    set in session.context, terminate without calling complete.
    """
    result: Dict[str, Any] = {
        "modified_values": {},
        "additional_data": {},
        "custom_message": "",
    }

    if not session:
        return result

    escalate = (
        session.context.get("escalate")
        if isinstance(getattr(session, "context", None), dict)
        else None
    )

    if escalate:
        result["directive"] = _ESCALATION_MESSAGE
        result["modified_values"]["__terminate__"] = "true"
        result["terminate"] = True
        return result

    collected = extracted_values or session.get_collected_summary()
    if not (collected or {}).get("feedback_comments"):
        result["modified_values"]["feedback_comments"] = "__omit__"

    return result


# ─── Completion handler ────────────────────────────────────────────────


async def example_complete(
    session: Any = None,
    visitor: Any = None,
    interview_action: Any = None,
    config: Any = None,
    extracted_values: Optional[Dict[str, str]] = None,
    review_data: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Mock completion — stores feedback in conversation context.

    Live skills call ZoonAPIAction here (see onboarding_interview
    ``complete_onboarding`` and pre_alert_interview ``pre_alert_complete``).
    This example avoids external API dependencies.
    """
    if not extracted_values:
        return {"directive": "No feedback data to save."}

    customer_name = (extracted_values.get("customer_name") or "").strip()
    product_rating = (extracted_values.get("product_rating") or "").strip()
    feedback_comments = (extracted_values.get("feedback_comments") or "").strip()
    follow_up_email = (extracted_values.get("follow_up_email") or "").strip()

    if not customer_name or not product_rating or not follow_up_email:
        return {
            "directive": (
                "Some required feedback fields are missing. "
                "Please go back and collect all required information."
            )
        }

    conversation = await _get_conversation(visitor)
    if conversation is not None:
        ctx = getattr(conversation, "context", None)
        if isinstance(ctx, dict):
            feedback_records = ctx.get("feedback_records", {})
            if not isinstance(feedback_records, dict):
                feedback_records = {}
            record_id = f"{follow_up_email}_{product_rating}"
            feedback_records[record_id] = {
                "customer_name": customer_name,
                "product_rating": product_rating,
                "feedback_comments": feedback_comments,
                "follow_up_email": follow_up_email,
            }
            ctx["feedback_records"] = feedback_records
            ctx["last_known_email"] = follow_up_email
            try:
                await conversation.save()
            except Exception as exc:
                logger.debug("example_complete: save conversation failed: %s", exc)

    if interview_action and visitor:
        try:
            await interview_action._close_task(visitor, status="completed")
        except Exception as exc:
            logger.debug("example_complete: close interview task failed: %s", exc)

    return {
        "directive": (
            f"Thank you, {customer_name}! Your rating of {product_rating}/5 "
            "has been recorded. We appreciate your feedback."
        )
    }
