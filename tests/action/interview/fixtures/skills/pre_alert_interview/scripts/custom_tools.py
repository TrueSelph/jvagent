"""Custom tools for the pre_alert_interview interview.

Every hook takes the single ``ctx`` (HookExecutionContext): read inputs as
attributes (``ctx.value``, ``ctx.session``, ``ctx.visitor``, ``ctx.interview``,
``ctx.extracted_values``), furnish user-facing text via ``ctx.say`` and control/
return data via ``ctx.tool_response`` (or ``ctx.valid`` / ``ctx.invalid`` for
validators).

Functions are loaded by ``function:`` name in SKILL.md frontmatter ``interview:``. Sections:

1. Constants
2. Shared helpers — _get_conversation, _get_user_pre_alerts
3. Validators — validate_tracking_number, validate_invoice_value,
   validate_alternative_tracking_number
4. Custom tools — check_tracking_status
5. Review handler — pre_alert_review
6. Completion handler — pre_alert_complete
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict

logger = logging.getLogger(__name__)

_PRE_ALERT_INTERVIEW_TYPE = "pre_alert_interview"
_KNOWN_STATUS_TRACKING = "291421515335"
_DESCRIPTION_QUESTION = "What is the description of the item(s) you're shipping?"
_PRE_ALERT_ASK_NOTE = "Do not ask for weight, dimensions, origin, or destination."


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


async def _get_user_pre_alerts(visitor: Any) -> Dict[str, Any]:
    conversation = await _get_conversation(visitor)
    if (
        conversation
        and hasattr(conversation, "context")
        and isinstance(conversation.context, dict)
    ):
        data = conversation.context.get("user_pre_alerts", {})
        return data if isinstance(data, dict) else {}
    return {}


# ─── Validators ──────────────────────────────────────────────────────


async def validate_tracking_number(ctx) -> Dict[str, Any]:
    """Validate tracking number (at least 10 digits)."""
    value = ctx.value
    digits = "".join(c for c in (value or "") if c.isdigit())
    if len(digits) < 10:
        return ctx.invalid(
            "Please provide a valid tracking number (at least 10 digits).",
            value=value,
        )
    return ctx.valid(value=digits)


async def validate_invoice_value(ctx) -> Dict[str, Any]:
    """Validate invoice value (optional — empty is valid)."""
    value = ctx.value
    if not value or not str(value).strip():
        return ctx.valid(value="")
    cleaned = re.sub(r"[$,\s]", "", str(value).strip())
    try:
        float(cleaned)
        return ctx.valid(value=str(cleaned))
    except ValueError:
        return ctx.invalid(
            "Please provide a valid numeric value (e.g. '540000' or '1299.99'), or say 'skip'.",
            value=value,
        )


async def validate_alternative_tracking_number(ctx) -> Dict[str, Any]:
    """Validate alternative tracking (optional — empty is valid)."""
    value = ctx.value
    if not value or not str(value).strip():
        return ctx.valid(value="")
    digits = "".join(c for c in str(value) if c.isdigit())
    if len(digits) < 10:
        return ctx.invalid(
            "Please provide a valid tracking number (at least 10 digits) or say 'skip'.",
            value=value,
        )
    return ctx.valid(value=digits)


# ─── Custom tools ──────────────────────────────────────────────────────


async def check_tracking_status(ctx) -> str:
    """Check known status tracking or existing user_pre_alerts after tracking_number is stored."""
    session = ctx.session
    if session is None:
        return ctx.no_session()

    tracking_number = (session.get_value("tracking_number") or "").strip()
    if not tracking_number:
        return ctx.tool_response(
            ok=False,
            status="error",
            error_code="MISSING_FIELD",
            system_message="Tracking number not yet stored in session.",
            skip_to_review=False,
            response_directive=ctx.call_tool("interview__set_fields"),
        )

    visitor = ctx.visitor
    user_pre_alerts = await _get_user_pre_alerts(visitor)
    existing = user_pre_alerts.get(tracking_number)
    is_known = tracking_number == _KNOWN_STATUS_TRACKING

    if is_known or existing:
        status_payload = {
            "tracking_number": tracking_number,
            "description": (existing or {}).get("description", ""),
            "status": (existing or {}).get("status", "pending"),
        }
        if not isinstance(session.context, dict):
            session.context = {}
        session.context["tracking_status"] = status_payload

        interview_action = ctx.interview
        if interview_action:
            await interview_action._save_session(session, visitor)

        return ctx.tool_response(
            ok=True,
            status="tracking_status",
            skip_to_review=True,
            system_message="Pre-alert or known status found for this tracking number.",
            next_tool="interview__review",
            response_directive=ctx.call_tool("interview__review"),
        )

    return ctx.tool_response(
        ok=True,
        status="ok",
        skip_to_review=False,
        system_message="New tracking number — continue collecting pre-alert details.",
    )


# ─── Review handler ────────────────────────────────────────────────────


async def pre_alert_review(ctx) -> Dict[str, Any]:
    """Status-only path when tracking_status is set, else confirmation summary."""
    result: Dict[str, Any] = {
        "modified_values": {},
        "additional_data": {},
        "custom_message": "",
    }

    session = ctx.session
    if not session:
        return result

    tracking_status = (
        session.context.get("tracking_status")
        if isinstance(getattr(session, "context", None), dict)
        else None
    )

    interview_action = ctx.interview
    if tracking_status and isinstance(tracking_status, dict) and interview_action:
        tracking_number = tracking_status.get("tracking_number", "")
        status_msg = f"Your package with tracking number **{tracking_number}** is being processed."
        try:
            model_action = await interview_action.get_model_action()
            if model_action and hasattr(model_action, "generate"):
                status_prompt = (
                    "You are an order status assistant for Zoon.\n\n"
                    "Determine pickup status from tracking details and respond in one "
                    "short friendly message.\n\n"
                    f"Tracking Details: {tracking_status.get('status', 'pending')}"
                )
                llm_result = await model_action.generate(
                    prompt="What is the status of my order?",
                    stream=False,
                    system=status_prompt,
                    temperature=0.1,
                    max_tokens=256,
                )
                if llm_result:
                    status_msg = (
                        llm_result if isinstance(llm_result, str) else str(llm_result)
                    )
        except Exception as e:
            logger.error("pre_alert_review: LLM status failed: %s", e)

        result["response_directive"] = status_msg
        result["modified_values"]["__terminate__"] = "true"
        result["terminate"] = True
        return result

    collected = ctx.extracted_values or session.get_collected_summary()
    if not (collected or {}).get("alternative_tracking_number"):
        result["modified_values"]["alternative_tracking_number"] = "__omit__"

    return result


# ─── Completion handler ────────────────────────────────────────────────


async def pre_alert_complete(ctx) -> Dict[str, Any]:
    """Create pre-alert via Zoon API and update user_pre_alerts in context."""
    extracted_values = ctx.extracted_values
    interview_action = ctx.interview
    if not extracted_values or not interview_action:
        return {"response_directive": "No extracted values to process."}

    tracking_number = (extracted_values.get("tracking_number") or "").strip()
    description = (extracted_values.get("description") or "").strip()
    invoice_value_str = (extracted_values.get("invoice_value") or "").strip()
    alternative_tracking_number = (
        extracted_values.get("alternative_tracking_number") or ""
    ).strip()

    if not tracking_number:
        return {
            "response_directive": "No tracking number provided. Cannot create pre-alert."
        }

    invoice_value = None
    if invoice_value_str:
        try:
            invoice_value = float(invoice_value_str)
        except ValueError:
            pass

    try:
        api = await interview_action.get_action("ZoonAPIAction")
    except Exception as e:
        logger.error("pre_alert_complete: failed to get ZoonAPIAction: %s", e)
        api = None

    if not api:
        return {
            "response_directive": (
                "Sorry, I couldn't create your pre-alert at the moment. "
                "Please try again in a few minutes."
            )
        }

    visitor = ctx.visitor
    customer_id = None
    user_id = str(getattr(visitor, "user_id", "") or "") if visitor else ""
    if user_id:
        try:
            customer_result = await api.find_customer_by_phone(user_id)
            if isinstance(customer_result, dict):
                customer_data = customer_result.get("customer")
                if isinstance(customer_data, dict):
                    customer_id = customer_data.get("id")
        except Exception as e:
            logger.error("pre_alert_complete: find_customer_by_phone failed: %s", e)

    if not customer_id:
        return {
            "response_directive": (
                "I couldn't find your account. Please ensure your WhatsApp number "
                "is registered with Zoon, then try again."
            )
        }

    try:
        result = await api.create_pre_alert(
            customer_id=customer_id,
            tracking_number=tracking_number,
            description=description,
            invoice_value=invoice_value,
            alternative_tracking_number=alternative_tracking_number or None,
        )
    except Exception as e:
        logger.error("pre_alert_complete: create_pre_alert failed: %s", e)
        return {
            "response_directive": (
                "Sorry, I couldn't create your pre-alert at the moment. "
                "Please try again in a few minutes."
            )
        }

    if not result or not isinstance(result, dict) or not result.get("id"):
        if isinstance(result, dict) and result.get("status") == 400:
            message = result.get("message", {})
            if isinstance(message, dict):
                error_message = message.get("message", str(message))
            elif isinstance(message, str):
                error_message = message
            else:
                error_message = "Validation error"
            return {
                "response_directive": (
                    f"There was an issue creating your pre-alert: {error_message}. "
                    "Please try again."
                )
            }
        return {
            "response_directive": (
                "Sorry, I couldn't create your pre-alert at the moment. "
                "Please try again in a few minutes."
            )
        }

    if visitor is None:
        try:
            from jvagent.tooling.tool_executor import get_dispatch_visitor

            visitor = get_dispatch_visitor()
        except Exception:
            visitor = None

    conversation = await _get_conversation(visitor)
    if conversation:
        conv_ctx = getattr(conversation, "context", None)
        if not isinstance(conv_ctx, dict):
            conv_ctx = {}
            conversation.context = conv_ctx
        user_pre_alerts = conv_ctx.get("user_pre_alerts", {})
        if not isinstance(user_pre_alerts, dict):
            user_pre_alerts = {}
        user_pre_alerts[tracking_number] = {
            "tracking_number": tracking_number,
            "description": description,
            "invoice_value": invoice_value,
            "customer_id": customer_id,
            "pre_alert_id": result.get("id"),
        }
        conv_ctx["user_pre_alerts"] = user_pre_alerts
        try:
            await conversation.save()
        except Exception as e:
            logger.error("pre_alert_complete: save context failed: %s", e)

    return {
        "response_directive": (
            f"Your pre-alert has been created successfully! Your tracking number is "
            f"**{tracking_number}**. You'll be notified when your package status changes."
        )
    }
