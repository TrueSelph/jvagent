"""InterviewAction — tool-driven interview runtime for orchestrator skills."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from jvagent.action.base import Action

from . import engine, tasks
from .hooks import clear_module_cache, load_hook_function
from .session import InterviewSession, load_session
from .spec import (
    INTERVIEW_FRONTMATTER_KEY,
    InterviewRegistry,
    InterviewSpec,
)
from .tools import build_tools

logger = logging.getLogger(__name__)


class InterviewAction(Action):
    """Provides interview tools for LLM-driven multi-turn flows."""

    description: str = (
        "Interview action that provides granular tools for conducting "
        "interviews. The LLM decides which tools to call at each step based on "
        "the interview spec and SKILL.md procedure."
    )
    binds_tools_to_visitor: bool = True

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._registry = InterviewRegistry()

    # -- discovery ----------------------------------------------------------

    async def on_register(self):
        await super().on_register()
        await self._discover_specs()

    async def on_reload(self):
        await super().on_reload()
        clear_module_cache()
        skills_dirs = await self.resolve_skill_scan_dirs()
        if skills_dirs:
            self._registry.reload(skills_dirs)

    async def on_startup(self):
        await super().on_startup()
        if not self._registry.specs:
            await self._discover_specs()

    async def _discover_specs(self) -> None:
        skills_dirs = await self.resolve_skill_scan_dirs()
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
        if not self._registry.specs:
            await self._discover_specs()

    def _load_fn(self, spec: InterviewSpec) -> Callable[[str], Optional[Callable]]:
        return lambda name: load_hook_function(spec, name)

    # -- tool surface --------------------------------------------------------

    async def get_tools(self) -> List[Any]:
        await self._ensure_specs_loaded()
        return build_tools(self)

    async def _handle_set_fields(
        self,
        fields: Optional[Dict[str, str]] = None,
        visitor: Any = None,
        **kwargs: Any,
    ) -> str:
        return await engine.handle_set_fields(self, fields, visitor, **kwargs)

    async def _handle_next_field(self, visitor: Any = None) -> str:
        return await engine.handle_next_field(self, visitor)

    async def _handle_skip_field(self, field: str, visitor: Any = None) -> str:
        return await engine.handle_skip_field(self, field, visitor)

    async def _handle_get_status(self, visitor: Any = None) -> str:
        return await engine.handle_get_status(self, visitor)

    async def _handle_review(self, visitor: Any = None) -> str:
        return await engine.handle_review(self, visitor)

    async def _handle_complete(self, visitor: Any = None) -> str:
        return await engine.handle_complete(self, visitor)

    async def _handle_cancel(self, visitor: Any = None) -> str:
        return await engine.handle_cancel(self, visitor)

    async def _handle_reset(self, visitor: Any = None) -> str:
        return await engine.handle_reset(self, visitor)

    async def _handle_start(
        self, interview_type: str, visitor: Any = None, **kwargs: Any
    ) -> str:
        return await engine.handle_start(self, interview_type, visitor, **kwargs)

    async def _handle_custom_tool(
        self, tdef: Any, spec: InterviewSpec, **kwargs
    ) -> str:
        return await engine.handle_custom_tool(self, tdef, spec, **kwargs)

    def _normalize_field_map(
        self,
        fields: Optional[Dict[str, str]] = None,
        **kwargs: Any,
    ) -> Dict[str, str]:
        return engine._normalize_field_map(fields, **kwargs)

    # -- session access (also used by skill hooks) ---------------------------

    async def _get_conversation(self, visitor: Any = None):
        return await engine.get_conversation(visitor)

    async def _get_session(self, visitor: Any = None) -> Optional[InterviewSession]:
        return await engine.get_session(visitor)

    async def _get_session_and_contract(
        self, visitor: Any = None
    ) -> Tuple[Optional[InterviewSession], Optional[InterviewSpec]]:
        return await engine.get_session_and_spec(self, visitor)

    async def _save_session(self, session: InterviewSession, visitor: Any = None):
        await engine.save_session_for(visitor, session)

    async def _clear_interview_session(
        self,
        visitor: Any = None,
        *,
        retain_context_keys: Optional[List[str]] = None,
    ) -> None:
        await engine.clear_interview_session(
            visitor, retain_context_keys=retain_context_keys
        )

    async def persist_interview_fields(
        self,
        session: InterviewSession,
        visitor: Any,
        fields: Dict[str, str],
        *,
        validate: bool = True,
    ) -> Dict[str, Any]:
        """Hook-initiated store used by custom skill tools."""
        return await engine.persist_interview_fields(
            self, session, visitor, fields, validate=validate
        )

    # -- orchestrator turn-lock hooks ----------------------------------------

    def is_interview_skill(self, skill_name: str) -> bool:
        return bool(self._registry.get(skill_name))

    async def _interview_ready(self, visitor: Any = None) -> bool:
        await self._ensure_specs_loaded()
        session, spec = await engine.get_session_and_spec(self, visitor)
        return session is not None and spec is not None and session.is_active()

    async def skill_runtime_ready(self, skill_name: str, visitor: Any = None) -> bool:
        if not self.is_interview_skill(skill_name):
            return False
        return await self._interview_ready(visitor)

    async def prepare_locked_skill_turn(
        self, skill_name: str, visitor: Any = None
    ) -> Any:
        from jvagent.action.interview_action import engine as interview_engine
        from jvagent.action.orchestrator.skill_tasks import LockedSkillPrep

        runtime_ready = await self.skill_runtime_ready(skill_name, visitor)
        pending_directive: Optional[str] = None
        if runtime_ready:
            session, spec = await self._get_session_and_contract(visitor)
            if session and spec:
                ctx = await interview_engine._session_field_context(
                    self, session, spec, visitor
                )
                pending_directive = ctx.get("field_awareness") or None
        return LockedSkillPrep(
            runtime_ready=runtime_ready,
            pending_directive=pending_directive,
        )

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
                f"(SKILL.md frontmatter '{INTERVIEW_FRONTMATTER_KEY}:'). "
                f"Available interview types: {available or '(none)'}. "
                "Do not call interview tools until the session is active."
            )
        raw = await self._handle_start(
            skill_name, visitor, user_message=(user_message or "").strip()
        )
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else {}
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if not isinstance(parsed, dict):
            return (
                f"Interview session ready ({skill_name}). "
                "Follow the interview SKILL procedure."
            )
        if parsed.get("status") == "error" or parsed.get("ok") is False:
            return (
                parsed.get("response_directive")
                or parsed.get("error")
                or f"Could not start interview session for {skill_name}."
            )
        awareness = (parsed.get("field_awareness") or "").strip()
        body = json.dumps(parsed)
        if awareness:
            return f"{awareness}\n\n{body}"
        return body

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
                if sd is None and owner == tasks.TASK_OWNER_ACTION:
                    it = tasks.task_interview_type(task)
                    sd = skill_by_name.get(it) if it else None
                if sd is not None and getattr(sd, "locked_in", False):
                    candidates.append((str(getattr(task, "updated_at", "") or ""), sd))
        except Exception:
            return None
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]
