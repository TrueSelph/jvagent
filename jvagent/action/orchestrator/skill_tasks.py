"""TaskStore-driven skill lifecycle helpers for the orchestrator.

Generic skill turn-lock, activation hooks (via ``requires-actions`` binding),
and auto-start pending resolution — no domain-specific interview logic here.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
)

if TYPE_CHECKING:
    from jvagent.action.orchestrator.skills import SkillDoc

logger = logging.getLogger(__name__)

_ACTIVE_SKILL_STATUSES = frozenset({"pending", "active"})


def task_store_for_conversation(conversation: Any) -> Optional[Any]:
    if conversation is None:
        return None
    try:
        from jvagent.memory.task_store import TaskStore

        return TaskStore(conversation)
    except Exception as exc:
        logger.debug("skill_tasks: TaskStore unavailable: %s", exc)
        return None


def tasks_for_skill(store: Any, skill_name: str) -> List[Any]:
    if store is None or not skill_name:
        return []
    try:
        return store.list(owner_action=skill_name) or []
    except Exception as exc:
        logger.debug("skill_tasks: list tasks for %r failed: %s", skill_name, exc)
        return []


def _task_updated_at(handle: Any) -> str:
    task = getattr(handle, "_task", None)
    if task is not None:
        return str(getattr(task, "updated_at", "") or "")
    return str(getattr(handle, "updated_at", "") or "")


def _task_status(handle: Any) -> str:
    task = getattr(handle, "_task", None)
    if task is not None:
        return str(getattr(task, "status", "") or "")
    return str(getattr(handle, "status", "") or "")


def is_skill_task_done(store: Any, skill_name: str) -> bool:
    """True only when a skill-named task has reached ``completed``."""
    return any(
        _task_status(t) == "completed" for t in tasks_for_skill(store, skill_name)
    )


def has_active_skill_task(store: Any, skill_name: str) -> bool:
    return any(
        _task_status(t) in _ACTIVE_SKILL_STATUSES
        for t in tasks_for_skill(store, skill_name)
    )


def pending_auto_start_skills(store: Any, skill_names: List[str]) -> List[str]:
    """Skill names still pending (no completed task), in config order."""
    return [n for n in skill_names if n and not is_skill_task_done(store, n)]


def enabled_actions(actions: List[Any]) -> List[Any]:
    return [a for a in actions if getattr(a, "enabled", True)]


def action_for_skill(doc: Any, actions: List[Any]) -> Optional[Any]:
    """First enabled action whose class name appears in ``doc.requires_actions``."""
    wanted = {
        str(r).strip()
        for r in (getattr(doc, "requires_actions", ()) or ())
        if str(r).strip()
    }
    if not wanted:
        return None
    for action in enabled_actions(actions):
        class_name = None
        if hasattr(action, "get_class_name"):
            try:
                class_name = action.get_class_name()
            except Exception:
                class_name = None
        if not class_name:
            class_name = type(action).__name__
        if class_name in wanted:
            return action
    return None


def resolver_actions_for_locked_skills(
    skill_docs: List[Any], actions: List[Any]
) -> List[Any]:
    """Unique enabled actions bound to any ``locked_in`` skill via requires-actions."""
    seen: set[int] = set()
    out: List[Any] = []
    for doc in skill_docs:
        if not getattr(doc, "locked_in", False):
            continue
        action = action_for_skill(doc, actions)
        if action is None:
            continue
        key = id(action)
        if key in seen:
            continue
        seen.add(key)
        out.append(action)
    return out


def visitor_utterance(visitor: Any) -> str:
    for attr in ("utterance", "message", "text"):
        val = getattr(visitor, attr, None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    interaction = getattr(visitor, "interaction", None)
    if interaction is not None:
        for attr in ("utterance", "message", "text"):
            val = getattr(interaction, attr, None)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def _locked_skill_from_task_store(
    conversation: Any, skill_by_name: dict[str, Any]
) -> Optional[Any]:
    store = task_store_for_conversation(conversation)
    if store is None:
        return None
    try:
        active_tasks = store.list(status="active")
    except Exception as exc:
        logger.debug("skill_tasks: failed to list active tasks: %s", exc)
        return None

    candidates: List[tuple[str, Any]] = []
    for task in active_tasks or []:
        owner = getattr(task, "owner_action", None)
        if not owner or owner not in skill_by_name:
            continue
        sd = skill_by_name[owner]
        if not getattr(sd, "locked_in", False):
            continue
        updated_at = str(getattr(task, "updated_at", "") or "")
        candidates.append((updated_at, sd))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _locked_skill_from_auto_start(
    visitor: Any,
    skill_docs: List[Any],
    auto_start_names: List[str],
) -> Optional[Any]:
    """First locked_in auto-start skill with an active skill-named task."""
    if not auto_start_names:
        return None
    conversation = getattr(visitor, "conversation", None)
    store = task_store_for_conversation(conversation)
    if store is None:
        return None
    skill_by_name = {d.name: d for d in skill_docs if getattr(d, "name", None)}
    for name in auto_start_names:
        doc = skill_by_name.get(name)
        if doc is None or not getattr(doc, "locked_in", False):
            continue
        if is_skill_task_done(store, name):
            continue
        if has_active_skill_task(store, name):
            return doc
    return None


async def resolve_active_locked_skill(
    visitor: Any,
    skill_docs: List[Any],
    actions: List[Any],
    *,
    lock_active_flow: bool,
    auto_start_names: Optional[List[str]] = None,
) -> Optional[Any]:
    """Return the SkillDoc for an active locked_in skill, if any."""
    if not lock_active_flow:
        return None
    conversation = getattr(visitor, "conversation", None)
    if conversation is None:
        return None

    skill_by_name = {d.name: d for d in skill_docs if getattr(d, "name", None)}

    for action in resolver_actions_for_locked_skills(skill_docs, actions):
        resolve_fn = getattr(action, "resolve_locked_skill", None)
        if not callable(resolve_fn):
            continue
        try:
            result = await resolve_fn(visitor, skill_docs)
            if result is not None:
                return result
        except Exception as exc:
            logger.warning(
                "skill_tasks: resolve_locked_skill failed on %s: %s",
                type(action).__name__,
                exc,
            )

    doc = _locked_skill_from_task_store(conversation, skill_by_name)
    if doc is not None:
        return doc

    if auto_start_names:
        return _locked_skill_from_auto_start(visitor, skill_docs, auto_start_names)

    return None


async def ensure_locked_skill_task(visitor: Any, doc: Any) -> None:
    """Create a SKILL TaskStore task for a locked_in skill if none is active."""
    conversation = getattr(visitor, "conversation", None)
    if conversation is None:
        return
    store = task_store_for_conversation(conversation)
    if store is None:
        return
    if has_active_skill_task(store, doc.name):
        return
    try:
        handle = await store.create(
            title=doc.name,
            description=doc.description or f"Executing skill {doc.name}",
            owner_action=doc.name,
            task_type="SKILL",
        )
        await handle.start()
    except Exception as exc:
        logger.warning(
            "skill_tasks: failed to create task for locked_in skill %s: %s",
            doc.name,
            exc,
        )


def compose_skill_activate_hooks(
    actions: List[Any], visitor: Any, code_exec: Optional[Any]
) -> Tuple[
    Optional[Callable[[Any], Awaitable[Optional[str]]]],
    Optional[Callable[[Any], Awaitable[bool]]],
]:
    """Build activate/reactivate hooks for catalog ``use_skill`` (requires-actions binding)."""

    async def _activate(doc: Any) -> Optional[str]:
        notes: List[str] = []
        if getattr(doc, "locked_in", False):
            await ensure_locked_skill_task(visitor, doc)

        bound = action_for_skill(doc, actions)
        if bound is not None and hasattr(bound, "on_skill_activate"):
            try:
                note = await bound.on_skill_activate(
                    doc.name,
                    visitor,
                    user_message=visitor_utterance(visitor),
                )
                if note:
                    notes.append(note)
            except Exception as exc:
                logger.warning(
                    "skill_tasks: on_skill_activate failed for %s via %s: %s",
                    doc.name,
                    type(bound).__name__,
                    exc,
                )
                notes.append(f"(skill activation error: {exc})")

        if getattr(doc, "spec", "jv") == "claude" and code_exec is not None:
            directory = getattr(doc, "directory", "") or ""
            if directory:
                try:
                    rel = await code_exec.stage_skill(visitor, directory, doc.name)
                    notes.append(
                        f"This skill's files are staged at '{rel}/' in your sandbox. Run "
                        f"its scripts with the code_execution__bash tool — e.g. "
                        f"`python {rel}/scripts/<script>.py`. Read bundled files there "
                        f"(e.g. `cat {rel}/reference.md`) only as needed."
                    )
                except Exception as exc:
                    notes.append(f"(could not stage skill files: {exc})")

        return "\n\n".join(notes) if notes else None

    async def _reactivate(doc: Any) -> bool:
        bound = action_for_skill(doc, actions)
        if bound is None or not hasattr(bound, "needs_session_rebootstrap"):
            return False
        try:
            return bool(await bound.needs_session_rebootstrap(doc.name, visitor))
        except Exception as exc:
            logger.warning(
                "skill_tasks: needs_session_rebootstrap failed for %s via %s: %s",
                doc.name,
                type(bound).__name__,
                exc,
            )
            return False

    return _activate, _reactivate


async def ensure_locked_skill_session(
    doc: Any,
    actions: List[Any],
    visitor: Any,
    *,
    user_message: str = "",
) -> Optional[str]:
    """Re-bootstrap bound-action runtime when a locked skill is active but not ready."""
    bound = action_for_skill(doc, actions)
    if bound is None or not hasattr(bound, "needs_session_rebootstrap"):
        return None
    try:
        if hasattr(bound, "_ensure_specs_loaded"):
            await bound._ensure_specs_loaded()
        needs = await bound.needs_session_rebootstrap(doc.name, visitor)
        if needs and hasattr(bound, "on_skill_activate"):
            note = await bound.on_skill_activate(
                doc.name,
                visitor,
                user_message=user_message,
            )
        else:
            note = None
        ready_fn = getattr(bound, "skill_runtime_ready", None)
        if callable(ready_fn):
            try:
                if await ready_fn(doc.name, visitor):
                    return note
            except Exception:
                pass
        if needs:
            logger.warning(
                "skill_tasks: locked skill %r runtime not ready after bootstrap",
                doc.name,
            )
            return note or (
                f"Could not prepare runtime for skill {doc.name}. "
                "Reply to the user only until activation confirms skill tools "
                "are callable."
            )
        return note
    except Exception as exc:
        logger.warning(
            "skill_tasks: ensure_locked_skill_session failed for %s via %s: %s",
            doc.name,
            type(bound).__name__,
            exc,
        )
    return None


@dataclass
class LockedSkillPrep:
    """Optional bound-action output when a locked skill turn starts."""

    observations: List[Dict[str, Any]] = field(default_factory=list)
    runtime_ready: Optional[bool] = None
    pending_directive: Optional[str] = None


def locked_skills_section_text(
    skill_doc: Any, *, pending_directive: Optional[str] = None
) -> str:
    """Build the turn-lock PROCEDURE block surfaced to the model each tick."""
    header = (
        f"ACTIVE SKILL IN PROGRESS: {skill_doc.name}\n"
        "Turn-lock is ON — complete this skill before routing to any other "
        "capability. Use only the tools listed below plus reply/respond.\n"
    )
    if pending_directive:
        header += f"{pending_directive}\n"
    return f"{header}PROCEDURE:\n{skill_doc.body}"


_STALE_PREP_OBSERVATION_TOOLS = frozenset(
    {"interview__message_evaluation", "interview__next_question"}
)


def drop_stale_locked_skill_prep_observations(
    observations: List[Dict[str, Any]],
) -> None:
    """Remove server-injected prep the model must not re-follow after a store."""
    observations[:] = [
        o for o in observations if o.get("tool") not in _STALE_PREP_OBSERVATION_TOOLS
    ]


async def ensure_skill_tools_materialized(
    skill_doc: Any,
    actions: List[Any],
    visitor: Any,
    tools: Dict[str, Any],
    visible: Set[str],
) -> None:
    """Re-add bound-action tools pruned before the interview session was ready."""
    required = set(getattr(skill_doc, "requires_tools", ()) or ())
    missing = {name for name in required if name not in tools}
    if not missing:
        return
    from jvagent.action.orchestrator.tools import wrap_action_tool

    for action in enabled_actions(actions):
        get_tools = getattr(action, "get_tools", None)
        if not callable(get_tools):
            continue
        try:
            result = get_tools()
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            logger.debug(
                "skill_tasks: ensure_skill_tools_materialized get_tools failed: %s",
                exc,
            )
            continue
        wrap_visitor = (
            visitor if getattr(action, "binds_tools_to_visitor", False) else None
        )
        for tool in result or []:
            name = getattr(tool, "name", None)
            if name and name in missing:
                tools[name] = wrap_action_tool(tool, visitor=wrap_visitor)
                visible.add(name)
                missing.discard(name)
        if not missing:
            return


async def refresh_locked_skill_prep(
    skill_doc: Any,
    actions: List[Any],
    visitor: Any,
    observations: List[Dict[str, Any]],
) -> Optional[str]:
    """Re-run bound-action prep after a successful interview store."""
    bound = action_for_skill(skill_doc, actions)
    if bound is None or not hasattr(bound, "prepare_locked_skill_turn"):
        return None
    drop_stale_locked_skill_prep_observations(observations)
    try:
        prep = await bound.prepare_locked_skill_turn(skill_doc.name, visitor)
    except Exception as exc:
        logger.warning(
            "skill_tasks: refresh_locked_skill_prep failed for %s via %s: %s",
            skill_doc.name,
            type(bound).__name__,
            exc,
        )
        return None
    if prep.observations:
        observations.extend(prep.observations)
    return prep.pending_directive


def restrict_tools_to_locked_skill(
    skill_doc: Any,
    tools: Dict[str, Any],
    visible: Set[str],
    activated: List[str],
    *,
    pending_directive: Optional[str] = None,
) -> Tuple[Dict[str, Any], Set[str], str]:
    """Restrict the callable surface to a locked skill's declared tools + egress."""
    if skill_doc.name not in activated:
        activated.append(skill_doc.name)
    allowed_names = set(getattr(skill_doc, "requires_tools", ()) or ())
    allowed_names.update({"reply", "respond"})
    restricted_tools = {k: v for k, v in tools.items() if k in allowed_names}
    restricted_visible = {k for k in visible if k in allowed_names}
    restricted_visible.update(k for k in allowed_names if k in restricted_tools)
    skills_section = locked_skills_section_text(
        skill_doc, pending_directive=pending_directive
    )
    return restricted_tools, restricted_visible, skills_section


