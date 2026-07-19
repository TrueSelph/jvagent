"""InterviewAction — tool-driven interview runtime for orchestrator skills."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.action.parameters import SCOPE_ORCHESTRATION, SCOPE_RESPONSE

from . import engine, tasks
from .hooks import clear_module_cache, load_hook_function
from .session import InterviewSession
from .spec import (
    INTERVIEW_FRONTMATTER_KEY,
    InterviewRegistry,
    InterviewSpec,
)
from .tools import build_tools

logger = logging.getLogger(__name__)

INTERVIEW_CORE_PARAMETERS = [
    {
        "scope": SCOPE_ORCHESTRATION,
        "condition": (
            "conducting an interview or asking the user questions on behalf of "
            "a skill"
        ),
        "response": (
            "Stay in the interview's persona and purpose; do not volunteer "
            "information beyond what the interview prompts for, do not skip "
            "or fabricate answers, and do not break out of the flow."
        ),
    },
    {
        "scope": SCOPE_RESPONSE,
        "condition": "responding while an interview session is active",
        "response": (
            "Keep replies focused on the current interview question; do not "
            "offer unrelated help or switch topics until the interview completes "
            "or is cancelled."
        ),
    },
    {
        "scope": SCOPE_RESPONSE,
        "condition": (
            "the user refuses, hesitates, or says they don't want to provide a "
            "required piece of information during an interview"
        ),
        "response": (
            "Let the user know the detail is required to continue. On the "
            "first refusal, just acknowledge and re-ask — don't mention cancel. "
            "Only offer cancel as an option after the user has refused more than "
            "once or seems stuck on the required question. Keep it light, don't "
            "push cancel."
        ),
    },
]


class InterviewAction(Action):
    """Provides interview tools for LLM-driven multi-turn flows."""

    description: str = (
        "Interview action that provides granular tools for conducting "
        "interviews. The LLM decides which tools to call at each step based on "
        "the interview spec and SKILL.md procedure."
    )
    binds_tools_to_visitor: bool = True
    parameters: List[Dict[str, Any]] = attribute(
        default_factory=list,
        description=(
            "Interview-scoped parameters are injected dynamically when a "
            "session is active (via _inject_spec_parameters), not accumulated "
            "every turn by the orchestrator."
        ),
    )

    def get_capabilities(self) -> List[str]:
        return [self.description] if self.description else []

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._registry = InterviewRegistry()

    # -- discovery ----------------------------------------------------------

    async def on_register(self):
        await super().on_register()
        # Declare the ``interview__`` tool namespace as a trusted directive
        # source — interview tool results carry ``next_tool`` /
        # ``response_directive`` (hooks.py). Dependency inversion keeps the
        # orchestrator free of interview literals.
        try:
            from jvagent.action.orchestrator.constants import (
                register_trusted_directive_prefix,
            )

            register_trusted_directive_prefix("interview__")
        except Exception:  # pragma: no cover - orchestrator optional at load
            pass
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
        for_each_staged: Optional[Dict[str, Dict[str, str]]] = None,
        **kwargs: Any,
    ) -> str:
        return await engine.handle_set_fields(
            self, fields, visitor, for_each_staged=for_each_staged, **kwargs
        )

    async def _handle_next_field(self, visitor: Any = None) -> str:
        return await engine.handle_next_field(self, visitor)

    async def _handle_skip_field(self, field: str, visitor: Any = None) -> str:
        return await engine.handle_skip_field(self, field, visitor)

    async def _handle_get_status(self, visitor: Any = None, **_: Any) -> str:
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

    async def _close_task(
        self,
        visitor: Any = None,
        status: str = "completed",
        spec_name: Optional[str] = None,
    ) -> None:
        await tasks.close_task(visitor, status=status, spec_name=spec_name)

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

    def is_interview_skill(self, skill_name: str) -> bool:
        return bool(self._registry.get(skill_name))

    async def resolve_task_lock_skill(
        self, visitor: Any, skill_docs: List[Any]
    ) -> Optional[Any]:
        """Prefer the task-lock skill matching the active interview session.

        Generic hook called by the orchestrator's task-lock resolver so a live
        session in conversation context wins over a stale TaskStore task.
        """
        session = await self._get_session(visitor)
        if session is None or not session.is_active():
            return None
        for doc in skill_docs:
            if getattr(doc, "name", None) == session.interview_type:
                return doc
        return None

    async def _has_ready_session(self, skill_name: str, visitor: Any = None) -> bool:
        """True when an active session for ``skill_name`` exists and its spec loads."""
        await self._ensure_specs_loaded()
        session = await self._get_session(visitor)
        if (
            session is None
            or not session.is_active()
            or session.interview_type != skill_name
        ):
            return False
        return self._registry.get(session.interview_type) is not None

    async def needs_task_lock_rebootstrap(
        self, skill_name: str, visitor: Any = None
    ) -> bool:
        """Task-lock hook: the runtime needs bootstrapping when no ready session exists."""
        return not await self._has_ready_session(skill_name, visitor)

    async def task_lock_runtime_ready(
        self, skill_name: str, visitor: Any = None
    ) -> bool:
        """Task-lock hook: the runtime is ready only with an active session + loaded spec.

        Gates whether the orchestrator may surface this skill's tools; a missing
        spec or absent session keeps the surface to reply/respond only.
        """
        return await self._has_ready_session(skill_name, visitor)

    async def prepare_task_lock_turn(self, skill_name: str, visitor: Any = None):
        """Task-lock hook: re-ground the model each locked turn.

        Activation surfaces ``field_reference`` once in the ``use_skill``
        observation; PROCEDURE lives in system ``skills_section`` (not under
        Steps taken this turn). On a resumed turn the lock restricts the surface
        and the activation observation may have aged out of history, so the model
        loses the field catalog and guesses keys (e.g. ``full_name`` instead of
        ``user_name``) — failed extractions and reprompting. Re-injecting the
        current status (catalog + pending field + collected/skipped) as a
        server-prep observation keeps key selection grounded without re-running
        ``use_skill``.

        The orchestrator skips appending these prep observations when the same
        turn already carries the activation catalog (``use_skill`` or
        skill-session note), so ``interview_type`` / ``field_reference`` are not
        duplicated under Steps taken this turn.
        """
        from jvagent.action.skill_spec.task_lock import TaskLockPrep

        if not await self._has_ready_session(skill_name, visitor):
            return TaskLockPrep()
        try:
            status = await engine.interview_turn_status(self, visitor)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("InterviewAction.prepare_task_lock_turn failed: %s", exc)
            return TaskLockPrep()
        if not status:
            return TaskLockPrep()
        return TaskLockPrep(
            observations=[
                {
                    "tool": "interview__get_status",
                    "args": {},
                    "observation": status,
                    "kind": "server_prep",
                }
            ]
        )

    async def task_lock_entry_directive(
        self, skill_name: str, visitor: Any = None
    ) -> Optional[str]:
        """Task-lock hook (ADR-0026): the terminal directive to deliver when this
        skill is *entered as a pushed prerequisite* mid-turn.

        Returns its first pending field's ``Tell the user or ask the user:`` prompt so the detour
        asks the user and the orchestrator ends the turn. Without this the model is
        handed a fillable field reference and fabricates the answer itself (racing
        straight past the gate). Returns ``None`` if there is no ready session or no
        pending field, in which case the orchestrator falls back to its own egress.
        """
        if not await self._has_ready_session(skill_name, visitor):
            return None
        # Advance, server-side, past any field that auto-resolves to a tool-call
        # chain — e.g. a pre_processor that fills its own field and returns
        # ``Call interview__next_field()`` — to the first field that has a real
        # user-facing question. Delivering the chain directive itself would leak
        # "Call interview__next_field()" to the user; resolving it here yields the
        # actual next prompt. Bounded so a misbehaving chain can't spin.
        directive: Optional[str] = None
        for _ in range(8):
            try:
                data = json.loads(await engine.handle_next_field(self, visitor))
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug(
                    "task_lock_entry_directive failed for %s: %s", skill_name, exc
                )
                return None
            directive = data.get("response_directive")
            chains_to_next_field = data.get("next_tool") == "interview__next_field" or (
                isinstance(directive, str)
                and directive.strip().lower().startswith("call interview__next_field")
            )
            if not chains_to_next_field:
                break
        if isinstance(directive, str) and directive.strip():
            return directive
        return None

    async def gated_resume_auto_resolves(
        self, skill_name: str, visitor: Any = None
    ) -> bool:
        """Task-lock hook (ADR-0026): True when this skill's next pending field
        resolves server-side on activation — a ``pre_processor`` fills it, or a
        declarative ``validator_args.seed_from_activation`` match does.

        The gated-resume drain uses this to pick the resume style. Server-driven
        (run the activation, deliver its resolved question terminally) is correct
        and deterministic when the first field auto-resolves; model-driven (hand
        the model the original request to extract from) is only needed when that
        field genuinely requires model extraction. Without this, a
        pre_processor-filled first field — one whose activation fills it from the
        original request (and may run side effects such as a lookup or submission)
        — is silently skipped whenever the model narrates a status line instead of
        invoking the field tool.
        """
        if not await self._has_ready_session(skill_name, visitor):
            return False
        session = await self._get_session(visitor)
        spec = self._registry.get(skill_name)
        if session is None or spec is None:
            return False
        for fdef in spec.fields:
            if not fdef.required:
                continue
            if str(session.get_value(fdef.key) or "").strip():
                continue
            # First pending required field: does activation resolve it server-side?
            if fdef.pre_processor:
                return True
            return bool((fdef.validator_args or {}).get("seed_from_activation"))
        return False

    async def snapshot_task_state(
        self, skill_name: str, visitor: Any = None
    ) -> Dict[str, Any]:
        """Task-lock hook (ADR-0026): a durable snapshot of this skill's runtime, so
        the live session may be torn down during a detour and rebuilt on resume.
        Returns the serialized interview session for ``skill_name``, or ``{}``."""
        session = await self._get_session(visitor)
        if session is None or session.interview_type != skill_name:
            return {}
        try:
            return session.to_dict()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("snapshot_task_state failed for %s: %s", skill_name, exc)
            return {}

    async def rehydrate_from_task(
        self, skill_name: str, snapshot: Dict[str, Any], visitor: Any = None
    ) -> bool:
        """Task-lock hook (ADR-0026): rebuild the interview session from a task
        snapshot when no live session exists, instead of starting fresh. Returns
        True if a session was rehydrated."""
        if not snapshot:
            return False
        await self._ensure_specs_loaded()
        if self._registry.get(skill_name) is None:
            return False
        if await self._has_ready_session(skill_name, visitor):
            return False  # a live session already exists — nothing to rebuild
        try:
            session = InterviewSession.from_dict(snapshot)
        except Exception as exc:
            logger.debug(
                "rehydrate_from_task: bad snapshot for %s: %s", skill_name, exc
            )
            return False
        if session.interview_type != skill_name:
            return False
        await self._save_session(session, visitor)
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
        return json.dumps(parsed)
