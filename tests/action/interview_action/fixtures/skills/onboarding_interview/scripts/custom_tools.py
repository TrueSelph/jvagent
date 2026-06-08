"""Custom tools for the onboarding_interview interview.

Functions are loaded by ``function:`` name in SKILL.md frontmatter ``interview:``. Sections:

1. Constants
2. Shared helpers — _get_conversation
3. Validators — validate_id_number, validate_otp_code
4. Input context providers — get_phone_number, suggest_email_from_task
5. Custom tools — verify_phone_number, verify_email (post_tools), send_otp,
   process_id_card, reset_onboarding
6. Completion handler — complete_onboarding
7. ID card helpers — _get_image_urls, _send_whatsapp_otp, task data helpers
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from jvagent.action.interview_action.core.responses import (
    call_tool_directive,
    interview_tool_response,
    no_session_directive,
    tell_user_directive,
)

logger = logging.getLogger(__name__)

_ONBOARDING_SKILL_NAME = "onboarding_interview"
_ID_FIELDS = ("id_number", "full_name", "date_of_birth")

_CUSTOMER_EXISTS_DIRECTIVE = tell_user_directive(
    "They already have a Zoon account linked to this number — onboarding is not needed. "
    "Let them know they can request product quotations, track shipments, arrange deliveries, or ask any questions about our services. "
    "Then invite them to say what they need help with.",
    note=(
        "Do not ask for email, ID, or other onboarding fields. "
        "Do not call interview__complete or any other interview tools."
    ),
)

_RESET_CANCELLED_DIRECTIVE = tell_user_directive(
    "Your onboarding has been cancelled. To chat with me and use Zoon services, "
    "you'll need to complete the onboarding process first.",
    note=(
        "Deliver this message only. Do not ask for phone, email, or other onboarding fields. "
        "Do not call interview__next_question, interview__set_field, or any other interview tools."
    ),
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


def _normalize_phone_digits(value: str) -> str:
    return "".join(c for c in str(value or "") if c.isdigit())


def _get_customer_phone_digits(customer: Dict[str, Any]) -> str:
    phone = customer.get("phone")
    if isinstance(phone, list) and phone:
        return _normalize_phone_digits(str(phone[0]))
    whatsapp = customer.get("whatsapp")
    if whatsapp:
        return _normalize_phone_digits(str(whatsapp))
    return ""


_TASK_FIELD_KEYS = (
    "phone_number",
    "email",
    "full_name",
    "id_number",
    "date_of_birth",
)


def _customer_email(customer: Dict[str, Any]) -> str:
    for key in ("primary_mail", "mail"):
        val = (customer.get(key) or "").strip()
        if val:
            return val
    emails = customer.get("email")
    if isinstance(emails, list) and emails:
        return str(emails[0]).strip()
    return ""


def _format_customer_dob(dob: Any) -> str:
    """Convert API dob (epoch int per ZoonAPIAction docs) to DD-MM-YYYY."""
    if dob is None or dob == "":
        return ""
    if isinstance(dob, str):
        return dob.strip()
    if isinstance(dob, (int, float)):
        ts = int(dob)
        if ts >= 31_536_000:
            from datetime import datetime, timezone

            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            if 1920 <= dt.year <= datetime.now(timezone.utc).year:
                return dt.strftime("%d-%m-%Y")
    return ""


def _fields_from_customer(customer: Dict[str, Any], phone: str) -> Dict[str, str]:
    """Map Zoon API customer object to interview field names."""
    fields: Dict[str, str] = {}
    phone_digits = _normalize_phone_digits(phone) or _get_customer_phone_digits(
        customer
    )
    if phone_digits:
        fields["phone_number"] = phone_digits
    email = _customer_email(customer)
    if email:
        fields["email"] = email
    name = (customer.get("name") or customer.get("username") or "").strip()
    if name:
        fields["full_name"] = name
    id_number = (customer.get("id_number") or "").strip()
    if id_number:
        fields["id_number"] = id_number
    dob = _format_customer_dob(customer.get("dob"))
    if dob:
        fields["date_of_birth"] = dob
    return fields


def _normalize_task_fields(raw: Dict[str, Any]) -> Dict[str, str]:
    """Keep only known profile keys; strip otp_code, id_card, and empty values."""
    out: Dict[str, str] = {}
    for key in _TASK_FIELD_KEYS:
        val = raw.get(key)
        if isinstance(val, str):
            val = val.strip()
        if not val:
            continue
        if key == "phone_number":
            val = _normalize_phone_digits(str(val))
        if val:
            out[key] = str(val)
    return out


def _build_completion_task_fields(
    session: Any,
    customer: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Merge API customer profile with session-collected values for task persistence."""
    session_fields = session.get_collected_summary() if session else {}
    phone = session_fields.get("phone_number") or ""
    customer_fields = _fields_from_customer(customer, phone) if customer else {}
    merged: Dict[str, Any] = {**customer_fields, **session_fields}
    if phone:
        merged["phone_number"] = _normalize_phone_digits(phone)
    return _normalize_task_fields(merged)


