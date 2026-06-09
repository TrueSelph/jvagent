"""Session CRUD and session-scoped interview tool handlers."""

from __future__ import annotations

import json
import logging
from typing import Any, List, Optional, Tuple

from jvagent.tooling.tool_executor import get_dispatch_visitor

from .._constants import (
    _question_def_to_dict,
    _validator_def_to_dict,
)
from ..core.interview_loader import InterviewSpec
from ..core.responses import (
    interview_tool_response,
    no_session_directive,
    tell_user_directive,
    tell_user_with_followup_directive,
)
from ..core.session import (
    CONVERSATION_CONTEXT_PLATFORM_KEYS,
    InterviewSession,
    InterviewStatus,
    clear_interview_context,
    clear_session,
    load_session,
    save_session,
)
from ..core.tools import skill_tool_name
from ..runtime.hooks import call_hook, load_hook_function
from ..runtime.path_resolver import (
    build_next_questions,
    compute_reachable_required,
    missing_required_reachable,
)
from ._host import InterviewHandlersHost

logger = logging.getLogger(__name__)


class InterviewSessionHandlersMixin(InterviewHandlersHost):

    async def _get_conversation(self, visitor: Any = None):
        if visitor is None:
            visitor = get_dispatch_visitor()
        if visitor is None:
            return None
        if hasattr(visitor, "conversation") and visitor.conversation is not None:
            return visitor.conversation
        if hasattr(visitor, "interaction") and visitor.interaction is not None:
            interaction = visitor.interaction
            if hasattr(interaction, "get_conversation"):
                return await interaction.get_conversation()
        return None

    async def _get_session(self, visitor: Any = None) -> Optional[InterviewSession]:
        conversation = await self._get_conversation(visitor)
        return load_session(conversation) if conversation else None

    async def _save_session(self, session: InterviewSession, visitor: Any = None):
        conversation = await self._get_conversation(visitor)
        if conversation:
            await save_session(conversation, session)

    async def _clear_interview_session(
        self,
        visitor: Any = None,
        *,
        retain_context_keys: Optional[List[str]] = None,
    ) -> None:
        conversation = await self._get_conversation(visitor)
        if not conversation:
            return
        retain = set(CONVERSATION_CONTEXT_PLATFORM_KEYS)
        if retain_context_keys:
            retain.update(retain_context_keys)
        clear_interview_context(conversation, retain_keys=retain)
        try:
            await conversation.save()
        except Exception:
            pass

    async def _get_session_and_contract(
        self, visitor: Any = None
    ) -> Tuple[Optional[InterviewSession], Optional[InterviewSpec]]:
        await self._ensure_specs_loaded()
        session = await self._get_session(visitor)
        if not session:
            return None, None
        return session, self._registry.get(session.interview_type)

    async def _handle_start(
        self,
        interview_type: str,
        visitor: Any = None,
        **kwargs: Any,
    ) -> str:
        spec = self._registry.get(interview_type)
        if not spec:
            available = self._registry.list_specs()
            return interview_tool_response(
                status="error",
                error_code="UNKNOWN_INTERVIEW_TYPE",
                response_directive=(
                    f"Interview type '{interview_type}' not found. "
                    f"Available types: {available}"
                ),
                available_types=available,
            )

        conversation = await self._get_conversation(visitor)
        existing = load_session(conversation) if conversation else None

        if existing and existing.is_active():
            if visitor:
                try:
                    await self._ensure_active_task(visitor, spec)
                except Exception:
                    pass
            required = await compute_reachable_required(
                existing, spec, self._load_fn(spec), visitor, self
            )
            return interview_tool_response(
                ok=True,
                status=existing.status.value,
                interview_type=existing.interview_type,
                fields=existing.get_collected_summary(),
                skipped_fields=sorted(existing.skipped_fields),
                missing_required=missing_required_reachable(existing, required),
                questions=[_question_def_to_dict(q) for q in spec.questions],
                validators=[_validator_def_to_dict(v) for v in spec.validators],
                custom_tools=[skill_tool_name(spec, t.name) for t in spec.tools],
            )

        fresh_session = False
        if conversation and existing:
            if existing.interview_type != interview_type or existing.status in (
                InterviewStatus.COMPLETED,
                InterviewStatus.CANCELLED,
            ):
                if existing.interview_type != interview_type and visitor:
                    try:
                        await self._close_task(
                            visitor,
                            status="cancelled",
                            spec_name=existing.interview_type,
                        )
                    except Exception:
                        pass
                clear_session(conversation)
                fresh_session = True
        elif not existing:
            fresh_session = True

        session = InterviewSession(interview_type=interview_type)
        if conversation:
            await save_session(conversation, session)
        if visitor:
            try:
                await self._ensure_active_task(visitor, spec)
            except Exception:
                pass

        required = await compute_reachable_required(
            session, spec, self._load_fn(spec), visitor, self
        )
        missing = missing_required_reachable(session, required)

        return interview_tool_response(
            ok=True,
            status="active",
            fresh_session=fresh_session,
            interview_type=spec.name,
            fields=session.get_collected_summary(),
            skipped_fields=sorted(session.skipped_fields),
            missing_required=missing,
            questions=[_question_def_to_dict(q) for q in spec.questions],
            validators=[_validator_def_to_dict(v) for v in spec.validators],
            custom_tools=[skill_tool_name(spec, t.name) for t in spec.tools],
        )

    async def _handle_get_status(self, visitor: Any = None) -> str:
        session, spec = await self._get_session_and_contract(visitor)
        if not session:
            available = self._registry.list_specs()
            return interview_tool_response(
                ok=False,
                status="no_session",
                available_types=available,
                response_directive=(
                    "No active interview session. Available types: "
                    + ", ".join(available)
                    if available
                    else "No interview types configured."
                ),
            )

        required = (
            await compute_reachable_required(
                session, spec, self._load_fn(spec), visitor, self
            )
            if spec
            else []
        )
        missing = missing_required_reachable(session, required)
        return interview_tool_response(
            ok=True,
            status=session.status.value,
            interview_type=session.interview_type,
            fields=session.get_collected_summary(),
            skipped_fields=sorted(session.skipped_fields),
            missing_required=missing,
            next_questions=(
                await build_next_questions(
                    session, spec, self._load_fn(spec), visitor, self
                )
                if spec
                else []
            ),
            started_at=session.started_at,
            questions=(
                [_question_def_to_dict(q) for q in spec.questions] if spec else None
            ),
            validators=(
                [_validator_def_to_dict(v) for v in spec.validators] if spec else None
            ),
            custom_tools=(
                [skill_tool_name(spec, t.name) for t in spec.tools] if spec else None
            ),
        )

    async def _handle_cancel(self, visitor: Any = None) -> str:
        session, spec = await self._get_session_and_contract(visitor)
        if not session:
            return interview_tool_response(
                ok=False,
                status="error",
                response_directive="No active interview session to cancel.",
            )

        cancel_message = (
            "I've cancelled this. Say what you'd like to do next, or start a new "
            "interview when you're ready."
        )
        if spec and spec.cancel and spec.cancel.function:
            func = load_hook_function(spec, spec.cancel.function)
            if func:
                try:
                    result = await call_hook(
                        func,
                        session=session,
                        spec=spec,
                        visitor=visitor,
                        interview_action=self,
                    )
                    if isinstance(result, dict):
                        cancel_message = (
                            result.get("response_directive")
                            or result.get("directive")
                            or result.get("message")
                            or cancel_message
                        )
                    elif isinstance(result, str):
                        cancel_message = result
                except Exception as e:
                    logger.error("Cancel handler failed: %s", e)

        await self._clear_interview_session(visitor)
        if visitor:
            try:
                await self._close_task(
                    visitor, status="cancelled", spec_name=spec.name if spec else None
                )
            except Exception:
                pass
        return interview_tool_response(
            ok=True,
            status="cancelled",
            response_directive=tell_user_directive(cancel_message),
            fields={},
        )

    async def _handle_reset_interview(self, visitor: Any = None) -> str:
        session, spec = await self._get_session_and_contract(visitor)
        if not session or not spec:
            return interview_tool_response(
                ok=False,
                status="error",
                error_code="NO_SESSION",
                response_directive="No active interview session to reset.",
            )

        if spec.reset and spec.reset.function:
            return await self._handle_custom_reset(session, spec, visitor)

        return await self._default_reset_interview(session, spec, visitor)

    async def _default_reset_interview(
        self,
        session: InterviewSession,
        spec: InterviewSpec,
        visitor: Any = None,
    ) -> str:
        skill_name = session.interview_type
        await self._clear_interview_session(visitor)
        try:
            await self._close_task(
                visitor, status="cancelled", spec_name=spec.name if spec else None
            )
        except Exception:
            pass

        try:
            await self._handle_start(skill_name, visitor, user_message="")
            next_obs = await self._handle_next_question(visitor)
            first_question = "Please continue."
            try:
                parsed = json.loads(next_obs)
                next_qs = parsed.get("next_questions") or []
                if next_qs and next_qs[0].get("question"):
                    first_question = str(next_qs[0]["question"])
            except (json.JSONDecodeError, TypeError, IndexError, KeyError):
                pass
            return interview_tool_response(
                ok=True,
                status="restarted",
                response_directive=tell_user_with_followup_directive(
                    "No problem — let's start over.",
                    first_question,
                ),
            )
        except Exception as exc:
            logger.error("reset_interview failed for %s: %s", skill_name, exc)
            return interview_tool_response(
                ok=False,
                status="error",
                response_directive=tell_user_directive(
                    "I couldn't restart the interview. Say when you'd like to try again."
                ),
            )

    async def _handle_custom_reset(
        self,
        session: InterviewSession,
        spec: InterviewSpec,
        visitor: Any = None,
    ) -> str:
        reset_def = spec.reset
        if not reset_def or not reset_def.function:
            return await self._default_reset_interview(session, spec, visitor)

        func = load_hook_function(spec, reset_def.function)
        if not func:
            return await self._default_reset_interview(session, spec, visitor)

        try:
            result = await call_hook(
                func,
                session=session,
                spec=spec,
                visitor=visitor,
                interview_action=self,
            )
        except Exception as exc:
            logger.error("Custom reset handler failed: %s", exc)
            return interview_tool_response(
                ok=False,
                status="error",
                response_directive=tell_user_directive(
                    "I couldn't reset the interview. Say when you'd like to try again."
                ),
            )

        coerced = self._coerce_reset_hook_result(result)
        if coerced is not None:
            return coerced
        return await self._default_reset_interview(session, spec, visitor)

    @staticmethod
    def _coerce_reset_hook_result(result: Any) -> Optional[str]:
        if isinstance(result, str):
            try:
                json.loads(result)
                return result
            except json.JSONDecodeError:
                text = result.strip()
                if not text:
                    return None
                return interview_tool_response(
                    ok=True,
                    status="restarted",
                    response_directive=(
                        text
                        if text.startswith("Tell the user:")
                        else tell_user_directive(text)
                    ),
                )

        if isinstance(result, dict):
            directive = (
                result.get("response_directive")
                or result.get("directive")
                or result.get("message")
            )
            status = str(result.get("status") or "restarted")
            ok = result.get("ok")
            if ok is None:
                ok = status not in ("error", "validation_failed")
            if directive and not str(directive).startswith("Tell the user:"):
                directive = tell_user_directive(str(directive))
            return interview_tool_response(
                ok=bool(ok),
                status=status,
                response_directive=directive,
                system_message=result.get("system_message"),
            )
        return None
