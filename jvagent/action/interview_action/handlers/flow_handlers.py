"""Flow, review, completion, and validator handlers."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from jvagent.tooling.tool_executor import get_dispatch_visitor

from .._constants import _parse_validation_result
from ..core.interview_loader import InterviewSpec, ToolDef, ValidatorDef
from ..core.responses import (
    call_tool_directive,
    interview_tool_response,
    interview_tool_response_from_payload,
    no_session_directive,
    review_confirmation_directive,
    tell_user_directive,
    tell_user_with_followup_directive,
)
from ..core.session import (
    CTX_QUESTION_PRESENTED,
    InterviewSession,
    InterviewStatus,
)
from ..core.tools import skill_tool_name
from ..core.validators import ExtractionStatus, get_validator
from ..runtime.hooks import call_hook, load_hook_function
from ..runtime.path_resolver import (
    build_next_questions,
    compute_reachable_question_names,
    compute_reachable_required,
    missing_required_reachable,
    prune_unreachable_fields,
    resolve_store_continuation,
)
from ..runtime.pipeline import (
    finalize_store_continuation,
    run_pre_tools,
    validate_field,
)
from ._host import InterviewHandlersHost

logger = logging.getLogger(__name__)


class InterviewFlowHandlersMixin(InterviewHandlersHost):

    async def _handle_next_question(self, visitor: Any = None) -> str:
        session, spec = await self._get_session_and_contract(visitor)
        if not session or not spec:
            return interview_tool_response(
                ok=False,
                status="error",
                error_code="NO_SESSION",
                error="No active interview session.",
                response_directive=no_session_directive(),
            )

        load_fn = self._load_fn(spec)
        required = await compute_reachable_required(
            session, spec, load_fn, visitor, self
        )
        missing = missing_required_reachable(session, required)
        next_qs = await build_next_questions(session, spec, load_fn, visitor, self)

        if not next_qs:
            return interview_tool_response(
                ok=True,
                status=session.status.value,
                fields=session.get_collected_summary(),
                skipped_fields=sorted(session.skipped_fields),
                missing_required=missing,
                next_questions=[],
                next_tool="interview__review",
                response_directive=call_tool_directive("interview__review"),
            )

        q_def = spec.get_question(next_qs[0]["name"])
        if not q_def:
            return interview_tool_response(
                ok=False,
                status="error",
                error="No question definition found for the next step.",
            )

        directive, extras = await run_pre_tools(self, session, spec, q_def, visitor)
        pre_tools_results = extras.get("pre_tools_results") or []
        if any(not r.get("ok", True) for r in pre_tools_results):
            return interview_tool_response(
                ok=False,
                status="error",
                error="One or more pre_tools failed.",
                fields=session.get_collected_summary(),
                missing_required=missing,
                next_questions=next_qs,
                pre_tools_results=pre_tools_results,
            )
        if extras.get("suggested_value") is not None:
            next_qs[0]["suggested_value"] = extras["suggested_value"]

        if not isinstance(session.context, dict):
            session.context = {}
        session.context[CTX_QUESTION_PRESENTED] = next_qs[0]["name"]
        await self._save_session(session, visitor)

        return interview_tool_response(
            ok=True,
            status=session.status.value,
            fields=session.get_collected_summary(),
            skipped_fields=sorted(session.skipped_fields),
            missing_required=missing,
            next_questions=next_qs,
            pre_tools_results=pre_tools_results,
            response_directive=directive,
        )

    async def _handle_skip_field(self, field: str, visitor: Any = None) -> str:
        session, spec = await self._get_session_and_contract(visitor)
        if not session or not spec:
            return interview_tool_response(
                ok=False,
                status="error",
                error_code="NO_SESSION",
                response_directive="No active interview session.",
            )

        q = spec.get_question(field)
        if q and q.required:
            question = q.question or f"Please provide your {field.replace('_', ' ')}."
            return interview_tool_response(
                ok=False,
                status=session.status.value,
                response_directive=tell_user_directive(question),
            )

        session.skip_field(field)
        reachable = await compute_reachable_question_names(
            session, spec, self._load_fn(spec), visitor, self
        )
        prune_unreachable_fields(session, reachable)
        await self._save_session(session, visitor)

        required = await compute_reachable_required(
            session, spec, self._load_fn(spec), visitor, self
        )
        missing = missing_required_reachable(session, required)
        _directive, next_tool = await resolve_store_continuation(
            session, spec, self._load_fn(spec), visitor, self
        )

        payload = await finalize_store_continuation(
            self,
            visitor,
            {
                "ok": True,
                "status": session.status.value,
                "field": field,
                "value": None,
                "fields": session.get_collected_summary(),
                "skipped_fields": sorted(session.skipped_fields),
                "missing_required": missing,
                "response_directive": _directive,
                "next_tool": next_tool,
            },
        )
        return interview_tool_response_from_payload(payload)

    async def _handle_review(self, visitor: Any = None) -> str:
        session, spec = await self._get_session_and_contract(visitor)
        if not session or not spec:
            return interview_tool_response(
                ok=False,
                status="error",
                response_directive="No active interview session.",
            )
        if spec.review and spec.review.function:
            return await self._handle_custom_review(visitor)
        return await self._default_review(session, spec, visitor)

    def _build_review_summary(
        self,
        session: InterviewSession,
        spec: InterviewSpec,
        collected: Dict[str, str],
        *,
        omit_fields: Optional[set] = None,
        additional_data: Optional[Dict[str, Any]] = None,
    ) -> str:
        omitted = omit_fields or set()
        review_lines = []
        for q in spec.questions:
            if q.name in omitted or session.is_skipped(q.name):
                continue
            if q.name in collected:
                label = q.name.replace("_", " ").title()
                review_lines.append(f"**{label}**: {collected[q.name]}")
            elif q.required:
                label = q.name.replace("_", " ").title()
                review_lines.append(f"**{label}**: *(not provided)*")
        for label, value in (additional_data or {}).items():
            review_lines.append(f"**{label}**: {value}")
        return "\n\n".join(review_lines)

    async def _default_review(
        self, session: InterviewSession, spec: InterviewSpec, visitor: Any
    ) -> str:
        collected = session.get_collected_summary()
        summary = self._build_review_summary(session, spec, collected)
        session.status = InterviewStatus.REVIEW
        await self._save_session(session, visitor)
        return interview_tool_response(
            ok=True,
            status="review",
            response_directive=review_confirmation_directive(summary),
            fields=collected,
            skipped_fields=sorted(session.skipped_fields),
            summary=summary,
            next_questions=[],
            system_message=(
                "Confirmation step — wait for user to confirm before interview__complete."
            ),
        )

    async def _handle_complete(self, visitor: Any = None) -> str:
        session, spec = await self._get_session_and_contract(visitor)
        if not session or not spec:
            return interview_tool_response(
                ok=False,
                status="error",
                response_directive="No active interview session.",
            )
        if spec.completion and spec.completion.function:
            return await self._handle_custom_complete(visitor)
        fields_summary = session.get_collected_summary()
        await self._clear_interview_session(visitor)
        if visitor:
            try:
                await self._close_task(visitor, status="completed", spec_name=spec.name)
            except Exception:
                pass
        return interview_tool_response(
            ok=True,
            status="completed",
            response_directive="Interview completed successfully.",
            fields=fields_summary,
        )

    async def _handle_custom_review(self, visitor: Any = None) -> str:
        session, spec = await self._get_session_and_contract(visitor)
        if not session or not spec or not spec.review or not spec.review.function:
            return await self._handle_review(visitor)

        func = load_hook_function(spec, spec.review.function)
        if not func:
            return await self._default_review(session, spec, visitor)

        try:
            result = await call_hook(
                func, session=session, spec=spec, visitor=visitor, interview_action=self
            )
        except Exception as e:
            return interview_tool_response(
                ok=False,
                status="error",
                response_directive=f"Custom review function failed: {e}",
            )

        collected = session.get_collected_summary()
        omit_fields: set = set()
        additional_data: Dict[str, Any] = {}
        custom_message = ""
        directive = ""
        terminate = False

        if isinstance(result, dict):
            modified_values = result.get("modified_values", {})
            additional_data = result.get("additional_data", {})
            custom_message = result.get("custom_message", "")
            directive = result.get("directive") or result.get("response_directive", "")
            terminate = bool(
                result.get("terminate")
                or modified_values.get("__terminate__") == "true"
            )
            for field_name, field_value in modified_values.items():
                if field_name == "__terminate__":
                    continue
                if field_value == "__omit__":
                    omit_fields.add(field_name)
                elif field_name in collected:
                    collected[field_name] = field_value

        if terminate:
            if visitor:
                try:
                    await self._close_task(
                        visitor, status="completed", spec_name=spec.name
                    )
                except Exception:
                    pass
            await self._clear_interview_session(visitor)
            status_text = custom_message or directive or "Share the status update."
            return interview_tool_response(
                ok=True,
                status="completed",
                terminate=True,
                response_directive=tell_user_directive(status_text),
                fields=collected,
                skipped_fields=sorted(session.skipped_fields),
                custom_message=custom_message,
            )

        summary = self._build_review_summary(
            session,
            spec,
            collected,
            omit_fields=omit_fields,
            additional_data=additional_data,
        )

        preamble = custom_message or directive
        if preamble and not preamble.strip().startswith("Tell the user:"):
            directive = review_confirmation_directive(summary, preamble=preamble)
        else:
            directive = review_confirmation_directive(summary)

        session.status = InterviewStatus.REVIEW
        await self._save_session(session, visitor)
        return interview_tool_response(
            ok=True,
            status="review",
            response_directive=directive,
            fields=collected,
            skipped_fields=sorted(session.skipped_fields),
            summary=summary,
            custom_message=custom_message,
            system_message=(
                "Confirmation step — wait for user to confirm before interview__complete."
            ),
        )

    async def _handle_custom_complete(self, visitor: Any = None) -> str:
        session, spec = await self._get_session_and_contract(visitor)
        if not session or not spec:
            return interview_tool_response(
                ok=False,
                status="error",
                response_directive="No active interview session.",
            )
        cc = spec.completion
        if not cc or not cc.function:
            return interview_tool_response(
                ok=False,
                status="error",
                response_directive="No completion function configured.",
            )
        func = load_hook_function(spec, cc.function)
        if not func:
            return interview_tool_response(
                ok=False,
                status="error",
                response_directive=f"Completion function '{cc.function}' not found.",
            )
        try:
            result = await call_hook(
                func, session=session, spec=spec, visitor=visitor, interview_action=self
            )
        except Exception as e:
            return interview_tool_response(
                ok=False,
                status="error",
                response_directive=f"Completion function failed: {e}",
            )

        fields_summary = session.get_collected_summary()
        if visitor:
            try:
                await self._close_task(visitor, status="completed", spec_name=spec.name)
            except Exception:
                pass
        retain_keys: List[str] = []
        if isinstance(result, dict):
            raw_retain = result.get("retain_context_keys")
            if isinstance(raw_retain, list):
                retain_keys = [str(k) for k in raw_retain if k]
        await self._clear_interview_session(visitor, retain_context_keys=retain_keys)

        if isinstance(result, dict):
            raw_directive = (
                result.get("response_directive")
                or result.get("directive")
                or result.get("message")
                or "Interview completed."
            )
            if raw_directive.strip().startswith(
                "Tell the user:"
            ) or raw_directive.strip().startswith("Call "):
                directive = raw_directive
            else:
                directive = tell_user_directive(raw_directive)
            return interview_tool_response(
                ok=True,
                status="completed",
                response_directive=directive,
                completion_result=result,
                fields=fields_summary,
            )
        return interview_tool_response(
            ok=True,
            status="completed",
            response_directive="Interview completed successfully.",
            fields=fields_summary,
        )

    async def _handle_custom_tool(
        self, tdef: ToolDef, spec: InterviewSpec, **kwargs
    ) -> str:
        if not tdef.function:
            return json.dumps({"error": f"Custom tool '{tdef.name}' has no function"})
        func = load_hook_function(spec, tdef.function)
        if not func:
            return json.dumps({"error": f"Function '{tdef.function}' not found"})
        try:
            visitor = kwargs.pop("visitor", None) or get_dispatch_visitor()
            session = await self._get_session(visitor)
            call_kwargs = dict(kwargs)
            call_kwargs["visitor"] = visitor
            result = await call_hook(
                func,
                session=session,
                spec=spec,
                visitor=visitor,
                interview_action=self,
                kwargs=call_kwargs,
            )
            if isinstance(result, str):
                try:
                    parsed = json.loads(result)
                    if isinstance(parsed, dict):
                        return await self._finalize_tool_response(
                            parsed, session, visitor
                        )
                except (json.JSONDecodeError, TypeError):
                    pass
                return result
            if isinstance(result, dict):
                return await self._finalize_tool_response(result, session, visitor)
            return json.dumps(
                {"result": "ok"} if result is not None else {"result": "empty"}
            )
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def persist_interview_fields(
        self,
        session: InterviewSession,
        visitor: Any,
        fields: Dict[str, str],
        *,
        validate: bool = True,
    ) -> Dict[str, Any]:
        spec = self._registry.get(session.interview_type)
        if not spec:
            return {
                "stored": [],
                "stored_values": {},
                "validation_errors": {"_session": "No spec found for interview type"},
            }
        stored: List[str] = []
        stored_values: Dict[str, str] = {}
        validation_errors: Dict[str, str] = {}
        for name, raw_value in fields.items():
            if raw_value is None:
                continue
            value = str(raw_value).strip()
            if not value:
                continue
            if validate:
                check = await validate_field(self, spec, name, value, session, visitor)
                if not check.get("valid"):
                    validation_errors[name] = check.get("error", "Validation failed")
                    continue
                value = check.get("value", value)
            session.set_value(name, value)
            stored.append(name)
            stored_values[name] = value
        if stored:
            reachable = await compute_reachable_question_names(
                session, spec, self._load_fn(spec), visitor, self
            )
            prune_unreachable_fields(session, reachable)
            if session.status == InterviewStatus.REVIEW:
                required = await compute_reachable_required(
                    session, spec, self._load_fn(spec), visitor, self
                )
                if missing_required_reachable(session, required):
                    session.status = InterviewStatus.ACTIVE
            await self._save_session(session, visitor)
        return {
            "stored": stored,
            "stored_values": stored_values,
            "validation_errors": validation_errors,
        }

    async def _run_validator(
        self,
        vdef: ValidatorDef,
        value: str,
        kwargs: dict,
        visitor: Any = None,
        session: Optional[InterviewSession] = None,
        spec: Optional[InterviewSpec] = None,
    ) -> str:
        validated_value = value.strip() if value else ""
        if not validated_value:
            return json.dumps(
                {
                    "valid": False,
                    "error": f"No value provided for validation by {vdef.name}",
                    "validator": vdef.name,
                }
            )

        validator_fn = get_validator(vdef.name)
        if validator_fn:
            v_kwargs = dict(vdef.kwargs)
            question_kwargs = kwargs if isinstance(kwargs, dict) else {}
            for k in (
                "exact_length",
                "min_length",
                "max_length",
                "length",
                "date_format",
                "allow_decimal",
                "allow_negative",
                "pattern",
            ):
                if k in question_kwargs and k not in v_kwargs:
                    v_kwargs[k] = question_kwargs[k]
            try:
                status, error_msg, autocorrected = validator_fn(
                    validated_value, **v_kwargs
                )
                if status == ExtractionStatus.EXTRACTED:
                    return json.dumps(
                        {
                            "valid": True,
                            "value": autocorrected or validated_value,
                            "validator": vdef.name,
                        }
                    )
                return json.dumps(
                    {
                        "valid": False,
                        "error": error_msg or f"Validation failed for {vdef.name}",
                        "value": validated_value,
                        "validator": vdef.name,
                    }
                )
            except Exception as e:
                return json.dumps(
                    {
                        "valid": False,
                        "error": f"Validator error: {e}",
                        "validator": vdef.name,
                    }
                )

        specs_to_search: List[InterviewSpec] = []
        if spec is not None:
            specs_to_search.append(spec)
        else:
            specs_to_search.extend(self._registry.specs.values())

        for hook_spec in specs_to_search:
            func = load_hook_function(hook_spec, vdef.name)
            if func and callable(func):
                try:
                    result = await call_hook(
                        func,
                        session=session,
                        spec=hook_spec,
                        visitor=visitor or get_dispatch_visitor(),
                        interview_action=self,
                        value=validated_value,
                        kwargs=kwargs,
                    )
                    parsed = _parse_validation_result(
                        result, validated_value, vdef.name
                    )
                    return json.dumps(parsed)
                except Exception as e:
                    return json.dumps(
                        {
                            "valid": False,
                            "error": f"Validator error: {e}",
                            "validator": vdef.name,
                        }
                    )

        scope = spec.name if spec is not None else "registry"
        return json.dumps(
            {
                "valid": False,
                "error": f"No validator found for {vdef.name} in {scope}",
                "validator": vdef.name,
            }
        )

    async def _finalize_tool_response(
        self, parsed: Dict[str, Any], session: Optional[InterviewSession], visitor: Any
    ) -> str:
        persist_fields = parsed.get("persist_fields")
        if persist_fields and session and isinstance(persist_fields, dict):
            await self.persist_interview_fields(
                session, visitor, persist_fields, validate=True
            )
        if session:
            spec = self._registry.get(session.interview_type)
            if spec:
                parsed.setdefault("fields", session.get_collected_summary())
                parsed.setdefault("skipped_fields", sorted(session.skipped_fields))
                required = await compute_reachable_required(
                    session, spec, self._load_fn(spec), visitor, self
                )
                parsed.setdefault(
                    "missing_required", missing_required_reachable(session, required)
                )
        return json.dumps(parsed)
