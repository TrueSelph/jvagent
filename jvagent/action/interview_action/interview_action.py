"""InterviewAction — tool-driven interview runtime for orchestrator skills."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from jvagent.action.base import Action
from jvagent.tooling.tool_executor import get_dispatch_visitor

from .core.field_extractors import extract_candidates_for_question
from .core.interview_loader import (
    INTERVIEW_FRONTMATTER_KEY,
    INTERVIEW_YAML,
    InterviewRegistry,
    InterviewSpec,
    QuestionDef,
    ToolDef,
    ValidatorDef,
    resolve_validator_def,
    resolve_validator_kwargs,
)
from .core.responses import (
    call_tool_directive,
    interview_step_response,
    interview_tool_response,
    no_session_directive,
    restart_session_directive,
    review_confirmation_directive,
    tell_user_directive,
    tell_user_with_followup_directive,
)
from .core.session import (
    CONVERSATION_CONTEXT_PLATFORM_KEYS,
    CTX_QUESTION_PRESENTED,
    InterviewSession,
    InterviewStatus,
    clear_interview_context,
    clear_session,
    load_session,
    save_session,
)
from .core.tools import build_tools, skill_tool_name
from .core.validators import ExtractionStatus, get_validator
from .runtime.hooks import call_hook, clear_module_cache, load_hook_function
from .runtime.path_resolver import (
    build_next_questions,
    compute_reachable_question_names,
    compute_reachable_required,
    missing_required_reachable,
    prune_unreachable_fields,
    resolve_next_question_name,
    resolve_store_continuation,
)
from .runtime.pipeline import apply_store_pipeline, run_pre_tools, validate_field

logger = logging.getLogger(__name__)

_TASK_OWNER_ACTION = "InterviewAction"
_TASK_TYPE = "INTERVIEW"

_ACTIVE_TASK_DESCRIPTION_TEMPLATE = (
    "The user has engaged the {action_title} (Action Description: {action_description}). "
    "If their latest message is off-topic or unrelated to it, answer that in at most one "
    "short sentence, then steer back and continue the interview — always "
    "ending your reply with the current pending question. Do not abandon the {action_title} until it is "
    "complete or the user explicitly cancels."
)


class InterviewAction(Action):
    """Provides interview tools for LLM-driven multi-turn flows."""

    description: str = (
        "Skills V2 interview action that provides granular tools for conducting "
        "interviews. The LLM decides which tools to call at each step based on "
        "the interview spec and SKILL.md procedure."
    )
    binds_tools_to_visitor: bool = True

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._registry = InterviewRegistry()

    async def on_register(self):
        await super().on_register()
        await self._discover_specs()

    async def on_reload(self):
        await super().on_reload()
        clear_module_cache()
        skills_dirs = await self._resolve_skills_dirs()
        if skills_dirs:
            self._registry.reload(skills_dirs)

    async def on_startup(self):
        await super().on_startup()
        if not self._registry.specs:
            await self._discover_specs()

    async def _discover_specs(self) -> None:
        skills_dirs = await self._resolve_skills_dirs()
        logger.info("InterviewAction discovering specs from: %s", skills_dirs)
        if skills_dirs:
            specs = self._registry.discover(skills_dirs)
            logger.info(
                "InterviewAction discovered %s interview specs: %s",
                len(specs),
                list(specs.keys()),
            )
        else:
            logger.warning("InterviewAction: no agent skills directory found.")

    async def _ensure_specs_loaded(self) -> None:
        skills_dirs = await self._resolve_skills_dirs()
        if skills_dirs:
            self._registry.reload(skills_dirs)
            return
        if not self._registry.specs:
            await self._discover_specs()

    async def _ensure_contracts_loaded(self) -> None:
        """Back-compat alias for skill_tasks.ensure_locked_skill_session."""
        await self._ensure_specs_loaded()

    async def _resolve_skills_dirs(self) -> List[str]:
        return await self.resolve_skill_scan_dirs()

    async def get_tools(self) -> List[Any]:
        await self._ensure_specs_loaded()
        return build_tools(self)

    def _load_fn(self, spec: InterviewSpec) -> Callable[[str], Optional[Callable]]:
        return lambda name: load_hook_function(spec, name)

    async def _interview_ready(self, visitor: Any = None) -> bool:
        await self._ensure_specs_loaded()
        session, spec = await self._get_session_and_contract(visitor)
        return session is not None and spec is not None and session.is_active()

    async def skill_runtime_ready(self, skill_name: str, visitor: Any = None) -> bool:
        if not self.is_interview_skill(skill_name):
            return False
        return await self._interview_ready(visitor)

    async def prepare_locked_skill_turn(
        self, skill_name: str, visitor: Any = None
    ) -> Any:
        from jvagent.action.interview_action.core.responses import (
            tool_observation_failed,
        )
        from jvagent.action.orchestrator.skill_tasks import LockedSkillPrep

        if not await self.skill_runtime_ready(skill_name, visitor):
            return LockedSkillPrep(
                runtime_ready=False,
                pending_directive=(
                    "Interview session is not open yet — reply to the user only; "
                    "do not call interview tools this turn."
                ),
            )

        should_seed, pending_field = await self._should_seed_next_question(
            skill_name, visitor
        )
        if not should_seed:
            field_hint = (
                f"interview__set_field(field='{pending_field}', ...)"
                if pending_field
                else "interview__set_field(field=<missing field>, ...)"
            )
            quality_gate = (
                "First apply the Answer quality gate: only call set_field when the "
                "user's latest message substantively answers the active question "
                f"({pending_field or 'see next_questions'}). If it is an acknowledgement, "
                "filler, or off-topic reply, respond only and re-ask — do not call tools."
            )
            return LockedSkillPrep(
                runtime_ready=True,
                pending_directive=(
                    f"{quality_gate} When the answer is substantive, call {field_hint} — "
                    "validation runs inside set_field. Do NOT call interview__next_question "
                    "before set_field returns ok:true."
                ),
            )

        try:
            next_obs = await self._handle_next_question(visitor)
        except Exception as exc:
            logger.warning(
                "InterviewAction.prepare_locked_skill_turn failed for %s: %s",
                skill_name,
                exc,
            )
            return LockedSkillPrep(
                runtime_ready=False,
                pending_directive=(
                    "Interview session could not be prepared — reply to the user only."
                ),
            )
        if tool_observation_failed(next_obs, error_code="NO_SESSION"):
            return LockedSkillPrep(
                runtime_ready=False,
                pending_directive=(
                    "Interview session is not open yet — reply to the user only; "
                    "do not call interview tools this turn."
                ),
            )
        return LockedSkillPrep(
            runtime_ready=True,
            observations=[
                {
                    "tool": "interview__next_question",
                    "args": {},
                    "observation": next_obs,
                }
            ],
            pending_directive=(
                "The first question is in the interview__next_question observation "
                "above — reply to the user using response_directive. "
                "Do NOT call interview__next_question again this turn."
            ),
        )

    async def _should_seed_next_question(
        self, skill_name: str, visitor: Any = None
    ) -> tuple[bool, Optional[str]]:
        """Return (seed_next_question, pending_field_for_set_field).

        Seed next_question until that field's question has been presented via
        interview__next_question. After presentation, a user utterance is routed to
        set_field (validators run there).
        """
        from jvagent.action.orchestrator.skill_tasks import visitor_utterance

        session, spec = await self._get_session_and_contract(visitor)
        if not session or not spec or session.interview_type != skill_name:
            return True, None
        if not session.is_active():
            return True, None

        if session.status == InterviewStatus.REVIEW:
            return False, None

        load_fn = self._load_fn(spec)
        pending_field = await resolve_next_question_name(
            session, spec, load_fn, visitor, self
        )

        utterance = visitor_utterance(visitor).strip()
        if not utterance:
            return True, pending_field

        if pending_field is None:
            return False, None

        if not session.get_value(pending_field) and not session.is_skipped(
            pending_field
        ):
            ctx = session.context if isinstance(session.context, dict) else {}
            if ctx.get(CTX_QUESTION_PRESENTED) != pending_field:
                return True, pending_field
            return False, pending_field

        return False, None

    async def prune_turn_tools(
        self, tools: Dict[str, Any], visible: set, visitor: Any = None
    ) -> None:
        if await self._interview_ready(visitor):
            return
        drop: set = {n for n in tools if n.startswith("interview__")}
        for spec_name in self._registry.list_specs():
            prefix = f"{spec_name}__"
            drop.update(n for n in tools if n.startswith(prefix))
        for name in drop:
            tools.pop(name, None)
        visible -= drop

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

    async def _get_session_and_spec(
        self, visitor: Any = None
    ) -> Tuple[Optional[InterviewSession], Optional[InterviewSpec]]:
        return await self._get_session_and_contract(visitor)

    @staticmethod
    def _task_interview_type(handle: Any) -> Optional[str]:
        task_data = getattr(handle, "data", None) or {}
        if isinstance(task_data, dict):
            raw = task_data.get("interview_type")
            return str(raw) if raw else None
        return None

    @staticmethod
    def _find_existing_active_task(
        visitor: Any, spec_name: Optional[str] = None
    ) -> Optional[Any]:
        try:
            store = visitor.tasks
        except Exception:
            return None
        if spec_name:
            try:
                skill_tasks = store.list(status="active", owner_action=spec_name)
                if skill_tasks:
                    return skill_tasks[0]
            except Exception:
                pass
            try:
                for handle in (
                    store.list(status="active", owner_action=_TASK_OWNER_ACTION) or []
                ):
                    if InterviewAction._task_interview_type(handle) == spec_name:
                        return handle
            except Exception:
                pass
            return None
        try:
            existing = store.list(status="active", owner_action=_TASK_OWNER_ACTION)
            return existing[0] if existing else None
        except Exception:
            return None

    async def _close_mismatched_interview_tasks(
        self, visitor: Any, spec_name: str, status: str = "cancelled"
    ) -> None:
        try:
            store = visitor.tasks
            handles = store.list(status="active", owner_action=_TASK_OWNER_ACTION) or []
        except Exception:
            return
        for handle in handles:
            if self._task_interview_type(handle) == spec_name:
                continue
            try:
                if status == "completed":
                    await handle.complete()
                elif status == "cancelled":
                    await handle.cancel()
                elif status == "failed":
                    await handle.fail()
                try:
                    await store.delete(handle.id)
                except Exception:
                    pass
            except Exception as exc:
                logger.debug("_close_mismatched_interview_tasks: %s", exc)

    async def _ensure_active_task(self, visitor: Any, spec: InterviewSpec) -> None:
        if self._find_existing_active_task(visitor, spec.name) is not None:
            return
        await self._close_mismatched_interview_tasks(visitor, spec.name)
        title = spec.title or spec.name.replace("_", " ").title()
        description = _ACTIVE_TASK_DESCRIPTION_TEMPLATE.format(
            action_title=title,
            action_description=spec.description or self.description or "",
        )
        try:
            handle = await visitor.tasks.create(
                title=title,
                description=description,
                owner_action=_TASK_OWNER_ACTION,
                task_type=_TASK_TYPE,
                data={"interview_type": spec.name, "state": "active"},
            )
            await handle.start()
        except Exception as exc:
            logger.debug("_ensure_active_task: %s", exc)

    async def _close_task(
        self,
        visitor: Any,
        status: str = "completed",
        spec_name: Optional[str] = None,
        contract_name: Optional[str] = None,
    ) -> None:
        spec_name = spec_name or contract_name
        try:
            store = visitor.tasks
            interview_handles = store.list(
                status="active", owner_action=_TASK_OWNER_ACTION
            )
        except Exception:
            return
        for handle in interview_handles or []:
            if spec_name and self._task_interview_type(handle) != spec_name:
                continue
            try:
                if status == "completed":
                    await handle.complete()
                elif status == "cancelled":
                    await handle.cancel()
                elif status == "failed":
                    await handle.fail()
                try:
                    await store.delete(handle.id)
                except Exception:
                    pass
            except Exception as exc:
                logger.debug("_close_task: %s", exc)
        if spec_name:
            try:
                for handle in store.list(status="active", owner_action=spec_name) or []:
                    try:
                        if status == "completed":
                            await handle.complete()
                        elif status == "cancelled":
                            await handle.cancel()
                        elif status == "failed":
                            await handle.fail()
                    except Exception:
                        pass
            except Exception:
                pass

    def is_interview_skill(self, skill_name: str) -> bool:
        return bool(self._registry.get(skill_name))

    async def needs_session_rebootstrap(
        self, skill_name: str, visitor: Any = None
    ) -> bool:
        await self._ensure_specs_loaded()
        if not self.is_interview_skill(skill_name):
            return False
        conversation = await self._get_conversation(visitor)
        if conversation is None:
            return True
        session = load_session(conversation)
        if session is not None and session.is_active():
            return session.interview_type != skill_name
        return True

    async def on_skill_activate(
        self,
        skill_name: str,
        visitor: Any = None,
        *,
        user_message: str = "",
    ) -> Optional[str]:
        await self._ensure_specs_loaded()
        if not self.is_interview_skill(skill_name):
            available = self._registry.list_specs()
            return (
                f"Interview skill '{skill_name}' has no interview spec on this agent "
                f"(SKILL.md frontmatter '{INTERVIEW_FRONTMATTER_KEY}:' or deprecated "
                f"{INTERVIEW_YAML}). "
                f"Available interview types: {available or '(none)'}. "
                "Do not call interview tools until the session is active."
            )
        raw = await self._handle_start(
            skill_name, visitor, user_message=(user_message or "").strip()
        )
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else {}
        except (json.JSONDecodeError, TypeError):
            return f"Interview session ready ({skill_name}). Call interview__next_question next."
        if not isinstance(parsed, dict):
            return f"Interview session ready ({skill_name}). Call interview__next_question next."
        if parsed.get("status") == "error" or parsed.get("ok") is False:
            return (
                parsed.get("response_directive")
                or parsed.get("error")
                or f"Could not start interview session for {skill_name}."
            )
        interview_type = parsed.get("interview_type", skill_name)
        parts = [
            f"Interview session ready ({interview_type}).",
            f"fields={parsed.get('fields', {})}",
            f"missing_required={parsed.get('missing_required', [])}",
        ]
        if parsed.get("post_tools_results"):
            parts.append(f"post_tools_results={parsed['post_tools_results']}")
        if parsed.get("skip_to_review"):
            parts.append("skip_to_review=true. Call interview__review next.")
        elif parsed.get("fresh_session"):
            parts.append(
                "Turn prep seeds the first question via interview__next_question — "
                "reply to the user using that observation's response_directive. "
                "Do NOT call interview__next_question again until after set_field "
                "returns ok:true."
            )
        else:
            parts.append(
                "Call interview__set_field for the user's answer, then "
                "interview__next_question once on ok:true."
            )
        return " ".join(parts)

    async def resolve_locked_skill(
        self, visitor: Any, skill_docs: List[Any]
    ) -> Optional[Any]:
        skill_by_name = {d.name: d for d in skill_docs if getattr(d, "name", None)}
        conversation = await self._get_conversation(visitor)
        if conversation is not None:
            session = load_session(conversation)
            if session is not None and session.is_active():
                sd = skill_by_name.get(session.interview_type)
                if sd is not None and getattr(sd, "locked_in", False):
                    return sd
        store = getattr(visitor, "tasks", None) if visitor else None
        if store is None and conversation is not None:
            try:
                from jvagent.memory.task_store import TaskStore

                store = TaskStore(conversation)
            except Exception:
                store = None
        if store is None:
            return None
        candidates: List[tuple[str, Any]] = []
        try:
            for task in store.list(status="active") or []:
                owner = getattr(task, "owner_action", None)
                sd = skill_by_name.get(owner) if owner else None
                if sd is None and owner == _TASK_OWNER_ACTION:
                    it = self._task_interview_type(task)
                    sd = skill_by_name.get(it) if it else None
                if sd is not None and getattr(sd, "locked_in", False):
                    candidates.append((str(getattr(task, "updated_at", "") or ""), sd))
        except Exception:
            return None
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    # ─── Tool handlers ───────────────────────────────────────────────

    async def _handle_start(
        self,
        interview_type: str,
        visitor: Any = None,
        force_fresh: bool = False,
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

        raw_fresh = kwargs.get("force_fresh", force_fresh)
        force_fresh = (
            raw_fresh.strip().lower() in ("true", "1", "yes")
            if isinstance(raw_fresh, str)
            else bool(raw_fresh)
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

        user_message = (kwargs.get("user_message") or "").strip()
        seeded_fields: List[str] = []
        skip_to_review = False
        post_tools_results: List[Dict[str, Any]] = []
        if user_message:
            seed_result = await self._seed_fields_from_user_message(
                session, spec, user_message, visitor
            )
            seeded_fields = seed_result.get("seeded", [])
            skip_to_review = bool(seed_result.get("skip_to_review"))
            post_tools_results = seed_result.get("post_tools_results") or []

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
            seeded_fields=seeded_fields or None,
            post_tools_results=post_tools_results or None,
            skip_to_review=True if skip_to_review else None,
            next_tool="interview__review" if skip_to_review else None,
            response_directive=(
                call_tool_directive("interview__review") if skip_to_review else None
            ),
        )

    async def _handle_set_field(
        self,
        field: str = "",
        value: str = "",
        visitor: Any = None,
        name: str = "",
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

        resolved_field, field_err = self._resolve_field_param(
            field, name, spec, **kwargs
        )
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
        return json.dumps(payload)

    async def _handle_next_question(self, visitor: Any = None) -> str:
        session, spec = await self._get_session_and_contract(visitor)
        if not session or not spec:
            return interview_step_response(
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
            return interview_step_response(
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
            return interview_step_response(
                ok=False,
                status="error",
                error="No question definition found for the next step.",
            )

        directive, extras = await run_pre_tools(self, session, spec, q_def, visitor)
        pre_tools_results = extras.get("pre_tools_results") or []
        if any(not r.get("ok", True) for r in pre_tools_results):
            return interview_step_response(
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

        return interview_step_response(
            ok=True,
            status=session.status.value,
            fields=session.get_collected_summary(),
            skipped_fields=sorted(session.skipped_fields),
            missing_required=missing,
            next_questions=next_qs,
            pre_tools_results=pre_tools_results,
            response_directive=directive,
        )

    async def _handle_get_field(self, field: str, visitor: Any = None) -> str:
        session, spec = await self._get_session_and_contract(visitor)
        if not session:
            return interview_tool_response(
                ok=False,
                status="error",
                error_code="NO_SESSION",
                response_directive="No active interview session.",
            )
        result: Dict[str, Any] = {
            "ok": True,
            "status": session.status.value,
            "field": field,
            "value": session.get_value(field),
            "is_set": session.has_field(field),
            "is_skipped": session.is_skipped(field),
            "fields": session.get_collected_summary(),
            "skipped_fields": sorted(session.skipped_fields),
        }
        if spec:
            required = await compute_reachable_required(
                session, spec, self._load_fn(spec), visitor, self
            )
            result["missing_required"] = missing_required_reachable(session, required)
            result["next_questions"] = await build_next_questions(
                session, spec, self._load_fn(spec), visitor, self
            )
        return json.dumps(result)

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

        if next_tool == "interview__review":
            from .runtime.pipeline import merge_auto_review

            payload = {
                "ok": True,
                "status": session.status.value,
                "field": field,
                "value": None,
                "fields": session.get_collected_summary(),
                "skipped_fields": sorted(session.skipped_fields),
                "missing_required": missing,
            }
            return json.dumps(await merge_auto_review(self, visitor, payload))

        return interview_tool_response(
            ok=True,
            status=session.status.value,
            field=field,
            value=None,
            fields=session.get_collected_summary(),
            skipped_fields=sorted(session.skipped_fields),
            missing_required=missing,
            response_directive=_directive,
            next_tool=next_tool,
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

    async def _default_review(
        self, session: InterviewSession, spec: InterviewSpec, visitor: Any
    ) -> str:
        collected = session.get_collected_summary()
        review_lines = []
        for q in spec.questions:
            if session.is_skipped(q.name):
                continue
            if q.name in collected:
                label = q.name.replace("_", " ").title()
                review_lines.append(f"**{label}**: {collected[q.name]}")
            elif q.required:
                label = q.name.replace("_", " ").title()
                review_lines.append(f"**{label}**: *(not provided)*")
        summary = "\n\n".join(review_lines)
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

        review_lines = []
        for q in spec.questions:
            if q.name in omit_fields or session.is_skipped(q.name):
                continue
            if q.name in collected:
                label = q.name.replace("_", " ").title()
                review_lines.append(f"**{label}**: {collected[q.name]}")
            elif q.required:
                label = q.name.replace("_", " ").title()
                review_lines.append(f"**{label}**: *(not provided)*")
        for label, value in additional_data.items():
            review_lines.append(f"**{label}**: {value}")
        summary = "\n\n".join(review_lines)

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

    async def _handle_decorated_function(
        self, func: Callable, spec: InterviewSpec, **kwargs
    ) -> str:
        try:
            visitor = kwargs.pop("visitor", None) or get_dispatch_visitor()
            session = await self._get_session(visitor)
            result = await call_hook(
                func,
                session=session,
                spec=spec,
                visitor=visitor,
                interview_action=self,
                kwargs=kwargs,
            )
            if isinstance(result, dict):
                return await self._finalize_tool_response(result, session, visitor)
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

    async def _seed_fields_from_user_message(
        self,
        session: InterviewSession,
        spec: InterviewSpec,
        user_message: str,
        visitor: Any,
    ) -> Dict[str, Any]:
        msg = (user_message or "").strip()
        if not msg:
            return {"seeded": [], "skip_to_review": False}

        seeded: List[str] = []
        skip_to_review = False
        post_tools_results: List[Dict[str, Any]] = []
        load_fn = self._load_fn(spec)

        for q in spec.questions:
            if session.get_value(q.name) or session.is_skipped(q.name):
                continue
            if not q.required or not q.validator:
                break
            vdef = resolve_validator_def(q, spec)
            if not vdef:
                break
            kwargs = resolve_validator_kwargs(q, vdef)
            candidates = extract_candidates_for_question(q, vdef, msg, kwargs)
            if not candidates:
                break
            stored_value = None
            for candidate in candidates:
                check = await validate_field(
                    self, spec, q.name, candidate, session, visitor
                )
                if check.get("valid"):
                    stored_value = check.get("value", candidate)
                    break
            if not stored_value:
                break
            session.set_value(q.name, stored_value)
            seeded.append(q.name)
            reachable = await compute_reachable_question_names(
                session, spec, load_fn, visitor, self
            )
            prune_unreachable_fields(session, reachable)
            if q.post_tools:
                from .runtime.pipeline import run_post_tools

                payload: Dict[str, Any] = {
                    "status": session.status.value,
                    "field": q.name,
                    "value": stored_value,
                    "fields": session.get_collected_summary(),
                }
                merged = await run_post_tools(
                    self, q, session, spec, visitor, stored_value, payload
                )
                skip_to_review = bool(merged.get("skip_to_review"))
                post_tools_results.extend(merged.get("post_tools_results") or [])

        if seeded:
            await self._save_session(session, visitor)

        return {
            "seeded": seeded,
            "skip_to_review": skip_to_review,
            "post_tools_results": post_tools_results,
        }

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

    def _resolve_field_param(
        self, field: str, name: str, spec: InterviewSpec, **kwargs: Any
    ) -> tuple[str, Optional[str]]:
        raw_field = (field or "").strip()
        raw_name = (name or kwargs.get("name") or "").strip()
        if raw_field and raw_name and raw_field != raw_name:
            return "", "field and name disagree — use field only"
        resolved = raw_field or raw_name
        if not resolved:
            return "", "Missing field — use parameter field (not name)"
        valid = {q.name for q in spec.questions}
        if resolved not in valid:
            return "", f"Unknown field '{resolved}'. Valid: {sorted(valid)}"
        return resolved, None


def _parse_validation_result(
    result: Any, original_value: str, validator_name: str
) -> Dict[str, Any]:
    if isinstance(result, dict):
        if result.get("valid") is True:
            out: Dict[str, Any] = {
                "valid": True,
                "value": result.get("value", original_value),
                "validator": validator_name,
            }
            for key in ("interview_complete", "response_directive"):
                if key in result:
                    out[key] = result[key]
            return out
        out = {
            "valid": False,
            "error": result.get("error", f"Validation failed for {validator_name}"),
            "value": original_value,
            "validator": validator_name,
        }
        if "response_directive" in result:
            out["response_directive"] = result["response_directive"]
        return out
    if isinstance(result, tuple) and len(result) == 3:
        status, error_msg, autocorrected = result
        if status == ExtractionStatus.EXTRACTED:
            return {
                "valid": True,
                "value": autocorrected or original_value,
                "validator": validator_name,
            }
        return {
            "valid": False,
            "error": error_msg or f"Validation failed for {validator_name}",
            "validator": validator_name,
        }
    if isinstance(result, str):
        try:
            as_json = json.loads(result)
            if isinstance(as_json, dict) and "valid" in as_json:
                return _parse_validation_result(as_json, original_value, validator_name)
        except (json.JSONDecodeError, TypeError):
            return {"valid": True, "value": result, "validator": validator_name}
    return {
        "valid": False,
        "error": f"Unexpected validation result type: {type(result)}",
        "validator": validator_name,
    }


def _question_def_to_dict(q: QuestionDef) -> Dict[str, Any]:
    result = {
        "name": q.name,
        "question": q.question,
        "description": q.description,
        "required": q.required,
        "validator": q.validator,
    }
    if q.validator_kwargs:
        result["validator_kwargs"] = q.validator_kwargs
    if q.input_handler:
        result["input_handler"] = q.input_handler
    if q.input_context_provider:
        result["input_context_provider"] = q.input_context_provider
    if q.pre_tools:
        result["pre_tools"] = q.pre_tools
    if q.post_tools:
        result["post_tools"] = q.post_tools
    if q.branches:
        result["branches"] = [
            {"condition": b.condition, "target": b.target} for b in q.branches
        ]
    if q.default_next:
        result["default_next"] = q.default_next
    return result


def _validator_def_to_dict(v: ValidatorDef) -> Dict[str, Any]:
    result: Dict[str, Any] = {"function": v.name, "description": v.description}
    if v.kwargs:
        result["kwargs"] = v.kwargs
    return result