def _reply_only_surface(
    skill_doc: Any,
    tools: Dict[str, Any],
    visible: Set[str],
    *,
    pending_directive: str,
) -> Tuple[Dict[str, Any], Set[str], str]:
    allowed_names = {"reply", "respond"}
    restricted_tools = {k: v for k, v in tools.items() if k in allowed_names}
    restricted_visible = {k for k in visible if k in allowed_names}
    skills_section = (
        f"ACTIVE SKILL IN PROGRESS: {skill_doc.name}\n"
        f"{pending_directive}\n"
        f"PROCEDURE:\n{skill_doc.body}"
    )
    return restricted_tools, restricted_visible, skills_section


async def apply_locked_skill_turn(
    skill_doc: Any,
    actions: List[Any],
    visitor: Any,
    *,
    user_message: str,
    tools: Dict[str, Any],
    visible: Set[str],
    activated: List[str],
    observations: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Set[str], str]:
    """Session bootstrap + bound-action prep + turn-lock tool restriction."""
    note = await ensure_locked_skill_session(
        skill_doc,
        actions,
        visitor,
        user_message=user_message,
    )
    if note:
        observations.append(
            {
                "tool": "(skill-session)",
                "args": {},
                "observation": note,
            }
        )

    bound = action_for_skill(skill_doc, actions)
    runtime_ready = True
    pending_directive: Optional[str] = None

    if bound is not None and hasattr(bound, "skill_runtime_ready"):
        try:
            runtime_ready = await bound.skill_runtime_ready(skill_doc.name, visitor)
        except Exception:
            runtime_ready = False

    if bound is not None and hasattr(bound, "prepare_locked_skill_turn"):
        try:
            prep = await bound.prepare_locked_skill_turn(skill_doc.name, visitor)
            if prep.observations:
                observations.extend(prep.observations)
            if prep.runtime_ready is not None:
                runtime_ready = prep.runtime_ready
            if prep.pending_directive:
                pending_directive = prep.pending_directive
        except Exception as exc:
            logger.warning(
                "skill_tasks: prepare_locked_skill_turn failed for %s via %s: %s",
                skill_doc.name,
                type(bound).__name__,
                exc,
            )
            runtime_ready = False

    if not runtime_ready:
        directive = pending_directive or (
            f"Runtime for skill {skill_doc.name} is not ready — reply to the "
            "user only; do not call skill tools until activation confirms they "
            "are callable."
        )
        logger.warning(
            "skill_tasks: turn-lock for %r without ready runtime — reply/respond only",
            skill_doc.name,
        )
        return _reply_only_surface(
            skill_doc, tools, visible, pending_directive=directive
        )

    await ensure_skill_tools_materialized(skill_doc, actions, visitor, tools, visible)

    return restrict_tools_to_locked_skill(
        skill_doc, tools, visible, activated, pending_directive=pending_directive
    )


