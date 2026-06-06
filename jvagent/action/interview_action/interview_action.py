"""InterviewAction — LLM-driven interview action.

Unlike SkillInterviewAction (v1), which uses a state machine and LLM-based
intent classification to drive the interview flow, this action gives the
LLM full control.  The action exposes granular tools (one per validator,
per custom operation) and the LLM decides what to ask, validate, and call
at each step.

The action is a pure Action (not an InteractAction) that provides tools.
The orchestrator agent reads the SKILL.md procedure and calls tools as needed.

Session state is persisted in conversation.context["interview"].
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import logging
import os
from copy import deepcopy
from typing import Any, Callable, Dict, List, Optional, Tuple

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_executor import get_dispatch_visitor

from .contract_loader import (
    CompletionDef,
    ContractRegistry,
    InterviewContract,
    QuestionDef,
    ToolDef,
    ValidatorDef,
    resolve_validator_def,
    resolve_validator_kwargs,
)
from .field_extractors import extract_candidates_for_question
from .responses import (
    call_tool_directive,
    directive_for_missing_fields,
    interview_step_response,
    interview_tool_response,
    no_session_directive,
    restart_session_directive,
    slim_post_tool_entry,
    tell_user_directive,
)
from .session import (
    SESSION_KEY,
    InterviewSession,
    InterviewStatus,
    clear_session,
    has_active_session,
    load_session,
    save_session,
)
from .tools import build_tools, skill_tool_name
from .validators import ExtractionStatus

logger = logging.getLogger(__name__)

_TASK_OWNER_ACTION = "InterviewAction"
_TASK_TYPE_INTERVIEW = "INTERVIEW"

_ACTIVE_TASK_DESCRIPTION_TEMPLATE = (
    "The user has engaged the {action_title} (Action Description: {action_description}). "
    "If their latest message is off-topic or unrelated to it, answer that in at most one "
    "short sentence, then steer back and continue the interview — always "
    "ending your reply with the current pending question. Do not abandon the {action_title} until it is "
    "complete or the user explicitly cancels."
)


class InterviewAction(Action):
    """Provides granular interview tools for LLM-driven interview flows."""

    description: str = (
        "Skills V2 interview action that provides granular tools for conducting "
        "interviews. The LLM decides which tools to call at each step based on "
        "the interview contract and SKILL.md procedure."
    )
    binds_tools_to_visitor: bool = True

    model_action_type: str = attribute(
        default="OpenAILanguageModelAction",
        description="Language model action type for classification/extraction",
    )

    _contract_registry: ContractRegistry

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._contract_registry = ContractRegistry()

    async def on_register(self):
        await super().on_register()
        await self._discover_contracts()

    async def on_reload(self):
        await super().on_reload()
        skills_dirs = await self._resolve_skills_dirs()
        if skills_dirs:
            self._contract_registry.reload(skills_dirs)

    async def on_startup(self):
        await super().on_startup()
        if not self._contract_registry._contracts:
            await self._discover_contracts()

    async def _discover_contracts(self) -> None:
        skills_dirs = await self._resolve_skills_dirs()
        logger.info("InterviewAction discovering contracts from: %s", skills_dirs)
        if skills_dirs:
            count = self._contract_registry.discover(skills_dirs)
            logger.info(
                "InterviewAction discovered %s interview contracts: %s",
                len(count),
                list(count.keys()),
            )
        else:
            logger.warning(
                "InterviewAction: no agent skills directory found. Contracts will be empty."
            )

    async def _ensure_contracts_loaded(self) -> None:
        """Lazy (re)load contracts before skill activation or session bootstrap."""
        skills_dirs = await self._resolve_skills_dirs()
        if skills_dirs:
            # Always sync from the agent skills tree when we can resolve it.
            # on_register may run before agent_dir exists, leaving a stale or
            # empty registry that short-circuited reload (turn-1 NO_SESSION loops).
            self._contract_registry.reload(skills_dirs)
            return
        if not self._contract_registry._contracts:
            await self._discover_contracts()

    async def _resolve_skills_dirs(self) -> List[str]:
        """Resolve ``agents/<ns>/<agent>/skills`` for the hosting agent.

        Must match orchestrator skill discovery — not the built-in
        ``jvagent/skills`` library (which has no ``contract.yaml`` files).
        """
        meta = self.metadata or {}

        agent_dir = meta.get("agent_dir")
        if agent_dir:
            skills_dir = os.path.join(str(agent_dir), "skills")
            if os.path.isdir(skills_dir):
                return [skills_dir]

        app_root = None
        try:
            from jvagent.core.app_context import get_app_root

            app_root = get_app_root()
        except Exception:
            app_root = None

        agent_ns = meta.get("agent_namespace")
        agent_name = meta.get("agent_name")
        if not agent_ns or not agent_name:
            try:
                agent = await self.get_agent()
            except Exception:
                agent = None
            if agent is not None:
                agent_ns = agent_ns or getattr(agent, "namespace", None)
                agent_name = agent_name or getattr(agent, "name", None)

        if app_root and agent_ns and agent_name:
            skills_dir = os.path.join(
                str(app_root), "agents", str(agent_ns), str(agent_name), "skills"
            )
            if os.path.isdir(skills_dir):
                return [skills_dir]

        return []

    async def get_tools(self) -> List[Any]:
        await self._ensure_contracts_loaded()
        return build_tools(self)

    async def _interview_ready(self, visitor: Any = None) -> bool:
        """True when an active session exists and its contract is loaded."""
        await self._ensure_contracts_loaded()
        session, contract = await self._get_session_and_contract(visitor)
        return session is not None and contract is not None and session.is_active()

    async def skill_runtime_ready(self, skill_name: str, visitor: Any = None) -> bool:
        """Bound-action hook: skill runtime is ready for turn-lock."""
        if not self.is_interview_skill(skill_name):
            return False
        return await self._interview_ready(visitor)

    async def prepare_locked_skill_turn(
        self, skill_name: str, visitor: Any = None
    ) -> Any:
        """Bound-action hook: seed turn observations when runtime is ready."""
        from jvagent.action.interview_action.responses import tool_observation_failed
        from jvagent.action.orchestrator.skill_tasks import LockedSkillPrep

        if not await self.skill_runtime_ready(skill_name, visitor):
            return LockedSkillPrep(
                runtime_ready=False,
                pending_directive=(
                    "Interview session is not open yet — reply to the user only; "
                    "do not call interview tools this turn."
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
        )

    async def prune_turn_tools(
        self,
        tools: Dict[str, Any],
        visible: set,
        visitor: Any = None,
    ) -> None:
        """Drop interview tools from the surface when no session is open."""
        if await self._interview_ready(visitor):
            return
        drop: set = {n for n in tools if n.startswith("interview__")}
        for contract_name in self._contract_registry.list_contracts():
            prefix = f"{contract_name}__"
            drop.update(n for n in tools if n.startswith(prefix))
        for name in drop:
            tools.pop(name, None)
        visible -= drop

    # ─── Conversations / Sessions ─────────────────────────────────────

    async def _get_conversation(self, visitor: Any = None):
        if visitor is None:
            visitor = get_dispatch_visitor()
        if visitor is None:
            return None
        conversation = None
        if hasattr(visitor, "conversation") and visitor.conversation is not None:
            conversation = visitor.conversation
        elif hasattr(visitor, "interaction") and visitor.interaction is not None:
            interaction = visitor.interaction
            if hasattr(interaction, "get_conversation"):
                conversation = await interaction.get_conversation()
        return conversation

    async def _get_session(self, visitor: Any = None) -> Optional[InterviewSession]:
        conversation = await self._get_conversation(visitor)
        if not conversation:
            return None
        return load_session(conversation)

    async def _save_session(self, session: InterviewSession, visitor: Any = None):
        conversation = await self._get_conversation(visitor)
        if conversation:
            await save_session(conversation, session)

    async def _clear_interview_session(self, visitor: Any = None) -> None:
        """Remove persisted interview state from the conversation."""
        conversation = await self._get_conversation(visitor)
        if not conversation:
            return
        clear_session(conversation)
        try:
            await conversation.save()
        except Exception:
            pass

    async def persist_interview_fields(
        self,
        session: InterviewSession,
        visitor: Any,
        fields: Dict[str, str],
        *,
        validate: bool = True,
    ) -> Dict[str, Any]:
        """Validate and store multiple interview fields, then save session once."""
        contract = self._contract_registry.get(session.interview_type)
        if not contract:
            return {
                "stored": [],
                "stored_values": {},
                "validation_errors": {
                    "_session": "No contract found for interview type"
                },
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
                check = await self._validate_field_for_persist(
                    contract, name, value, visitor
                )
                if not check.get("valid"):
                    validation_errors[name] = check.get("error", "Validation failed")
                    continue
                value = check.get("value", value)

            session.set_value(name, value)
            stored.append(name)
            stored_values[name] = value

        if stored:
            if session.status == InterviewStatus.REVIEW:
                if session.missing_required(contract.get_required_fields()):
                    session.status = InterviewStatus.ACTIVE
            await self._save_session(session, visitor)

        return {
            "stored": stored,
            "stored_values": stored_values,
            "validation_errors": validation_errors,
        }

    async def _validate_field_for_persist(
        self,
        contract: InterviewContract,
        field_name: str,
        value: str,
        visitor: Any,
        session: Optional[InterviewSession] = None,
    ) -> Dict[str, Any]:
        """Run contract validator (and alternate) for a field before persisting."""
        q = contract.get_question(field_name)
        if not q or not q.validator:
            return {"valid": True, "value": value.strip()}

        vdef = resolve_validator_def(q, contract)
        if not vdef:
            return {"valid": True, "value": value.strip()}

        kwargs = resolve_validator_kwargs(q, vdef)
        raw = await self._handle_validate(
            vdef, value, kwargs, visitor=visitor, session=session
        )
        parsed = json.loads(raw)
        if parsed.get("valid"):
            result: Dict[str, Any] = {
                "valid": True,
                "value": parsed.get("value", value),
            }
            for key in ("interview_complete", "response_directive"):
                if key in parsed:
                    result[key] = parsed[key]
            return result

        result = {
            "valid": False,
            "error": parsed.get("error", f"Validation failed for {field_name}"),
        }
        if "response_directive" in parsed:
            result["response_directive"] = parsed["response_directive"]
        return result

    async def _seed_fields_from_user_message(
        self,
        session: InterviewSession,
        contract: InterviewContract,
        user_message: str,
        visitor: Any,
    ) -> Dict[str, Any]:
        """Try to extract and store required fields from the opening user message."""
        msg = (user_message or "").strip()
        if not msg:
            return {"seeded": [], "skip_to_review": False}

        seeded: List[str] = []
        skip_to_review = False
        post_tools_results: List[Dict[str, Any]] = []

        for q in contract.questions:
            if session.get_value(q.name) or session.is_skipped(q.name):
                continue
            if not q.required:
                continue
            if not q.validator:
                break

            vdef = resolve_validator_def(q, contract)
            if not vdef:
                break

            kwargs = resolve_validator_kwargs(q, vdef)
            candidates = extract_candidates_for_question(q, vdef, msg, kwargs)
            if not candidates:
                break

            stored_value = None
            for candidate in candidates:
                check = await self._validate_field_for_persist(
                    contract, q.name, candidate, visitor
                )
                if check.get("valid"):
                    stored_value = check.get("value", candidate)
                    break

            if not stored_value:
                break

            await self.persist_interview_fields(
                session, visitor, {q.name: stored_value}, validate=False
            )
            seeded.append(q.name)

            if q.post_tools:
                payload: Dict[str, Any] = {
                    "status": session.status.value,
                    "field": q.name,
                    "value": stored_value,
                    "fields": session.get_collected_summary(),
                }
                merged = await self._merge_post_tools(
                    payload, q, session, contract, visitor, stored_value
                )
                skip_to_review = bool(merged.get("skip_to_review"))
                for entry in merged.get("post_tools_results") or []:
                    post_tools_results.append(entry)

        return {
            "seeded": seeded,
            "skip_to_review": skip_to_review,
            "post_tools_results": post_tools_results,
        }

    async def _apply_persist_fields_from_response(
        self,
        parsed: Dict[str, Any],
        session: Optional[InterviewSession],
        visitor: Any,
    ) -> None:
        """Safety net: tools may return persist_fields to store without calling persist."""
        persist_fields = parsed.get("persist_fields")
        if not persist_fields or not session or not isinstance(persist_fields, dict):
            return
        await self.persist_interview_fields(
            session, visitor, persist_fields, validate=True
        )

    async def _finalize_tool_response(
        self,
        parsed: Dict[str, Any],
        session: Optional[InterviewSession],
        visitor: Any,
    ) -> str:
        await self._apply_persist_fields_from_response(parsed, session, visitor)
        enriched = await self._enrich_with_state(parsed, visitor)
        return json.dumps(enriched)

    # ─── Task Tracking ─────────────────────────────────────────────

    @staticmethod
    def _task_interview_type(handle: Any) -> Optional[str]:
        task_data = getattr(handle, "data", None) or {}
        if isinstance(task_data, dict):
            raw = task_data.get("interview_type")
            return str(raw) if raw else None
        return None

    @staticmethod
    def _find_existing_active_task(
        visitor: Any, contract_name: Optional[str] = None
    ) -> Optional[Any]:
        try:
            store = visitor.tasks
        except Exception:
            return None
        if contract_name:
            try:
                skill_tasks = store.list(status="active", owner_action=contract_name)
                if skill_tasks:
                    return skill_tasks[0]
            except Exception:
                pass
            try:
                for handle in (
                    store.list(status="active", owner_action=_TASK_OWNER_ACTION) or []
                ):
                    if InterviewAction._task_interview_type(handle) == contract_name:
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
        self, visitor: Any, contract_name: str, status: str = "cancelled"
    ) -> None:
        try:
            store = visitor.tasks
        except Exception:
            return
        try:
            handles = store.list(status="active", owner_action=_TASK_OWNER_ACTION) or []
        except Exception:
            return
        for handle in handles:
            if self._task_interview_type(handle) == contract_name:
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
                logger.debug(
                    "_close_mismatched_interview_tasks: failed for %s: %s",
                    getattr(handle, "id", "?"),
                    exc,
                )

    async def _ensure_active_task(
        self, visitor: Any, contract: InterviewContract
    ) -> None:
        existing = self._find_existing_active_task(visitor, contract.name)
        if existing is not None:
            return
        await self._close_mismatched_interview_tasks(
            visitor, contract.name, status="cancelled"
        )
        title = contract.title or contract.name.replace("_", " ").title()
        action_description = contract.description or self.description or ""
        description = _ACTIVE_TASK_DESCRIPTION_TEMPLATE.format(
            action_title=title,
            action_description=action_description,
        )
        try:
            handle = await visitor.tasks.create(
                title=title,
                description=description,
                owner_action=_TASK_OWNER_ACTION,
                task_type=_TASK_TYPE_INTERVIEW,
                data={"interview_type": contract.name, "state": "active"},
            )
            await handle.start()
        except Exception as exc:
            logger.debug("_ensure_active_task: failed: %s", exc)

    async def _close_task(
        self,
        visitor: Any,
        status: str = "completed",
        contract_name: Optional[str] = None,
    ) -> None:
        try:
            store = visitor.tasks
        except Exception:
            return
        try:
            interview_handles = store.list(
                status="active", owner_action=_TASK_OWNER_ACTION
            )
        except Exception:
            interview_handles = []
        for handle in interview_handles:
            if contract_name and self._task_interview_type(handle) != contract_name:
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
                logger.debug(
                    "_close_task: failed for %s: %s", getattr(handle, "id", "?"), exc
                )

        if contract_name:
            try:
                skill_handles = store.list(status="active", owner_action=contract_name)
            except Exception:
                skill_handles = []
            for handle in skill_handles:
                try:
                    if status == "completed":
                        await handle.complete()
                    elif status == "cancelled":
                        await handle.cancel()
                    elif status == "failed":
                        await handle.fail()
                except Exception as exc:
                    logger.debug(
                        "_close_task: skill %s failed for %s: %s",
                        status,
                        getattr(handle, "id", "?"),
                        exc,
                    )

    def _resolve_field_param(
        self,
        field: str,
        name: str,
        contract: InterviewContract,
        **kwargs: Any,
    ) -> tuple[str, Optional[str]]:
        """Resolve field name from field or name alias; return (resolved, error_message)."""
        raw_field = (field or "").strip()
        raw_name = (name or kwargs.get("name") or "").strip()
        if raw_field and raw_name and raw_field != raw_name:
            return "", "field and name disagree — use field only"
        resolved = raw_field or raw_name
        if not resolved:
            return "", "Missing field — use parameter field (not name)"
        valid = {q.name for q in contract.questions}
        if resolved not in valid:
            return "", f"Unknown field '{resolved}'. Valid: {sorted(valid)}"
        return resolved, None

    # ─── Skill activation (session bootstrap) ───────────────────────────

    def is_interview_skill(self, skill_name: str) -> bool:
        """True when skill_name matches a loaded interview contract."""
        return bool(self._contract_registry.get(skill_name))

    async def needs_session_rebootstrap(
        self, skill_name: str, visitor: Any = None
    ) -> bool:
        """True when an interview skill is active but has no session in context."""
        await self._ensure_contracts_loaded()
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
        """Create or resume interview session when use_skill activates an interview skill."""
        await self._ensure_contracts_loaded()
        if not self.is_interview_skill(skill_name):
            available = self._contract_registry.list_contracts()
            logger.warning(
                "InterviewAction.on_skill_activate: unknown interview skill %r "
                "(available contracts: %s)",
                skill_name,
                available,
            )
            return (
                f"Interview skill '{skill_name}' has no contract.yaml on this agent. "
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
        else:
            parts.append("Call interview__next_question next.")
        return " ".join(parts)

    async def resolve_locked_skill(
        self, visitor: Any, skill_docs: List[Any]
    ) -> Optional[Any]:
        """Map active interview session or INTEGRVIEW task to a locked_in SkillDoc."""
        skill_by_name = {d.name: d for d in skill_docs if getattr(d, "name", None)}
        conversation = await self._get_conversation(visitor)
        if conversation is not None:
            session = load_session(conversation)
            if session is not None and session.is_active():
                sd = skill_by_name.get(session.interview_type)
                if sd is not None and getattr(sd, "locked_in", False):
                    return sd

        store = None
        if visitor is not None and hasattr(visitor, "tasks"):
            try:
                store = visitor.tasks
            except Exception:
                store = None
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
            active_tasks = store.list(status="active")
        except Exception:
            return None

        for task in active_tasks or []:
            owner = getattr(task, "owner_action", None)
            sd = None
            if owner and owner in skill_by_name:
                sd = skill_by_name[owner]
            elif owner == _TASK_OWNER_ACTION:
                interview_type = self._task_interview_type(task)
                if interview_type and interview_type in skill_by_name:
                    sd = skill_by_name[interview_type]
            if sd is not None and getattr(sd, "locked_in", False):
                updated_at = str(getattr(task, "updated_at", "") or "")
                candidates.append((updated_at, sd))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    # ─── Tool Handlers: Data Operations ─────────────────────────────

    async def _handle_start(
        self,
        interview_type: str,
        visitor: Any = None,
        force_fresh: bool = False,
        **kwargs: Any,
    ) -> str:
        contract = self._contract_registry.get(interview_type)
        if not contract:
            available = self._contract_registry.list_contracts()
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
        if isinstance(raw_fresh, str):
            force_fresh = raw_fresh.strip().lower() in ("true", "1", "yes")
        else:
            force_fresh = bool(raw_fresh)

        conversation = await self._get_conversation(visitor)
        existing = load_session(conversation) if conversation else None

        if existing and existing.is_active():
            if visitor is None:
                visitor = get_dispatch_visitor()
            if visitor:
                try:
                    await self._ensure_active_task(visitor, contract)
                except Exception:
                    pass
            missing_existing = existing.missing_required(contract.get_required_fields())
            return interview_tool_response(
                ok=True,
                status=existing.status.value,
                interview_type=existing.interview_type,
                fields=existing.get_collected_summary(),
                skipped_fields=sorted(existing.skipped_fields),
                missing_required=missing_existing,
                questions=[_question_def_to_dict(q) for q in contract.questions],
                validators=[_validator_def_to_dict(v) for v in contract.validators],
                custom_tools=[
                    skill_tool_name(contract, t.name) for t in contract.tools
                ],
            )

        fresh_session = False
        if conversation and existing:
            if existing.interview_type != interview_type or existing.status in (
                InterviewStatus.COMPLETED,
                InterviewStatus.CANCELLED,
            ):
                if existing.interview_type != interview_type:
                    if visitor is None:
                        visitor = get_dispatch_visitor()
                    if visitor:
                        try:
                            await self._close_task(
                                visitor,
                                status="cancelled",
                                contract_name=existing.interview_type,
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

        if visitor is None:
            visitor = get_dispatch_visitor()
        if visitor:
            try:
                await self._ensure_active_task(visitor, contract)
            except Exception:
                pass

        user_message = (kwargs.get("user_message") or "").strip()
        seeded_fields: List[str] = []
        skip_to_review = False
        post_tools_results: List[Dict[str, Any]] = []
        if user_message:
            seed_result = await self._seed_fields_from_user_message(
                session, contract, user_message, visitor
            )
            seeded_fields = seed_result.get("seeded", [])
            skip_to_review = bool(seed_result.get("skip_to_review"))
            post_tools_results = seed_result.get("post_tools_results") or []

        missing = session.missing_required(contract.get_required_fields())

        return interview_tool_response(
            ok=True,
            status="active",
            fresh_session=fresh_session,
            interview_type=contract.name,
            fields=session.get_collected_summary(),
            skipped_fields=sorted(session.skipped_fields),
            missing_required=missing,
            questions=[_question_def_to_dict(q) for q in contract.questions],
            validators=[_validator_def_to_dict(v) for v in contract.validators],
            custom_tools=[skill_tool_name(contract, t.name) for t in contract.tools],
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
        session, contract = await self._get_session_and_contract(visitor)
        if not session or not contract:
            return interview_tool_response(
                ok=False,
                status="error",
                error_code="NO_SESSION",
                response_directive=no_session_directive(),
            )

        resolved_field, field_err = self._resolve_field_param(
            field, name, contract, **kwargs
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

        check = await self._validate_field_for_persist(
            contract, resolved_field, value, visitor, session=session
        )
        if not check.get("valid"):
            next_qs = self._next_questions(session, contract)
            err = check.get("error", "Invalid value")
            question_text = ""
            if next_qs and next_qs[0].get("question"):
                question_text = next_qs[0]["question"]
            directive = check.get("response_directive") or (
                tell_user_directive(f"{err} {question_text}")
                if question_text
                else tell_user_directive(f"{err} Please try again.")
            )
            return interview_tool_response(
                ok=False,
                status="validation_failed",
                valid=False,
                error_code="VALIDATION_FAILED",
                error=err,
                field=resolved_field,
                fields=session.get_collected_summary(),
                skipped_fields=sorted(session.skipped_fields),
                missing_required=session.missing_required(
                    contract.get_required_fields()
                ),
                next_questions=next_qs,
                response_directive=directive,
            )

        stored_value = check.get("value", value)
        await self.persist_interview_fields(
            session, visitor, {resolved_field: stored_value}, validate=False
        )

        missing = session.missing_required(contract.get_required_fields())

        payload: Dict[str, Any] = {
            "ok": True,
            "status": session.status.value,
            "field": resolved_field,
            "value": stored_value,
            "fields": session.get_collected_summary(),
            "skipped_fields": sorted(session.skipped_fields),
            "missing_required": missing,
        }

        if check.get("interview_complete"):
            payload["interview_complete"] = True
            if check.get("response_directive"):
                payload["response_directive"] = check["response_directive"]
            return json.dumps(payload)

        q_def = contract.get_question(resolved_field)
        if q_def and q_def.post_tools:
            payload = await self._merge_post_tools(
                payload, q_def, session, contract, visitor, stored_value
            )
            payload.pop("next_questions", None)

        return json.dumps(payload)

    async def _merge_post_tools(
        self,
        payload: Dict[str, Any],
        question_def: QuestionDef,
        session: InterviewSession,
        contract: InterviewContract,
        visitor: Any,
        stored_value: str,
    ) -> Dict[str, Any]:
        """Run post_tools after a field is stored; merge results into set_field payload."""
        post_results: List[Dict[str, Any]] = []
        last_directive: Optional[str] = None
        last_next_tool: Optional[str] = None

        for tool_name in question_def.post_tools:
            func = self._load_custom_function(contract, tool_name)
            if not func:
                continue
            try:
                result = await self._call_custom_function(
                    func, session, contract, visitor
                )
                parsed: Dict[str, Any] = {}
                if isinstance(result, str):
                    try:
                        parsed = json.loads(result)
                    except (json.JSONDecodeError, TypeError):
                        post_results.append(
                            {
                                "tool": tool_name,
                                "ok": False,
                                "error": "Invalid tool response",
                            }
                        )
                        continue
                elif isinstance(result, dict):
                    parsed = result
                else:
                    post_results.append(
                        {
                            "tool": tool_name,
                            "ok": False,
                            "error": "Empty tool response",
                        }
                    )
                    continue

                entry = slim_post_tool_entry(tool_name, parsed)
                post_results.append(entry)

                for key in (
                    "skip_to_review",
                    "next_tool",
                    "exists",
                    "status",
                    "interview_complete",
                    "system_message",
                    "error",
                    "error_code",
                    "response_directive",
                ):
                    if key in parsed:
                        payload[key] = parsed[key]

                if parsed.get("skip_to_review"):
                    last_next_tool = "interview__review"
                    last_directive = call_tool_directive("interview__review")
                elif parsed.get("exists") and parsed.get("interview_complete"):
                    last_directive = parsed.get("response_directive")
                elif parsed.get("next_tool") == "interview__review":
                    last_next_tool = parsed["next_tool"]
                    last_directive = parsed.get(
                        "response_directive"
                    ) or call_tool_directive(parsed["next_tool"])
            except Exception as e:
                logger.error(
                    "post_tools '%s' failed for question '%s': %s",
                    tool_name,
                    question_def.name,
                    e,
                )
                post_results.append(
                    {
                        "tool": tool_name,
                        "ok": False,
                        "error": str(e),
                    }
                )

        if post_results:
            payload["post_tools_results"] = post_results
            if any(not r.get("ok", True) for r in post_results):
                payload["ok"] = False

        if last_directive is not None:
            payload["response_directive"] = last_directive
        if last_next_tool is not None:
            payload["next_tool"] = last_next_tool
        elif payload.get("skip_to_review"):
            payload["next_tool"] = "interview__review"

        return payload

    async def _handle_next_question(self, visitor: Any = None) -> str:
        """Return the next question to ask, running pre_tools for context."""
        session, contract = await self._get_session_and_contract(visitor)
        if not session or not contract:
            return interview_step_response(
                ok=False,
                status="error",
                error_code="NO_SESSION",
                error="No active interview session.",
                response_directive=no_session_directive(),
            )

        missing = session.missing_required(contract.get_required_fields())
        next_qs = self._next_questions(session, contract)

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

        q_def = contract.get_question(next_qs[0]["name"])
        if not q_def:
            return interview_step_response(
                ok=False,
                status="error",
                error="No question definition found for the next step.",
            )

        directive, extras = await self._run_pre_tools(
            session, contract, q_def, visitor=visitor
        )
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
        session = await self._get_session(visitor)
        if not session:
            return json.dumps(
                {
                    "status": "error",
                    "response_directive": "No active interview session.",
                }
            )

        value = session.get_value(field)
        result: Dict[str, Any] = {
            "status": session.status.value,
            "field": field,
            "value": value,
            "is_set": session.has_field(field),
            "is_skipped": session.is_skipped(field),
            "fields": session.get_collected_summary(),
            "skipped_fields": sorted(session.skipped_fields),
        }
        contract = self._contract_registry.get(session.interview_type)
        if contract:
            result["missing_required"] = session.missing_required(
                contract.get_required_fields()
            )
            result["next_questions"] = self._next_questions(session, contract)
        return json.dumps(result)

    async def _handle_skip_field(self, field: str, visitor: Any = None) -> str:
        session, contract = await self._get_session_and_contract(visitor)
        if not session or not contract:
            return json.dumps(
                {
                    "status": "error",
                    "response_directive": "No active interview session.",
                }
            )

        q = contract.get_question(field)
        if q and q.required:
            question = q.question or f"Please provide your {field.replace('_', ' ')}."
            return json.dumps(
                {
                    "status": session.status.value,
                    "response_directive": tell_user_directive(question),
                }
            )

        session.skip_field(field)
        await self._save_session(session, visitor)

        missing = session.missing_required(contract.get_required_fields())

        return json.dumps(
            {
                "ok": True,
                "status": session.status.value,
                "field": field,
                "skipped": True,
                "fields": session.get_collected_summary(),
                "skipped_fields": sorted(session.skipped_fields),
                "missing_required": missing,
            }
        )

    async def _handle_get_status(self, visitor: Any = None) -> str:
        if visitor is None:
            visitor = get_dispatch_visitor()
        session = await self._get_session(visitor)

        if not session:
            available = self._contract_registry.list_contracts()
            return json.dumps(
                {
                    "status": "no_session",
                    "available_interview_types": available,
                    "response_directive": (
                        "No active interview session. Available types: "
                        + ", ".join(available)
                        if available
                        else "No interview types configured."
                    ),
                }
            )

        contract = self._contract_registry.get(session.interview_type)
        missing = contract.get_required_fields() if contract else []
        missing_required = session.missing_required(missing)

        result = {
            "status": session.status.value,
            "interview_type": session.interview_type,
            "fields": session.get_collected_summary(),
            "skipped_fields": sorted(session.skipped_fields),
            "missing_required": missing_required,
            "next_questions": (
                self._next_questions(session, contract) if contract else []
            ),
            "started_at": session.started_at,
        }

        if contract:
            result["questions"] = [_question_def_to_dict(q) for q in contract.questions]
            result["validators"] = [
                _validator_def_to_dict(v) for v in contract.validators
            ]
            result["custom_tools"] = [
                skill_tool_name(contract, t.name) for t in contract.tools
            ]

        return json.dumps(result)

    async def _handle_review(self, visitor: Any = None) -> str:
        session, contract = await self._get_session_and_contract(visitor)
        if not session or not contract:
            return json.dumps(
                {
                    "status": "error",
                    "response_directive": "No active interview session.",
                }
            )

        if contract.review and contract.review.function:
            return await self._handle_custom_review(visitor)

        collected = session.get_collected_summary()
        review_lines = []
        for q in contract.questions:
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

        return json.dumps(
            {
                "status": "review",
                "response_directive": tell_user_directive(
                    "Does everything look correct, or would you like to change anything?"
                ),
                "fields": collected,
                "skipped_fields": sorted(session.skipped_fields),
                "summary": summary,
                "next_questions": [],
            }
        )

    async def _handle_complete(self, visitor: Any = None) -> str:
        session, contract = await self._get_session_and_contract(visitor)
        if not session or not contract:
            return json.dumps(
                {
                    "status": "error",
                    "response_directive": "No active interview session.",
                }
            )

        if contract.completion and contract.completion.function:
            return await self._handle_custom_complete(visitor)

        fields_summary = session.get_collected_summary()
        await self._clear_interview_session(visitor)

        if visitor is None:
            visitor = get_dispatch_visitor()
        try:
            await self._close_task(
                visitor,
                status="completed",
                contract_name=contract.name if contract else None,
            )
        except Exception:
            pass

        return json.dumps(
            {
                "status": "completed",
                "response_directive": "Interview completed successfully.",
                "fields": fields_summary,
            }
        )

    async def _handle_cancel(self, visitor: Any = None) -> str:
        session = await self._get_session(visitor)
        if not session:
            return json.dumps(
                {
                    "status": "error",
                    "response_directive": "No active interview session to cancel.",
                }
            )

        await self._clear_interview_session(visitor)

        if visitor is None:
            visitor = get_dispatch_visitor()
        try:
            await self._close_task(visitor, status="cancelled")
        except Exception:
            pass

        return interview_tool_response(
            status="cancelled",
            response_directive=tell_user_directive(
                "I've cancelled this. Say what you'd like to do next, or start a new "
                "interview when you're ready."
            ),
            fields={},
        )

    # ─── Tool Handlers: Validation ──────────────────────────────────

    async def _handle_validate(
        self,
        vdef: ValidatorDef,
        value: str,
        kwargs: dict,
        visitor: Any = None,
        session: Optional[InterviewSession] = None,
    ) -> str:
        """Validate a value using the specified validator definition."""
        validated_value = value.strip() if value else ""
        if not validated_value:
            result = {
                "valid": False,
                "error": f"No value provided for validation by {vdef.name}",
                "field": None,
                "value": None,
                "validator": vdef.name,
            }
            return json.dumps(await self._enrich_with_state(result, visitor))

        validator_fn = _get_builtin_validator(vdef.name)
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
                    result = {
                        "valid": True,
                        "value": autocorrected or validated_value,
                        "field": None,
                        "validator": vdef.name,
                    }
                    return json.dumps(await self._enrich_with_state(result, visitor))
                else:
                    result = {
                        "valid": False,
                        "error": error_msg or f"Validation failed for {vdef.name}",
                        "field": None,
                        "value": validated_value,
                        "validator": vdef.name,
                    }
                    return json.dumps(await self._enrich_with_state(result, visitor))
            except Exception as e:
                logger.error(f"Builtin validator {vdef.name} failed: {e}")
                result = {
                    "valid": False,
                    "error": f"Validator error: {e}",
                    "field": None,
                    "validator": vdef.name,
                }
                return json.dumps(await self._enrich_with_state(result, visitor))

        for contract in self._contract_registry._contracts.values():
            func = self._load_custom_function(contract, vdef.name)
            if func and callable(func):
                try:
                    call_visitor = (
                        visitor if visitor is not None else get_dispatch_visitor()
                    )
                    result = await self._call_custom_function(
                        func,
                        session,
                        contract,
                        visitor=call_visitor,
                        value=validated_value,
                        kwargs=kwargs,
                    )
                    if asyncio.iscoroutine(result):
                        result = await result
                    parsed = self._parse_validation_result_dict(
                        result, validated_value, vdef.name
                    )
                    for key in ("interview_complete", "response_directive"):
                        if isinstance(result, dict) and key in result:
                            parsed[key] = result[key]
                    return json.dumps(await self._enrich_with_state(parsed, visitor))
                except Exception as e:
                    logger.error(f"Custom validator {vdef.name} failed: {e}")
                    result = {
                        "valid": False,
                        "error": f"Validator error: {e}",
                        "validator": vdef.name,
                    }
                    return json.dumps(await self._enrich_with_state(result, visitor))

        result = {
            "valid": False,
            "error": f"No validator found for {vdef.name}",
            "validator": vdef.name,
        }
        return json.dumps(await self._enrich_with_state(result, visitor))

    def _parse_validation_result_dict(
        self, result: Any, original_value: str, validator_name: str
    ) -> Dict[str, Any]:
        """Parse a custom validator result into a dict (not JSON)."""
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
            else:
                out = {
                    "valid": False,
                    "error": result.get(
                        "error", f"Validation failed for {validator_name}"
                    ),
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
            else:
                return {
                    "valid": False,
                    "error": error_msg or f"Validation failed for {validator_name}",
                    "validator": validator_name,
                }

        if isinstance(result, str):
            try:
                as_json = json.loads(result)
                if isinstance(as_json, dict) and "valid" in as_json:
                    return self._parse_validation_result_dict(
                        as_json, original_value, validator_name
                    )
            except (json.JSONDecodeError, TypeError):
                pass
            return {"valid": True, "value": result, "validator": validator_name}

        return {
            "valid": False,
            "error": f"Unexpected validation result type: {type(result)}",
            "validator": validator_name,
        }

    # ─── Tool Handlers: Custom Tools ─────────────────────────────────
    # Custom tools that produce field values must call persist_interview_fields
    # or return persist_fields in JSON (applied before enrich).

    async def _handle_custom_tool(
        self, tdef: ToolDef, contract: InterviewContract, **kwargs
    ) -> str:
        if tdef.function:
            for c in self._contract_registry._contracts.values():
                func = self._load_custom_function(c, tdef.function)
                if func and callable(func):
                    try:
                        visitor = kwargs.pop("visitor", None) or get_dispatch_visitor()
                        session = await self._get_session(visitor)
                        call_kwargs = {
                            "visitor": visitor,
                            "interview_action": self,
                            "session": session,
                            "config": c,
                        }
                        if session:
                            call_kwargs["extracted_values"] = (
                                session.get_collected_summary()
                            )
                        call_kwargs.update(kwargs)

                        sig_params = set()
                        try:
                            sig = inspect.signature(func)
                            sig_params = set(sig.parameters.keys())
                        except (ValueError, TypeError):
                            pass

                        if sig_params:
                            call_kwargs = {
                                k: v for k, v in call_kwargs.items() if k in sig_params
                            }

                        result = func(**call_kwargs)
                        if asyncio.iscoroutine(result):
                            result = await result

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
                            return await self._finalize_tool_response(
                                result, session, visitor
                            )
                        return json.dumps(
                            {"result": "ok"}
                            if result is not None
                            else {"result": "empty"}
                        )
                    except Exception as e:
                        logger.error(f"Custom tool '{tdef.name}' failed: {e}")
                        return json.dumps({"error": str(e)})

        return json.dumps(
            {"error": f"Custom tool '{tdef.name}' has no function configured"}
        )

    async def _handle_action_tool(
        self, tdef: ToolDef, contract: InterviewContract, **kwargs
    ) -> str:
        if not tdef.function:
            action_name = kwargs.pop("_action_name", None)
            method_name = kwargs.pop("_method_name", None)
        else:
            parts = tdef.function.split(".", 1)
            if len(parts) == 2:
                action_name, method_name = parts
            else:
                action_name = None
                method_name = tdef.function

        if action_name and method_name:
            try:
                action: Optional[Action] = await self.get_action(action_name)
                if action:
                    method = getattr(action, method_name, None)
                    if method and callable(method):
                        visitor = kwargs.pop("visitor", None) or get_dispatch_visitor()
                        session = await self._get_session(visitor)
                        params = (
                            self._resolve_tool_params(tdef.parameters, session)
                            if session
                            else kwargs
                        )
                        result = await method(**params)
                        if isinstance(result, dict):
                            return json.dumps(result)
                        return str(result) if result else json.dumps({"result": "ok"})
            except Exception as e:
                logger.error(f"Action tool '{tdef.name}' failed: {e}")
                return json.dumps({"error": str(e)})

        return json.dumps({"error": f"Action tool '{tdef.name}' could not be resolved"})

    async def _handle_decorated_function(
        self, func: Callable, contract: InterviewContract, **kwargs
    ) -> str:
        try:
            visitor = kwargs.pop("visitor", None) or get_dispatch_visitor()
            session = await self._get_session(visitor)
            call_kwargs = {
                "visitor": visitor,
                "interview_action": self,
                "session": session,
                "config": contract,
            }
            if session:
                call_kwargs["extracted_values"] = session.get_collected_summary()
            call_kwargs.update(kwargs)

            sig_params = set()
            try:
                sig = inspect.signature(func)
                sig_params = set(sig.parameters.keys())
            except (ValueError, TypeError):
                pass

            if sig_params:
                call_kwargs = {k: v for k, v in call_kwargs.items() if k in sig_params}

            result = func(**call_kwargs)
            if asyncio.iscoroutine(result):
                result = await result

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
            logger.error(f"Decorated function call failed: {e}")
            return json.dumps({"error": str(e)})

    # ─── Custom Review & Completion ──────────────────────────────────

    async def _handle_custom_review(self, visitor: Any = None) -> str:
        session, contract = await self._get_session_and_contract(visitor)
        if not session or not contract:
            return json.dumps(
                {
                    "status": "error",
                    "response_directive": "No active interview session.",
                }
            )

        if not contract.review or not contract.review.function:
            return await self._handle_review(visitor)

        func = self._load_custom_function(contract, contract.review.function)
        if not func:
            return await self._handle_review(visitor)

        try:
            result = await self._call_custom_function(func, session, contract, visitor)
        except Exception as e:
            logger.error(
                f"Custom review function '{contract.review.function}' failed: {e}"
            )
            return json.dumps(
                {
                    "status": "error",
                    "response_directive": f"Custom review function failed: {e}",
                }
            )

        collected = session.get_collected_summary()
        omit_fields = set()
        additional_data = {}
        custom_message = ""
        directive = ""

        terminate = False
        if isinstance(result, dict):
            modified_values = result.get("modified_values", {})
            additional_data = result.get("additional_data", {})
            custom_message = result.get("custom_message", "")
            directive = result.get("directive", "")
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
            if visitor is None:
                visitor = get_dispatch_visitor()
            try:
                await self._close_task(
                    visitor, status="completed", contract_name=contract.name
                )
            except Exception:
                pass
            await self._clear_interview_session(visitor)
            status_text = (
                custom_message or directive or "Share the package status update."
            )
            if collected.get("tracking_number"):
                status_text = (
                    f"Package status for tracking {collected['tracking_number']}: "
                    f"{status_text}"
                )
            return interview_tool_response(
                status="completed",
                terminate=True,
                response_directive=tell_user_directive(status_text),
                fields=collected,
                skipped_fields=sorted(session.skipped_fields),
                custom_message=custom_message,
            )

        review_lines = []
        for q in contract.questions:
            if q.name in omit_fields:
                continue
            if session.is_skipped(q.name):
                continue
            if q.name in collected:
                label = q.name.replace("_", " ").title()
                review_lines.append(f"**{label}**: {collected[q.name]}")
            elif q.required:
                label = q.name.replace("_", " ").title()
                review_lines.append(f"**{label}**: *(not provided)*")

        if additional_data:
            for label, value in additional_data.items():
                review_lines.append(f"**{label}**: {value}")

        summary = "\n\n".join(review_lines)

        if not directive:
            directive = tell_user_directive(
                "Does everything look correct, or would you like to change anything?"
            )
        elif not directive.strip().startswith("Tell the user:"):
            directive = tell_user_directive(directive)

        session.status = InterviewStatus.REVIEW
        await self._save_session(session, visitor)

        return json.dumps(
            {
                "status": "review",
                "response_directive": directive,
                "fields": collected,
                "skipped_fields": sorted(session.skipped_fields),
                "summary": summary,
                "custom_message": custom_message,
            }
        )

    async def _handle_custom_complete(self, visitor: Any = None) -> str:
        session, contract = await self._get_session_and_contract(visitor)
        if not session or not contract:
            return json.dumps(
                {
                    "status": "error",
                    "response_directive": "No active interview session.",
                }
            )

        cc_cfg = contract.completion
        if not cc_cfg or not cc_cfg.function:
            return json.dumps(
                {
                    "status": "error",
                    "response_directive": "No completion function configured.",
                }
            )

        func = self._load_custom_function(contract, cc_cfg.function)
        if not func:
            return json.dumps(
                {
                    "status": "error",
                    "response_directive": f"Completion function '{cc_cfg.function}' not found.",
                }
            )

        try:
            result = await self._call_custom_function(func, session, contract, visitor)
        except Exception as e:
            logger.error(f"Completion function '{cc_cfg.function}' failed: {e}")
            return json.dumps(
                {
                    "status": "error",
                    "response_directive": f"Completion function failed: {e}",
                }
            )

        fields_summary = session.get_collected_summary()

        if visitor is None:
            visitor = get_dispatch_visitor()
        try:
            await self._close_task(
                visitor,
                status="completed",
                contract_name=contract.name if contract else None,
            )
        except Exception:
            pass

        await self._clear_interview_session(visitor)

        if result and isinstance(result, dict):
            return json.dumps(
                {
                    "status": "completed",
                    "response_directive": result.get(
                        "directive", result.get("message", "Interview completed.")
                    ),
                    "completion_result": result,
                    "fields": fields_summary,
                }
            )

        return json.dumps(
            {
                "status": "completed",
                "response_directive": "Interview completed successfully.",
                "fields": fields_summary,
            }
        )

    # ─── Helpers ───────────────────────────────────────────────────

    async def _get_session_and_contract(
        self, visitor: Any = None
    ) -> Tuple[Optional[InterviewSession], Optional[InterviewContract]]:
        await self._ensure_contracts_loaded()
        session = await self._get_session(visitor)
        if not session:
            return None, None
        contract = self._contract_registry.get(session.interview_type)
        return session, contract

    def _load_custom_function(
        self, contract: InterviewContract, function_name: str
    ) -> Optional[Callable]:
        custom_tools_path = os.path.join(
            contract.source_dir, "scripts", "custom_tools.py"
        )
        if not os.path.isfile(custom_tools_path):
            custom_tools_path = os.path.join(contract.source_dir, "custom_tools.py")
        if not os.path.isfile(custom_tools_path):
            return None

        try:
            from .decorators import interview_tool as _it

            spec = importlib.util.spec_from_file_location(
                f"interview_custom_tools_{contract.name}", custom_tools_path
            )
            if not spec or not spec.loader:
                return None
            module = importlib.util.module_from_spec(spec)
            module.__dict__["interview_tool"] = _it
            module.__dict__["ExtractionStatus"] = ExtractionStatus
            module.__dict__["InterviewSession"] = InterviewSession
            spec.loader.exec_module(module)
            func = getattr(module, function_name, None)
            if func and callable(func):
                return func
        except Exception as e:
            logger.error(
                f"Failed to load custom function '{function_name}' from {custom_tools_path}: {e}"
            )

        return None

    async def _call_custom_function(
        self,
        func: Callable,
        session: Optional[InterviewSession],
        contract: InterviewContract,
        visitor: Any = None,
        value: Optional[str] = None,
        kwargs: Optional[dict] = None,
    ) -> Any:
        if visitor is None:
            visitor = get_dispatch_visitor()

        extracted_values = session.get_collected_summary() if session else {}

        call_kwargs = {
            "session": session,
            "visitor": visitor,
            "interview_action": self,
            "config": contract,
            "extracted_values": extracted_values,
        }

        if value is not None:
            call_kwargs["value"] = value

        if kwargs and isinstance(kwargs, dict):
            call_kwargs.update(kwargs)

        sig_params = set()
        try:
            sig = inspect.signature(func)
            sig_params = set(sig.parameters.keys())
        except (ValueError, TypeError):
            pass

        call_kwargs = (
            {k: v for k, v in call_kwargs.items() if k in sig_params}
            if sig_params
            else call_kwargs
        )

        result = func(**call_kwargs)
        if asyncio.iscoroutine(result):
            result = await result

        return result

    def _resolve_tool_params(
        self, template: Dict[str, Any], session: InterviewSession
    ) -> Dict[str, Any]:
        resolved = {}
        for key, val in template.items():
            if isinstance(val, str) and val.startswith("{") and val.endswith("}"):
                field_name = val[1:-1]
                resolved[key] = session.get_value(field_name) or val
            else:
                resolved[key] = val
        return resolved

    async def _run_pre_tools(
        self,
        session: InterviewSession,
        contract: InterviewContract,
        question_def: QuestionDef,
        visitor: Any = None,
    ) -> tuple[str, Dict[str, Any]]:
        """Run pre_tools before asking this question; return directive and extras."""
        extras: Dict[str, Any] = {}
        pre_tools = question_def.resolved_pre_tools()
        pre_results: List[Dict[str, Any]] = []

        if not pre_tools:
            return tell_user_directive(question_def.question), extras

        directive: Optional[str] = None
        for tool_name in pre_tools:
            func = self._load_custom_function(contract, tool_name)
            if not func:
                continue
            try:
                result = await self._call_custom_function(
                    func, session, contract, visitor=visitor
                )
                parsed: Dict[str, Any] = {}
                if isinstance(result, dict):
                    parsed = result
                elif isinstance(result, str):
                    try:
                        parsed = json.loads(result)
                    except (json.JSONDecodeError, TypeError):
                        pre_results.append(
                            {
                                "tool": tool_name,
                                "ok": False,
                                "error": "Invalid tool response",
                            }
                        )
                        continue

                tool_ok = parsed.get("ok", True) if parsed else False
                if parsed:
                    pre_results.append({"tool": tool_name, "ok": tool_ok, **parsed})
                    if parsed.get("value") is not None:
                        extras["suggested_value"] = parsed["value"]
                    if parsed.get("directive"):
                        directive = parsed["directive"]
            except Exception as e:
                logger.error(
                    "pre_tools '%s' failed for question '%s': %s",
                    tool_name,
                    question_def.name,
                    e,
                )
                pre_results.append(
                    {
                        "tool": tool_name,
                        "ok": False,
                        "error": str(e),
                    }
                )

        extras["pre_tools_results"] = pre_results
        if directive:
            return directive, extras
        return tell_user_directive(question_def.question), extras

    async def _resolve_question_directive(
        self,
        session: InterviewSession,
        contract: InterviewContract,
        question_def: QuestionDef,
        visitor: Any = None,
    ) -> tuple[str, Dict[str, Any]]:
        """Return (response_directive, extras) for asking this question."""
        return await self._run_pre_tools(
            session, contract, question_def, visitor=visitor
        )

    async def _apply_next_question_directive(
        self,
        *,
        session: InterviewSession,
        contract: InterviewContract,
        next_qs: List[Dict[str, Any]],
        missing: List[str],
        visitor: Any = None,
    ) -> tuple[str, Optional[str]]:
        """Pick response_directive and next_tool for the next interview step."""
        directive, next_tool = directive_for_missing_fields(next_qs, missing)
        if next_tool or not next_qs:
            return directive, next_tool

        q_def = contract.get_question(next_qs[0]["name"])
        if not q_def:
            return tell_user_directive(next_qs[0]["question"]), next_tool

        response_directive, ctx_extra = await self._resolve_question_directive(
            session, contract, q_def, visitor=visitor
        )
        if ctx_extra.get("suggested_value") is not None:
            next_qs[0]["suggested_value"] = ctx_extra["suggested_value"]
        return response_directive, next_tool

    def _next_questions(
        self, session: InterviewSession, contract: InterviewContract
    ) -> List[Dict[str, Any]]:
        """Return ordered list of questions that still need answers."""
        collected = session.get_collected_summary()
        result = []
        for q in contract.questions:
            if q.name in collected or session.is_skipped(q.name):
                continue
            entry = {
                "name": q.name,
                "question": q.question,
                "required": q.required,
                "validator": q.validator,
            }
            if q.input_context_provider:
                entry["input_context_provider"] = q.input_context_provider
            if q.pre_tools:
                entry["pre_tools"] = q.pre_tools
            if q.post_tools:
                entry["post_tools"] = q.post_tools
            result.append(entry)
        return result

    @staticmethod
    def _should_attach_session_state(
        response_dict: Dict[str, Any],
        session: Optional[InterviewSession],
    ) -> bool:
        """Whether to merge persisted session fields/questions into this tool response."""
        if not session:
            return False

        target_type = response_dict.get("interview_type")
        if not isinstance(target_type, str) or not target_type.strip():
            return True

        next_tool = response_dict.get("next_tool")
        status = response_dict.get("status")
        routing = status == "start_new"
        if not routing:
            return True

        if session.interview_type != target_type:
            return False
        if not session.is_active():
            return False
        return True

    async def _enrich_with_state(
        self, response_dict: Dict[str, Any], visitor: Any = None
    ) -> Dict[str, Any]:
        """Add interview state context (fields, missing_required, next_questions, response_directive) to a response dict."""
        session, contract = await self._get_session_and_contract(visitor)
        if (
            session
            and contract
            and self._should_attach_session_state(response_dict, session)
        ):
            response_dict["fields"] = session.get_collected_summary()
            response_dict["skipped_fields"] = sorted(session.skipped_fields)
            response_dict["missing_required"] = session.missing_required(
                contract.get_required_fields()
            )
            next_qs = self._next_questions(session, contract)
            response_dict["next_questions"] = next_qs

            if (
                "response_directive" not in response_dict
                and response_dict.get("valid") is False
            ):
                response_dict.setdefault("error_code", "VALIDATION_FAILED")
                err = response_dict.get("error", "Invalid value")
                if next_qs and next_qs[0].get("question"):
                    response_dict["response_directive"] = tell_user_directive(
                        f"{err} {next_qs[0]['question']}"
                    )
                else:
                    response_dict["response_directive"] = tell_user_directive(
                        f"{err} Please try again."
                    )

        return response_dict


# ─── Module-level helpers ─────────────────────────────────────────


def _get_builtin_validator(format_name: str):
    from .validators import get_validator

    return get_validator(format_name)


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
    if q.input_context_provider:
        result["input_context_provider"] = q.input_context_provider
    if q.pre_tools:
        result["pre_tools"] = q.pre_tools
    if q.post_tools:
        result["post_tools"] = q.post_tools
    return result


def _validator_def_to_dict(v: ValidatorDef) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "function": v.name,
        "description": v.description,
    }
    if v.kwargs:
        result["kwargs"] = v.kwargs
    return result
