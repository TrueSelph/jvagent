"""InterviewAction — tool-driven interview runtime for orchestrator skills."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from jvagent.action.base import Action

from ._constants import TASK_OWNER_ACTION
from .core.interview_loader import (
    INTERVIEW_FRONTMATTER_KEY,
    InterviewRegistry,
    InterviewSpec,
)
from .core.session import load_session
from .core.tools import build_tools
from .handlers import (
    InterviewFieldHandlersMixin,
    InterviewFlowHandlersMixin,
    InterviewSessionHandlersMixin,
)
from .runtime.hooks import clear_module_cache, load_hook_function
from .tasks import InterviewTaskMixin

logger = logging.getLogger(__name__)


class InterviewAction(
    InterviewFlowHandlersMixin,
    InterviewFieldHandlersMixin,
    InterviewSessionHandlersMixin,
    InterviewTaskMixin,
    Action,
):
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
        if self._registry.specs:
            return
        await self._discover_specs()

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
        from jvagent.action.orchestrator.skill_tasks import LockedSkillPrep

        runtime_ready = await self.skill_runtime_ready(skill_name, visitor)
        return LockedSkillPrep(runtime_ready=runtime_ready)

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
                f"(SKILL.md frontmatter '{INTERVIEW_FRONTMATTER_KEY}:'). "
                f"Available interview types: {available or '(none)'}. "
                "Do not call interview tools until the session is active."
            )
        import json

        raw = await self._handle_start(
            skill_name, visitor, user_message=(user_message or "").strip()
        )
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else {}
        except (json.JSONDecodeError, TypeError):
            return (
                f"Interview session ready ({skill_name}). "
                "Follow the interview SKILL procedure — call interview__next_question "
                "or interview__set_fields as appropriate."
            )
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
        interview_type = parsed.get("interview_type", skill_name)
        missing_required = parsed.get("missing_required") or []
        parts = [
            f"Interview session ready ({interview_type}).",
            f"fields={parsed.get('fields', {})}",
            f"missing_required={missing_required}",
            "Follow the interview SKILL procedure for the user's message.",
        ]
        if missing_required:
            parts.append(
                "New session: call interview__next_question before asking the user "
                "any field question via reply."
            )
        if parsed.get("post_tools_results"):
            parts.append(f"post_tools_results={parsed['post_tools_results']}")
        if parsed.get("skip_to_review"):
            parts.append("skip_to_review=true. Call interview__review next.")
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
                if sd is None and owner == TASK_OWNER_ACTION:
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