async def prune_turn_tools_for_actions(
    actions: List[Any],
    visitor: Any,
    tools: Dict[str, Any],
    visible: Set[str],
) -> None:
    """Let bound actions drop turn tools when their runtime is not ready."""
    for action in enabled_actions(actions):
        prune_fn = getattr(action, "prune_turn_tools", None)
        if not callable(prune_fn):
            continue
        try:
            await prune_fn(tools, visible, visitor)
        except Exception as exc:
            logger.warning(
                "skill_tasks: prune_turn_tools failed on %s: %s",
                type(action).__name__,
                exc,
            )


# Back-compat aliases (tests / callers migrating from onboard.py)
has_active_onboard_task = has_active_skill_task
is_onboard_skill_done = is_skill_task_done
pending_onboard_skills = pending_auto_start_skills


def resolve_onboard_locked_skill_doc(
    visitor: Any,
    skill_docs: List[Any],
    onboard_skill_names: List[str],
    *,
    lock_active_flow: bool,
) -> Optional[Any]:
    return (
        _locked_skill_from_auto_start(visitor, skill_docs, onboard_skill_names)
        if lock_active_flow
        else None
    )


def first_pending_locked_onboard_doc(
    skill_docs: List[Any],
    onboard_skill_names: List[str],
    store: Any,
) -> Optional["SkillDoc"]:
    skill_by_name = {d.name: d for d in skill_docs if getattr(d, "name", None)}
    for name in onboard_skill_names:
        doc = skill_by_name.get(name)
        if doc is None or not getattr(doc, "locked_in", False):
            continue
        if not is_skill_task_done(store, name):
            return doc
    return None
