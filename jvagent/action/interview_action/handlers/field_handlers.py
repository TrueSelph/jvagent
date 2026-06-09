"""Field read/write interview tool handlers."""

from __future__ import annotations

from typing import Any, Optional

from ..core.interview_loader import InterviewSpec
from ..core.responses import (
    interview_tool_response,
    interview_tool_response_from_payload,
    no_session_directive,
    restart_session_directive,
    tell_user_directive,
)
from ..core.session import InterviewStatus
from ..runtime.pipeline import apply_store_pipeline
from ._host import InterviewHandlersHost


class InterviewFieldHandlersMixin(InterviewHandlersHost):

    async def _handle_set_field(
        self,
        field: str = "",
        value: str = "",
        visitor: Any = None,
        **kwargs: Any,
    ) -> str:
        session, spec = await self._get_session_and_contract(visitor)
        if not session or not spec:
            return interview_tool_response(
                ok=False,
                status="error",
                error_code="NO_SESSION",
                response_directive=no_session_directive(),
            )

        resolved_field, field_err = self._resolve_field_param(field, spec, **kwargs)
        if field_err:
            return interview_tool_response(
                ok=False,
                status="error",
                error_code="INVALID_FIELD",
                error=field_err,
                response_directive=tell_user_directive(
                    "Please provide the information again — the system could not "
                    "record the last answer."
                ),
            )

        if session.status == InterviewStatus.COMPLETED:
            return interview_tool_response(
                status="completed",
                response_directive=restart_session_directive(session.interview_type),
            )

        if not (value or "").strip():
            from jvagent.action.orchestrator.skill_tasks import visitor_utterance

            value = visitor_utterance(visitor)

        payload = await apply_store_pipeline(
            self, session, spec, resolved_field, value, visitor
        )
        return interview_tool_response_from_payload(payload)

    async def _handle_get_field(self, field: str, visitor: Any = None) -> str:
        session, spec = await self._get_session_and_contract(visitor)
        if not session or not spec:
            return interview_tool_response(
                ok=False,
                status="error",
                error_code="NO_SESSION",
                response_directive=no_session_directive(),
            )

        resolved_field, field_err = self._resolve_field_param(field, spec)
        if field_err:
            return interview_tool_response(
                ok=False,
                status="error",
                error_code="INVALID_FIELD",
                error=field_err,
            )

        if session.is_skipped(resolved_field):
            return interview_tool_response(
                ok=True,
                status=session.status.value,
                field=resolved_field,
                value=None,
                exists=False,
                fields=session.get_collected_summary(),
                skipped_fields=sorted(session.skipped_fields),
            )

        value = session.get_value(resolved_field)
        return interview_tool_response(
            ok=True,
            status=session.status.value,
            field=resolved_field,
            value=value,
            exists=value is not None and bool(str(value).strip()),
            fields=session.get_collected_summary(),
            skipped_fields=sorted(session.skipped_fields),
        )

    def _resolve_field_param(
        self, field: str, spec: InterviewSpec, **kwargs: Any
    ) -> tuple[str, Optional[str]]:
        resolved = (field or "").strip()
        if not resolved:
            return "", "Missing field parameter"
        valid = {q.name for q in spec.questions}
        if resolved not in valid:
            return "", f"Unknown field '{resolved}'. Valid: {sorted(valid)}"
        return resolved, None
