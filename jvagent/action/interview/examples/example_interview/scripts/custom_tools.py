"""Custom tools for the example_interview reference skill.

Functions are loaded by ``function:`` name in SKILL.md frontmatter ``interview:``. This file
demonstrates every hook type used by live interview skills. Sections:

1. Constants
2. Shared helpers — _get_conversation
3. Validators — validate_rating
4. Input context providers — suggest_email (pre_tool; pattern from onboarding)
5. Custom tools — check_low_rating (post_tool), reset_example_interview
6. Review handler — example_review (manual-confirm review pattern)
7. Completion handler — example_complete

Every hook takes the single ``ctx`` (HookExecutionContext): read inputs as
attributes (``ctx.value``, ``ctx.session``, ``ctx.extracted_values``), furnish
user-facing text via ``ctx.say`` and control/return data via ``ctx.tool_response``
(or ``ctx.valid`` / ``ctx.invalid`` for validators).

Copy this folder to skills/<your_skill_name>/ and adapt function names to
match your frontmatter ``interview:`` references.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict

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


async def validate_rating(ctx) -> Dict[str, Any]:
    """Validate product rating (integer 1–5)."""
    value = ctx.value
    cleaned = re.sub(r"\D", "", str(value or "").strip())
    if not cleaned:
        return ctx.invalid("Please provide a rating from 1 to 5.", value=value)
    try:
        rating = int(cleaned)
    except ValueError:
        return ctx.invalid("Please provide a whole number from 1 to 5.", value=value)
    if rating < 1 or rating > 5:
        return ctx.invalid("Rating must be between 1 and 5.", value=value)
    return ctx.valid(value=str(rating))


# ─── Input context providers (pre_tools) ─────────────────────────────


async def suggest_email(ctx) -> Dict[str, Any]:
    """Pre-tool for follow_up_email — suggests email from conversation context.

    Input-context provider pattern: return a
    suggested_value and say the suggestion; the LLM must confirm before set_field.
    """
    conversation = await _get_conversation(ctx.visitor)
    if conversation is None:
        return ctx.tool_response(ok=True, status="ok")

    cctx = getattr(conversation, "context", None)
    if not isinstance(cctx, dict):
        return ctx.tool_response(ok=True, status="ok")

    # Example: read a previously stored email from another flow.
    suggested = (cctx.get("last_known_email") or "").strip()
    if not suggested or "@" not in suggested:
        return ctx.tool_response(ok=True, status="ok")

    ctx.say(
        f"We have {suggested} on file. Would you like to use this for follow-up?",
        hint=(
            "On the user's NEXT message: if they confirm (yes/ok/sure), call "
            f'interview__set_fields with {{"fields": {{"follow_up_email": "{suggested}"}}}}). '
            "If they provide a different email, save that instead."
        ),
    )
    return ctx.tool_response(ok=True, status="ok", suggested_value=suggested)


# ─── Custom tools (post_tools + LLM-callable) ────────────────────────


async def check_low_rating(ctx) -> str:
    """Post-tool after product_rating — escalate low ratings to review.

    Set session context and return ``next_tool: interview__review`` so the
    response queues a jump to review.
    """
    session = ctx.session
    if session is None:
        return ctx.no_session()

    rating_str = (session.get_value("product_rating") or "").strip()
    try:
        rating = int(rating_str)
    except ValueError:
        return ctx.tool_response(
            ok=False,
            status="error",
            error_code="INVALID_RATING",
            system_message="Product rating not yet stored in session.",
            response_directive=ctx.call_tool("interview__set_fields"),
        )

    if rating <= _LOW_RATING_THRESHOLD:
        if not isinstance(session.context, dict):
            session.context = {}
        session.context["escalate"] = True
        session.context["escalation_reason"] = f"low_rating_{rating}"

        if ctx.interview:
            await ctx.interview._save_session(session, ctx.visitor)

        return ctx.tool_response(
            ok=True,
            status="escalation",
            system_message=f"Rating {rating} triggers escalation — skip remaining questions.",
            next_tool="interview__review",
            response_directive=ctx.call_tool("interview__review"),
        )

    return ctx.tool_response(
        ok=True,
        status="ok",
        system_message="Rating accepted — continue collecting feedback.",
    )


async def send_followup_reminder(ctx) -> str:
    """LLM-callable demo tool — records that a follow-up reminder was queued."""
    session = ctx.session
    email = (session.get_value("follow_up_email") if session else "") or ""
    if not email:
        ctx.say("I need an email on file before queuing a follow-up reminder.")
        return ctx.tool_response(ok=False, status="error")
    if session is not None and isinstance(session.context, dict):
        session.context["followup_reminder_queued"] = True
    ctx.say(f"Got it — we'll send a follow-up to {email} if needed.")
    return ctx.tool_response(ok=True, status="ok")


async def reset_example_interview(ctx) -> Dict[str, Any]:
    """Clear interview progress and re-start from the first question.

    Reset-tool pattern: clear session,
    close task, re-init interview. Wired via ``interview.reset.function`` —
    the model still calls ``interview__reset()``.
    """
    visitor = ctx.visitor
    interview = ctx.interview
    conversation = await _get_conversation(visitor)
    if conversation is None:
        ctx.say("No conversation available to reset the interview.")
        return ctx.tool_response(ok=False, status="error")

    cctx = getattr(conversation, "context", None)
    if isinstance(cctx, dict):
        cctx.pop("interview", None)

    try:
        await conversation.save()
    except Exception as exc:
        logger.debug("reset_example_interview: save conversation failed: %s", exc)

    if interview is not None and visitor is not None:
        try:
            await interview._close_task(
                visitor,
                status="cancelled",
                spec_name=_SKILL_NAME,
            )
        except Exception as exc:
            logger.debug(
                "reset_example_interview: close interview task failed: %s", exc
            )

    if interview is not None and visitor is not None:
        try:
            await interview._handle_start(
                _SKILL_NAME,
                visitor,
                user_message="",
            )
            next_obs = await interview._handle_next_field(visitor)
            first_question = "What is your name?"
            try:
                parsed = json.loads(next_obs)
                next_qs = parsed.get("next_field") or []
                if next_qs and next_qs[0].get("question"):
                    first_question = str(next_qs[0]["question"])
            except (json.JSONDecodeError, TypeError, IndexError, KeyError):
                pass
            ctx.say(["No problem — let's start over.", first_question])
            return ctx.tool_response(ok=True, status="restarted")
        except Exception as exc:
            logger.error("reset_example_interview: re-init failed: %s", exc)

    ctx.say("Your progress was cleared. Say when you'd like to start again.")
    return ctx.tool_response(ok=True, status="cleared")


# ─── Review handler ──────────────────────────────────────────────────


async def example_review(ctx) -> Dict[str, Any]:
    """Escalation terminate path or standard confirmation summary.

    Review-handler pattern: when escalate is
    set in session.context, terminate without calling complete.
    """
    result: Dict[str, Any] = {
        "modified_values": {},
        "additional_data": {},
        "custom_message": "",
    }

    session = ctx.session
    if not session:
        return result

    escalate = (
        session.context.get("escalate")
        if isinstance(getattr(session, "context", None), dict)
        else None
    )

    if escalate:
        ctx.say(_ESCALATION_MESSAGE)
        result["modified_values"]["__terminate__"] = "true"
        result["terminate"] = True
        return result

    collected = ctx.extracted_values or session.get_collected_summary()
    if not (collected or {}).get("feedback_comments"):
        result["modified_values"]["feedback_comments"] = "__omit__"

    return result


# ─── Completion handler ────────────────────────────────────────────────


async def example_complete(ctx) -> str:
    """Mock completion — stores feedback in conversation context.

    Live skills call a consumer API action here (e.g. a customer-
    record lookup or a record-create call in the completion handler).
    This example avoids external API dependencies.
    """
    extracted_values = ctx.extracted_values
    if not extracted_values:
        ctx.say("No feedback data to save.")
        return ctx.tool_response(ok=True, status="ok")

    customer_name = (extracted_values.get("customer_name") or "").strip()
    product_rating = (extracted_values.get("product_rating") or "").strip()
    feedback_comments = (extracted_values.get("feedback_comments") or "").strip()
    follow_up_email = (extracted_values.get("follow_up_email") or "").strip()

    if not customer_name or not product_rating or not follow_up_email:
        ctx.say(
            "Some required feedback fields are missing. "
            "Please go back and collect all required information."
        )
        return ctx.tool_response(ok=True, status="ok")

    visitor = ctx.visitor
    conversation = await _get_conversation(visitor)
    if conversation is not None:
        cctx = getattr(conversation, "context", None)
        if isinstance(cctx, dict):
            feedback_records = cctx.get("feedback_records", {})
            if not isinstance(feedback_records, dict):
                feedback_records = {}
            record_id = f"{follow_up_email}_{product_rating}"
            feedback_records[record_id] = {
                "customer_name": customer_name,
                "product_rating": product_rating,
                "feedback_comments": feedback_comments,
                "follow_up_email": follow_up_email,
            }
            cctx["feedback_records"] = feedback_records
            cctx["last_known_email"] = follow_up_email
            try:
                await conversation.save()
            except Exception as exc:
                logger.debug("example_complete: save conversation failed: %s", exc)

    if ctx.interview and visitor:
        try:
            await ctx.interview._close_task(
                visitor,
                status="completed",
                spec_name=_SKILL_NAME,
            )
        except Exception as exc:
            logger.debug("example_complete: close interview task failed: %s", exc)

    ctx.say(
        f"Thank you, {customer_name}! Your rating of {product_rating}/5 "
        "has been recorded. We appreciate your feedback."
    )
    return ctx.tool_response(ok=True, status="ok")
