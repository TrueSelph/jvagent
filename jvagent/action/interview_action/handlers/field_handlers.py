"""Field read/write interview tool handlers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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

    async def _handle_set_fields(
        self,
        fields: Optional[Dict[str, str]] = None,
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

        field_map = self._normalize_field_map(fields, field, value, **kwargs)
        if not field_map:
            return interview_tool_response(
                ok=False,
                status="error",
                error_code="NO_FIELDS",
                error="No fields provided.",
                response_directive=tell_user_directive(
                    "Please provide the information again — no fields were specified."
                ),
            )

        if session.status == InterviewStatus.COMPLETED:
            return interview_tool_response(
                status="completed",
                response_directive=restart_session_directive(session.interview_type),
            )

        from jvagent.action.orchestrator.skill_tasks import visitor_utterance

        utterance = visitor_utterance(visitor) if visitor else ""
        results: List[Dict[str, Any]] = []
        all_ok = True
        last_payload: Dict[str, Any] = {}

        for fname, fvalue in field_map.items():
            resolved_field, field_err = self._resolve_field_param(fname, spec)
            if field_err:
                results.append(
                    {"field": fname, "ok": False, "error": field_err, "stored": False}
                )
                all_ok = False
                continue

            stored_value = (fvalue or "").strip()
            if not stored_value:
                if len(field_map) == 1:
                    stored_value = (utterance or "").strip()
                if not stored_value:
                    results.append(
                        {
                            "field": resolved_field,
                            "ok": False,
                            "error": "Missing value",
                            "stored": False,
                        }
                    )
                    all_ok = False
                    continue

            payload = await apply_store_pipeline(
                self, session, spec, resolved_field, stored_value, visitor
            )
            last_payload = payload
            entry: Dict[str, Any] = {
                "field": resolved_field,
                "ok": bool(payload.get("ok")),
                "stored": payload.get("stored"),
                "already_stored": payload.get("already_stored"),
                "value": payload.get("value"),
            }
            if not payload.get("ok"):
                entry["error"] = payload.get("error") or payload.get(
                    "validation_failed"
                )
                all_ok = False
            if payload.get("post_tools_results"):
                entry["post_tools_results"] = payload["post_tools_results"]
            results.append(entry)

            if payload.get("interview_complete"):
                payload["results"] = results
                payload["ok"] = all_ok and bool(payload.get("ok"))
                return interview_tool_response_from_payload(payload)

            if not payload.get("ok"):
                if len(field_map) == 1:
                    payload["results"] = results
                    return interview_tool_response_from_payload(payload)
                break

        if len(field_map) == 1 and all_ok and last_payload:
            last_payload["results"] = results
            return interview_tool_response_from_payload(last_payload)

        final: Dict[str, Any] = {
            "ok": all_ok,
            "status": session.status.value,
            "results": results,
            "fields": session.get_collected_summary(),
            "skipped_fields": sorted(session.skipped_fields),
        }
        if not all_ok and last_payload and not last_payload.get("ok"):
            final.update(
                {
                    k: last_payload[k]
                    for k in last_payload
                    if k
                    not in (
                        "fields",
                        "skipped_fields",
                        "results",
                    )
                }
            )
            final["results"] = results
            final["ok"] = False
        for key in (
            "missing_required",
            "response_directive",
            "next_tool",
            "next_questions",
            "post_tools_results",
            "field",
            "value",
            "validator",
            "validated_from",
        ):
            if key in last_payload and key not in final:
                final[key] = last_payload[key]
        if len(results) == 1:
            final["field"] = results[0].get("field")
            final["value"] = results[0].get("value")
            final["stored"] = results[0].get("stored")
            final["already_stored"] = results[0].get("already_stored")

        return interview_tool_response_from_payload(final)

    async def _handle_set_field(
        self,
        field: str = "",
        value: str = "",
        visitor: Any = None,
        **kwargs: Any,
    ) -> str:
        return await self._handle_set_fields(
            field=field, value=value, visitor=visitor, **kwargs
        )

    async def _handle_get_fields(
        self,
        fields: Optional[List[str]] = None,
        field: str = "",
        visitor: Any = None,
    ) -> str:
        session, spec = await self._get_session_and_contract(visitor)
        if not session or not spec:
            return interview_tool_response(
                ok=False,
                status="error",
                error_code="NO_SESSION",
                response_directive=no_session_directive(),
            )

        names = list(fields or [])
        if field and field not in names:
            names.append(field)
        if not names:
            names = sorted(session.fields.keys())

        values: Dict[str, Any] = {}
        for name in names:
            resolved_field, field_err = self._resolve_field_param(name, spec)
            if field_err:
                values[name] = {"ok": False, "error": field_err, "exists": False}
                continue
            if session.is_skipped(resolved_field):
                values[resolved_field] = {
                    "ok": True,
                    "value": None,
                    "exists": False,
                    "skipped": True,
                }
                continue
            val = session.get_value(resolved_field)
            values[resolved_field] = {
                "ok": True,
                "value": val,
                "exists": val is not None and bool(str(val).strip()),
            }

        return interview_tool_response(
            ok=True,
            status=session.status.value,
            values=values,
            fields=session.get_collected_summary(),
            skipped_fields=sorted(session.skipped_fields),
        )

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

        val = session.get_value(resolved_field)
        return interview_tool_response(
            ok=True,
            status=session.status.value,
            field=resolved_field,
            value=val,
            exists=val is not None and bool(str(val).strip()),
            fields=session.get_collected_summary(),
            skipped_fields=sorted(session.skipped_fields),
        )

    @staticmethod
    def _normalize_field_map(
        fields: Optional[Dict[str, str]],
        field: str,
        value: str,
        **kwargs: Any,
    ) -> Dict[str, str]:
        field_map: Dict[str, str] = {}
        if fields and isinstance(fields, dict):
            field_map = {str(k): str(v) for k, v in fields.items()}
        legacy_field = (field or kwargs.get("field") or "").strip()
        if legacy_field:
            field_map[legacy_field] = value or kwargs.get("value") or ""
        return field_map

    def _resolve_field_param(
        self, field: str, spec: InterviewSpec, **kwargs: Any
    ) -> tuple[str, Optional[str]]:
        resolved = (field or "").strip()
        if not resolved:
            return "", "Missing field parameter"
        valid = {f.key for f in spec.fields}
        if resolved not in valid:
            return "", f"Unknown field '{resolved}'. Valid: {sorted(valid)}"
        return resolved, None