def _fields_from_extracted_values(
    extracted_values: Dict[str, Any],
    account_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """New account creation: collected values enriched by API response when present."""
    fields: Dict[str, Any] = dict(extracted_values or {})
    if account_result and isinstance(account_result, dict):
        api_fields = _fields_from_customer(
            account_result,
            str(fields.get("phone_number") or ""),
        )
        fields = {**api_fields, **fields}
    return _normalize_task_fields(fields)


def _otp_phone_hint(session: Any) -> str:
    ctx = getattr(session, "context", None) if session else None
    phone = ""
    if isinstance(ctx, dict):
        phone = ctx.get("otp_target_phone") or ""
    if not phone and session:
        phone = session.get_value("phone_number") or ""
    digits = _normalize_phone_digits(phone)
    if len(digits) >= 4:
        return f"ending in {digits[-4:]}"
    return "on file"


def _build_otp_welcome_message(
    account_number: str, *, phone_updated: bool = False
) -> str:
    if phone_updated:
        base = (
            f"Your WhatsApp number has been updated and linked to your Zoon account "
            f"**{account_number}**."
            if account_number
            else "Your WhatsApp number has been updated and linked to your Zoon account."
        )
    else:
        base = (
            f"Welcome back! Your WhatsApp number has been linked to your Zoon account "
            f"**{account_number}**."
            if account_number
            else "Welcome back! Your WhatsApp number has been linked to your Zoon account."
        )
    return (
        f"{base} You can use this account number as the shipping address for your "
        f"packages. Link: http://zoonshop.com/cargo-shipping"
        if account_number
        else (
            f"{base} You can now request quotations, track shipments, arrange deliveries, "
            f"or ask any questions about our services."
        )
    )


async def _persist_skill_task_data(
    visitor: Any,
    skill_name: str,
    fields: Dict[str, Any],
    *,
    account_number: Optional[str] = None,
    flow_mode: Optional[str] = None,
) -> None:
    """Merge extracted fields into active SKILL task data before complete."""
    if visitor is None:
        return
    try:
        store = visitor.tasks
        for handle in store.list(status="active", owner_action=skill_name):
            payload: Dict[str, Any] = {"fields": fields}
            if account_number:
                payload["account_number"] = account_number
            if flow_mode:
                payload["flow_mode"] = flow_mode
            await handle.update(**payload)
    except Exception as exc:
        logger.debug("_persist_skill_task_data failed: %s", exc)


def _get_completed_skill_fields(visitor: Any, skill_name: str) -> Dict[str, Any]:
    """Return fields from most recent completed SKILL task, or {}."""
    if visitor is None:
        return {}
    try:
        store = visitor.tasks
        handles = store.list(status="completed", owner_action=skill_name)
        if not handles:
            return {}
        latest = max(handles, key=lambda h: h.updated_at or "")
        fields = latest.data.get("fields")
        return fields if isinstance(fields, dict) else {}
    except Exception as exc:
        logger.debug("_get_completed_skill_fields failed: %s", exc)
        return {}


async def _complete_otp_success(
    session: Any,
    visitor: Any,
    interview_action: Any,
    *,
    account_number: str = "",
    flow_mode: str = "onboard",
    phone_updated: bool = False,
) -> str:
    """Mark onboarding complete after OTP verification; return welcome directive."""
    from jvagent.action.interview_action.core.session import (
        InterviewStatus,
    )

    ctx = getattr(session, "context", None) if session else None
    customer = (
        (ctx or {}).get("email_lookup_customer") if isinstance(ctx, dict) else None
    )
    fields = _build_completion_task_fields(session, customer)
    await _persist_skill_task_data(
        visitor,
        _ONBOARDING_SKILL_NAME,
        fields,
        account_number=account_number or None,
        flow_mode=flow_mode,
    )

    if session is not None:
        session.status = InterviewStatus.COMPLETED
        if isinstance(session.context, dict):
            session.context.pop("otp_pending", None)
            session.context.pop("otp_sent", None)
        await interview_action._save_session(session, visitor)

    await interview_action._close_task(
        visitor,
        status="completed",
        spec_name=_ONBOARDING_SKILL_NAME,
    )

    conversation = await _get_conversation(visitor)
    if conversation is not None:
        ctx = getattr(conversation, "context", None)
        if isinstance(ctx, dict):
            ctx["user_is_onboarded"] = "completed"
            if account_number:
                ctx["customer_id"] = str(account_number)
            try:
                await conversation.save()
            except Exception as e:
                logger.error("_complete_otp_success: failed to save context: %s", e)

    welcome = _build_otp_welcome_message(account_number, phone_updated=phone_updated)
    return tell_user_directive(
        welcome,
        note="Do not call interview__complete or any other interview tools.",
    )


# ─── Validators ──────────────────────────────────────────────────────


async def validate_id_number(value: str, **kwargs) -> str:
    """Validate national ID (8-9 digits) or passport number."""

    cleaned = value.strip().upper()
    cleaned_len = len(cleaned)
    passport_error = None
    passport_valid = False
    passport_value = value

    if not cleaned:
        passport_error = "Please provide a passport number."
    elif cleaned_len < 8 or cleaned_len > 9:
        passport_error = f"Passport number must be 8-9 characters long, got {cleaned_len} characters."
    else:
        passport_valid = True
        passport_value = cleaned

    if passport_valid:
        return json.dumps(
            {
                "valid": True,
                "value": passport_value,
                "validator": "validate_id_number",
            }
        )

    return json.dumps(
        {
            "valid": False,
            "error": (
                passport_error
                or f"ID number must be 8 to 9 digits, got {cleaned_len} digits."
            ),
            "value": value,
            "validator": "validate_id_number",
        }
    )


async def validate_otp_code(
    value: str,
    session: Any = None,
    visitor: Any = None,
    interview_action: Any = None,
    **kwargs,
) -> Dict[str, Any]:
    """Validate OTP format and confirm via Zoon API."""
    ctx = getattr(session, "context", None) if session else None
    otp_sent = isinstance(ctx, dict) and ctx.get("otp_sent")

    if not otp_sent:
        return {
            "valid": False,
            "error": "OTP was not sent for this session.",
            "validator": "validate_otp_code",
            "response_directive": call_tool_directive(
                'interview__skip_field(field="otp_code")'
            ),
        }

    digits = "".join(c for c in str(value or "") if c.isdigit())
    if not digits:
        return {
            "valid": False,
            "error": "Please enter the verification code you received.",
            "value": value,
            "validator": "validate_otp_code",
        }
    if len(digits) < 4 or len(digits) > 8:
        return {
            "valid": False,
            "error": "Verification code must be 4 to 8 digits.",
            "value": value,
            "validator": "validate_otp_code",
        }

    email = (session.get_value("email") or "").strip() if session else ""
    phone = (session.get_value("phone_number") or "").strip() if session else ""

    if not email or not phone:
        return {
            "valid": False,
            "error": "Email and phone number are required before verifying OTP.",
            "validator": "validate_otp_code",
        }

    try:
        api = await interview_action.get_action("ZoonAPIAction")
        if not api:
            return {
                "valid": False,
                "error": "OTP confirmation is unavailable.",
                "validator": "validate_otp_code",
            }

        result = await api.confirm_whatsapp_otp(email, digits, phone)
        if isinstance(result, dict) and result.get("status") == 400:
            message = result.get("message", "Invalid or expired verification code.")
            if isinstance(message, dict):
                message = message.get(
                    "message", "Invalid or expired verification code."
                )
            hint = _otp_phone_hint(session)
            return {
                "valid": False,
                "error": str(message),
                "value": value,
                "validator": "validate_otp_code",
                "response_directive": tell_user_directive(
                    f"That verification code is incorrect. Would you like me to resend "
                    f"the code to the number {hint}?",
                    note=(
                        "If the user wants a resend, call onboarding_interview__send_otp. "
                        "Otherwise ask them to enter the code again."
                    ),
                ),
            }

        if not result or (
            isinstance(result, dict) and result.get("status") in (409, 400)
        ):
            return {
                "valid": False,
                "error": "OTP confirmation failed.",
                "validator": "validate_otp_code",
                "response_directive": tell_user_directive(
                    "We could not verify your code. Would you like me to resend it?"
                ),
            }

        customer = (ctx or {}).get("email_lookup_customer") or {}
        account_number = customer.get("account_number", "")
        flow_mode = (ctx or {}).get("flow_mode", "onboard")
        phone_updated = flow_mode == "update_phone"

        welcome = await _complete_otp_success(
            session,
            visitor,
            interview_action,
            account_number=str(account_number or ""),
            flow_mode=flow_mode,
            phone_updated=phone_updated,
        )
        return {
            "valid": True,
            "value": digits,
            "validator": "validate_otp_code",
            "interview_complete": True,
            "response_directive": welcome,
            "retain_context_keys": ["user_is_onboarded", "customer_id"],
        }
    except Exception as e:
        logger.error("validate_otp_code failed: %s", e)
        return {
            "valid": False,
            "error": f"OTP verification failed: {e}",
            "validator": "validate_otp_code",
        }


# ─── Input context providers ─────────────────────────────────────────


async def get_phone_number(
    session: Any = None,
    visitor: Any = None,
    **kwargs,
) -> Dict[str, Any]:
    """Input context provider for phone_number — suggests WhatsApp phone on file."""
    channel = getattr(visitor, "channel", None) if visitor else None
    if channel != "whatsapp":
        return {}

    user_id = getattr(visitor, "user_id", None) if visitor else None
    digits = "".join(c for c in str(user_id or "") if c.isdigit())
    if len(digits) != 10:
        return {}

    return {
        "ok": True,
        "value": digits,
        "directive": tell_user_directive(
            f"We have {digits} on file from WhatsApp. Would you like to use this "
            "as your contact number?",
            note=(
                "On the user's NEXT message: if they confirm (yes/ok/sure), call "
                "interview__set_field(field='phone_number', value=<digits>). "
                "Read post_tools_results; if exists:false, call interview__next_question."
            ),
        ),
    }


async def suggest_email_from_task(
    session: Any = None,
    visitor: Any = None,
    **kwargs,
) -> Dict[str, Any]:
    """Pre-tool for email — suggests address from completed onboarding task."""
    if session and session.get_value("email"):
        return {}

    prior = _get_completed_skill_fields(visitor, _ONBOARDING_SKILL_NAME)
    suggested = (prior.get("email") or "").strip()
    if not suggested:
        return {}

    return {
        "ok": True,
        "suggested_value": suggested,
        "directive": tell_user_directive(
            f"We have {suggested} on file from your previous onboarding. "
            "Would you like to use this email?",
            note=(
                "On the user's NEXT message: if they confirm (yes/ok/sure), call "
                f"interview__set_field(field='email', value='{suggested}'). "
                "If they provide a different email, save that instead."
            ),
        ),
    }


# ─── Custom tools ──────────────────────────────────────────────────────


async def verify_phone_number(
    phone: str = "",
    visitor: Any = None,
    interview_action: Any = None,
    session: Any = None,
    **kwargs,
) -> str:
    """Check if customer exists by phone (runs via post_tools after phone_number save)."""
    if visitor is None:
        from jvagent.tooling.tool_executor import get_dispatch_visitor

        visitor = get_dispatch_visitor()

    phone = (phone or "").strip()
    if not phone and session:
        phone = session.get_value("phone_number") or ""

    if not phone:
        return interview_tool_response(
            ok=False,
            status="error",
            exists=False,
            error="No phone number provided",
            system_message="No phone number provided for verification.",
        )

    try:
        api = await interview_action.get_action("ZoonAPIAction")
        if api:
            result = await api.find_customer_by_phone(phone)
            customer = result.get("customer") if isinstance(result, dict) else None
            if customer:
                account_number = customer.get("account_number") or ""
                fields = _normalize_task_fields(_fields_from_customer(customer, phone))
                await _persist_skill_task_data(
                    visitor,
                    _ONBOARDING_SKILL_NAME,
                    fields,
                    account_number=account_number or None,
                    flow_mode="onboard",
                )
                if session is not None:
                    from jvagent.action.interview_action.core.session import (
                        InterviewStatus,
                    )

                    session.status = InterviewStatus.COMPLETED
                    await interview_action._save_session(session, visitor)
                await interview_action._close_task(
                    visitor,
                    status="completed",
                    spec_name=_ONBOARDING_SKILL_NAME,
                )
                await interview_action._clear_interview_session(visitor)

                conversation = getattr(visitor, "conversation", None)
                if conversation is not None:
                    ctx = getattr(conversation, "context", None)
                    if isinstance(ctx, dict):
                        ctx["user_is_onboarded"] = "completed"
                        if account_number:
                            ctx["customer_id"] = str(account_number)
                        try:
                            await conversation.save()
                        except Exception as e:
                            logger.error(
                                "verify_phone_number: failed to save context: %s", e
                            )

                return interview_tool_response(
                    ok=True,
                    status="customer_exists",
                    exists=True,
                    interview_complete=True,
                    system_message="This phone number is already registered with Zoon.",
                    response_directive=_CUSTOMER_EXISTS_DIRECTIVE,
                )
    except Exception as e:
        logger.error(f"verify_phone_number check failed: {e}")
        return interview_tool_response(
            ok=False,
            status="error",
            exists=False,
            error=str(e),
            system_message="Customer lookup failed.",
        )

    return interview_tool_response(
        ok=True,
        status="not_registered",
        exists=False,
        system_message="No existing customer found with this phone number. Proceed with onboarding.",
    )


async def verify_email(
    visitor: Any = None,
    interview_action: Any = None,
    session: Any = None,
    **kwargs,
) -> str:
    """Check if customer exists by email and trigger OTP when phone differs."""
    if session is None:
        return interview_tool_response(
            ok=False,
            status="error",
            error_code="NO_SESSION",
            system_message="No active interview session for email verification.",
            response_directive=no_session_directive(),
        )

    email = (session.get_value("email") or "").strip()
    if not email:
        return interview_tool_response(
            ok=False,
            status="error",
            error_code="MISSING_FIELD",
            system_message="Email not yet stored in session.",
            response_directive=call_tool_directive("interview__set_field"),
        )

    session_phone = _normalize_phone_digits(session.get_value("phone_number") or "")

    try:
        api = await interview_action.get_action("ZoonAPIAction")
        if not api:
            return interview_tool_response(
                ok=False,
                status="error",
                system_message="Customer lookup unavailable.",
            )

        result = await api.find_customer_by_email(email)
        customer = result.get("customer") if isinstance(result, dict) else None

        if not customer:
            return interview_tool_response(
                ok=True,
                status="not_registered",
                system_message="No existing customer found with this email. Proceed with onboarding.",
            )

        account_phone = _get_customer_phone_digits(customer)
        if not account_phone or account_phone == session_phone:
            return interview_tool_response(
                ok=True,
                status="ok",
                system_message="Email matched an existing account with the same phone. Proceed with onboarding.",
            )

        if not isinstance(session.context, dict):
            session.context = {}
        session.context["email_lookup_customer"] = customer
        session.context["otp_pending"] = True
        session.context.setdefault("flow_mode", "onboard")
        await interview_action._save_session(session, visitor)

        return interview_tool_response(
            ok=True,
            status="otp_required",
            otp_pending=True,
            system_message=(
                "Existing account with different phone — call send_otp to verify."
            ),
            response_directive=tell_user_directive(
                "We found an existing account with this email, but it has a different "
                "phone number on file. I'll send a verification code to the email on your "
                "account.",
                note=(
                    "Call onboarding_interview__send_otp now. Do not call "
                    "interview__next_question until OTP is handled or skipped."
                ),
            ),
            next_tool="onboarding_interview__send_otp",
        )
    except Exception as e:
        logger.error("verify_email check failed: %s", e)
        return interview_tool_response(
            ok=False,
            status="error",
            system_message="Email verification failed.",
            error=str(e),
        )


async def send_otp(
    visitor: Any = None,
    interview_action: Any = None,
    session: Any = None,
    **kwargs,
) -> Dict[str, Any]:
    """Send WhatsApp verification OTP to the email on the account."""
    if session is None:
        return {
            "ok": False,
            "status": "error",
            "response_directive": no_session_directive(),
        }

    email = (session.get_value("email") or "").strip()
    if not email:
        prior = _get_completed_skill_fields(visitor, _ONBOARDING_SKILL_NAME)
        email = (prior.get("email") or "").strip()
        if email:
            await interview_action.persist_interview_fields(
                session, visitor, {"email": email}, validate=False
            )

    phone = (session.get_value("phone_number") or "").strip()

    if not email or not phone:
        return {
            "ok": False,
            "status": "error",
            "system_message": "Email and phone number are required before sending OTP.",
            "response_directive": tell_user_directive(
                "Please provide your email and phone number before we can send a "
                "verification code."
            ),
        }

    if not isinstance(session.context, dict):
        session.context = {}

    otp_sent = await _send_whatsapp_otp(interview_action, email)
    if not otp_sent:
        otp_sent = await _send_whatsapp_otp(interview_action, email)

    if otp_sent:
        session.context["otp_sent"] = True
        session.context["otp_target_phone"] = phone
        await interview_action._save_session(session, visitor)
        hint = _otp_phone_hint(session)
        return {
            "ok": True,
            "status": "otp_sent",
            "otp_sent": True,
            "system_message": f"Verification code sent to the phone number ending with {hint}.",
            "response_directive": tell_user_directive(
                f"We found a different phone number linked to your account. A verification code has been sent to the number ending with {hint}. Please enter the code to verify your identity.",
                note=(
                    "When the user provides the code, call "
                    "interview__set_field(field='otp_code', value=<code>)."
                ),
            ),
        }

    session.context["otp_sent"] = False
    await interview_action._save_session(session, visitor)
    return {
        "ok": False,
        "status": "error",
        "otp_sent": False,
        "system_message": "Could not send verification code.",
        "response_directive": call_tool_directive(
            'interview__skip_field(field="otp_code")'
        ),
    }


async def process_id_card(
    visitor: Any = None,
    interview_action: Any = None,
    session: Any = None,
    **kwargs,
) -> str:
    """Get ID image from visitor data, extract fields, and persist on success."""
    if visitor is None:
        from jvagent.tooling.tool_executor import get_dispatch_visitor

        visitor = get_dispatch_visitor()

    image_urls = _get_image_urls(visitor)
    if not image_urls:
        return interview_tool_response(
            ok=False,
            status="no_image",
            system_message="No ID card image found.",
            response_directive=tell_user_directive(
                "Please upload a clear photo of your ID card, or say no to enter your details manually.",
            ),
        )

    if interview_action is None:
        return interview_tool_response(
            ok=False,
            status="error",
            system_message="Interview action not available for ID extraction.",
        )

    try:
        model_action = await interview_action.get_model_action(required=True)
        if not model_action or not hasattr(model_action, "create_multimodal_content"):
            return interview_tool_response(
                ok=False,
                status="extract_failed",
                system_message="Image extraction is not available.",
                response_directive=tell_user_directive(
                    "We could not read your ID photo. Please enter your details manually."
                ),
            )

        prompt = (
            "Extract the following details from the ID card in the image and return them in a valid JSON object. "
            "Set any field you cannot read to N/A:\n"
            "- id_number (the national ID or passport number)\n"
            "- full_name (first name and last name as written on the card)\n"
            "- date_of_birth (in DD-MM-YYYY format)"
        )
        multimodal_prompt = model_action.create_multimodal_content(
            text=prompt, images=image_urls
        )
        result = await model_action.generate(
            prompt=multimodal_prompt,
            stream=False,
            system=(
                "You are a document extraction assistant. Extract the requested fields from the image. "
                "Return ONLY a valid JSON object with the field names as keys."
            ),
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=512,
            calling_action_name="InterviewAction",
        )

        if not result:
            return interview_tool_response(
                ok=False,
                status="extract_failed",
                system_message="Could not read the ID card image.",
                response_directive=tell_user_directive(
                    "We could not read that photo. Please upload a clearer image or enter your details manually."
                ),
            )

        json_match = re.search(r"\{.*\}", result, re.DOTALL)
        parsed = json.loads(json_match.group(0) if json_match else result)

        extracted = {}
        for field_name in _ID_FIELDS:
            val = parsed.get(field_name)
            if val and str(val).strip().upper() != "N/A":
                extracted[field_name] = str(val).strip()

        if not extracted:
            return interview_tool_response(
                ok=False,
                status="no_fields",
                system_message="Could not find the required information in the ID card image.",
                response_directive=tell_user_directive(
                    "We could not read the required details from that photo. "
                    "Please try another image or enter your details manually."
                ),
            )

        if session is None:
            return interview_tool_response(
                ok=False,
                status="error",
                system_message="No active interview session to store extracted fields.",
                response_directive=no_session_directive(),
            )

        persist_result = await interview_action.persist_interview_fields(
            session, visitor, extracted, validate=True
        )
        validation_errors = persist_result.get("validation_errors", {})

        contract = None
        if hasattr(interview_action, "_registry"):
            contract = interview_action._registry.get(session.interview_type)
        missing = (
            session.missing_required(contract.get_required_fields()) if contract else []
        )
        fields_summary = session.get_collected_summary()

        if validation_errors:
            failed = list(validation_errors.keys())
            return interview_tool_response(
                ok=False,
                status="validation_failed",
                system_message=f"Some ID details could not be saved: {failed}.",
                fields=fields_summary,
                missing_required=missing,
                response_directive=tell_user_directive(
                    "Some details from the ID photo could not be saved. Please provide the missing information."
                ),
            )

        stored = list(extracted.keys())
        return interview_tool_response(
            ok=True,
            status="extracted",
            system_message=f"Extracted and saved ID details: {', '.join(stored)}.",
            fields=fields_summary,
            missing_required=missing,
            response_directive=tell_user_directive(
                "ID details were saved from your photo."
            ),
        )

    except Exception as e:
        logger.error(f"process_id_card extraction failed: {e}")
        return interview_tool_response(
            ok=False,
            status="extract_failed",
            system_message="Could not extract details from the ID card image.",
            response_directive=tell_user_directive(
                "We could not read that photo. Please upload a clearer image or enter your details manually."
            ),
        )


async def reset_onboarding(
    visitor: Any = None,
    interview_action: Any = None,
    session: Any = None,
    config: Any = None,
    **kwargs,
) -> str:
    """Clear interview progress, cancel tasks, and inform user onboarding was cancelled."""
    if visitor is None:
        try:
            from jvagent.tooling.tool_executor import get_dispatch_visitor

            visitor = get_dispatch_visitor()
        except Exception:
            visitor = None

    conversation = await _get_conversation(visitor)
    if conversation is None:
        return interview_tool_response(
            ok=False,
            status="error",
            response_directive="No conversation available to reset onboarding.",
        )

    ctx = getattr(conversation, "context", None)
    if isinstance(ctx, dict):
        ctx.pop("interview", None)
        ctx["onboarding_required"] = True
        if ctx.get("user_is_onboarded") != "completed":
            ctx["user_is_onboarded"] = ""

    try:
        await conversation.save()
    except Exception as exc:
        logger.debug("reset_onboarding: save conversation failed: %s", exc)

    if interview_action is not None and visitor is not None:
        try:
            await interview_action._close_task(
                visitor,
                status="cancelled",
                spec_name=_ONBOARDING_SKILL_NAME,
            )
        except Exception as exc:
            logger.debug("reset_onboarding: close interview task failed: %s", exc)

    return interview_tool_response(
        ok=True,
        status="cancelled",
        system_message=(
            "Onboarding cancelled. User must complete onboarding to chat with the agent."
        ),
        response_directive=_RESET_CANCELLED_DIRECTIVE,
    )


# ─── Completion handler ────────────────────────────────────────────────


async def complete_onboarding(
    session: Any = None,
    visitor: Any = None,
    interview_action: Any = None,
    config: Any = None,
    extracted_values: Optional[Dict[str, str]] = None,
    review_data: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Create customer account via Zoon API after interview__complete."""
    result: Dict[str, Any] = {}

    if not extracted_values or not interview_action:
        result["directive"] = "No extracted values to process."
        return result

    account_result = None
    try:
        action = await interview_action.get_action("ZoonAPIAction")
        if action:
            params = {
                "primary_mail": extracted_values.get("email", ""),
                "phone": [extracted_values.get("phone_number", "")],
                "name": extracted_values.get("full_name", ""),
                "username": extracted_values.get("full_name", ""),
                "dob": extracted_values.get("date_of_birth", ""),
                "id_number": extracted_values.get("id_number", ""),
                "email": (
                    [extracted_values.get("email", "")]
                    if extracted_values.get("email")
                    else []
                ),
            }
            account_result = await action.create_customer(**params)
    except Exception as e:
        logger.error(f"complete_onboarding: create_customer failed: {e}")
        result["directive"] = f"Account creation failed: {e}. Please try again."
        return result

    account_created = account_result and account_result.get("status") == 200

    if account_created:
        account_number = account_result.get("account_number")
        result["directive"] = (
            f"Account created successfully! Account number: **{account_number}**. "
            f"Inform the user they can use this account number as the shipping address for their packages. "
            f"Link: http://zoonshop.com/cargo-shipping"
        )
    elif account_result and account_result.get("status") == 406:
        email = extracted_values.get("email", "")
        otp_sent = await _send_whatsapp_otp(interview_action, email)
        if not otp_sent:
            otp_sent = await _send_whatsapp_otp(interview_action, email)

        if otp_sent:
            result["directive"] = (
                "A different WhatsApp number is already connected to an account with this ID. "
                "A verification code was sent to the email on file. "
                "Ask the user to enter the OTP code they received."
            )
        else:
            result["directive"] = (
                "A different WhatsApp number is already connected to an account with this ID. "
                "We could not send a verification code. Please contact support or try again later."
            )
    else:
        result["directive"] = "Account creation failed. Please try again later."

    if visitor is None:
        try:
            from jvagent.tooling.tool_executor import get_dispatch_visitor

            visitor = get_dispatch_visitor()
        except Exception:
            visitor = None

    if visitor and account_created:
        account_number = account_result.get("account_number", "")
        fields = _fields_from_extracted_values(extracted_values, account_result)
        await _persist_skill_task_data(
            visitor,
            _ONBOARDING_SKILL_NAME,
            fields,
            account_number=account_number or None,
            flow_mode="onboard",
        )

        conversation = await _get_conversation(visitor)
        if conversation:
            ctx = getattr(conversation, "context", None)
            if ctx is not None and isinstance(ctx, dict):
                ctx["user_is_onboarded"] = "completed"
                ctx.pop("onboarding_required", None)
                if account_number:
                    ctx["customer_id"] = str(account_number)
                try:
                    await conversation.save()
                except Exception as e:
                    logger.error(f"Failed to save conversation context: {e}")

        try:
            store = visitor.tasks
            for handle in store.list(
                status="active", owner_action=_ONBOARDING_SKILL_NAME
            ):
                await handle.complete()
        except Exception as exc:
            logger.debug("complete_onboarding: complete skill task failed: %s", exc)

        result["retain_context_keys"] = ["user_is_onboarded", "customer_id"]

    return result


# ─── ID card helpers (process_id_card) ─────────────────────────────────


async def _send_whatsapp_otp(interview_action: Any, email: str) -> bool:
    if not email or not interview_action:
        return False
    try:
        api = await interview_action.get_action("ZoonAPIAction")
        if api and hasattr(api, "request_whatsapp_otp"):
            otp_result = await api.request_whatsapp_otp(email)
            return bool(otp_result and otp_result.get("status") not in (400, None))
    except Exception as e:
        logger.error(f"send_whatsapp_otp failed: {e}")
    return False


def _get_image_urls(visitor: Any) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    if (
        not visitor
        or not hasattr(visitor, "data")
        or not isinstance(visitor.data, dict)
    ):
        return result

    whatsapp_media = visitor.data.get("whatsapp_media")
    if whatsapp_media:
        if isinstance(whatsapp_media, list):
            for item in whatsapp_media:
                if isinstance(item, str):
                    result.append({"url": item})
                elif isinstance(item, dict) and ("url" in item or "base64" in item):
                    result.append(item)
        elif isinstance(whatsapp_media, str):
            result.append({"url": whatsapp_media})
        elif isinstance(whatsapp_media, dict) and (
            "url" in whatsapp_media or "base64" in whatsapp_media
        ):
            result.append(whatsapp_media)

    image_urls = visitor.data.get("image_urls")
    if not result and image_urls and isinstance(image_urls, list):
        for item in image_urls:
            if isinstance(item, str):
                result.append({"url": item})
            elif isinstance(item, dict) and ("url" in item or "base64" in item):
                result.append(item)

    return result
