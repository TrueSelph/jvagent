"""TaskStore-driven skill lifecycle helpers for the orchestrator.

Generic skill turn-lock, activation hooks (via ``requires-actions`` binding),
and auto-start pending resolution — no domain-specific interview logic here.
"""

from __future__ import annotations

import fnmatch
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


_SKILL_LIFECYCLE_METHODS = frozenset(
    {
        "on_skill_activate",
        "prepare_task_lock_turn",
        "task_lock_runtime_ready",
        "needs_task_lock_rebootstrap",
        "resolve_task_lock_skill",
    }
)


def _action_class_name(action: Any) -> str:
    if hasattr(action, "get_class_name"):
        try:
            name = action.get_class_name()
            if name:
                return name
        except Exception:
            pass
    return type(action).__name__


def _action_ref(action: Any) -> Optional[str]:
    ref_fn = getattr(action, "get_action_ref", None)
    if not callable(ref_fn):
        return None
    try:
        return ref_fn()
    except Exception:
        return None


def _requires_action_names(doc: Any) -> Tuple[str, ...]:
    return tuple(
        str(r).strip()
        for r in (getattr(doc, "requires_actions", ()) or ())
        if str(r).strip()
    )


def _enabled_matching_actions(doc: Any, actions: List[Any]) -> List[Any]:
    wanted = set(_requires_action_names(doc))
    if not wanted:
        return []
    return [
        action
        for action in enabled_actions(actions)
        if _action_class_name(action) in wanted
    ]


def _bind_by_extends(doc: Any, actions: List[Any]) -> Optional[Any]:
    extends = getattr(doc, "extends", None)
    if not extends or not str(extends).startswith("action:"):
        return None
    target_ref = str(extends)[len("action:") :].strip()
    if not target_ref:
        return None
    for action in enabled_actions(actions):
        ref = _action_ref(action)
        if ref and ref == target_ref:
            return action
    return None


def _implements_lifecycle(action: Any) -> bool:
    return any(
        callable(getattr(action, name, None)) for name in _SKILL_LIFECYCLE_METHODS
    )


def _bind_by_protocol(doc: Any, actions: List[Any]) -> Optional[Any]:
    matches = [
        action
        for action in _enabled_matching_actions(doc, actions)
        if _implements_lifecycle(action)
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        logger.warning(
            "skill_tasks: skill %r has ambiguous lifecycle binding among %s; "
            "add extends: action:… or reduce requires-actions",
            getattr(doc, "name", "?"),
            [_action_class_name(action) for action in matches],
        )
    return None


def _bind_by_requires_order(doc: Any, actions: List[Any]) -> Optional[Any]:
    by_class = {
        _action_class_name(action): action
        for action in _enabled_matching_actions(doc, actions)
    }
    for req_name in _requires_action_names(doc):
        action = by_class.get(req_name)
        if action is not None:
            return action
    return None


def action_for_skill(doc: Any, actions: List[Any]) -> Optional[Any]:
    """Resolve the Action that owns skill lifecycle hooks for ``doc``.

    ``requires-actions`` is a hard dependency gate (all must be enabled);
    binding picks which Action runs ``on_skill_activate``,
    ``prepare_task_lock_turn``, etc. Resolution order:

    1. ``extends: action:<namespace>/<action>`` ref match
    2. Sole lifecycle-protocol implementor among required actions
    3. First match in ``requires-actions`` declaration order
    """
    if not _requires_action_names(doc):
        return None
    bound = _bind_by_extends(doc, actions)
    if bound is not None:
        return bound
    bound = _bind_by_protocol(doc, actions)
    if bound is not None:
        return bound
    return _bind_by_requires_order(doc, actions)


def resolver_actions_for_task_lock_skills(
    skill_docs: List[Any], actions: List[Any]
) -> List[Any]:
    """Unique enabled actions bound to any ``task_lock`` skill via requires-actions."""
    seen: set[int] = set()
    out: List[Any] = []
    for doc in skill_docs:
        if not getattr(doc, "task_lock", False):
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


def _task_lock_skill_from_task_store(
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
        if not getattr(sd, "task_lock", False):
            continue
        updated_at = str(getattr(task, "updated_at", "") or "")
        candidates.append((updated_at, sd))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _task_lock_skill_from_auto_start(
    visitor: Any,
    skill_docs: List[Any],
    auto_start_names: List[str],
) -> Optional[Any]:
    """First task-lock auto-start skill with an active skill-named task."""
    if not auto_start_names:
        return None
    conversation = getattr(visitor, "conversation", None)
    store = task_store_for_conversation(conversation)
    if store is None:
        return None
    skill_by_name = {d.name: d for d in skill_docs if getattr(d, "name", None)}
    for name in auto_start_names:
        doc = skill_by_name.get(name)
        if doc is None or not getattr(doc, "task_lock", False):
            continue
        if is_skill_task_done(store, name):
            continue
        if has_active_skill_task(store, name):
            return doc
    return None


async def resolve_active_task_lock_skill(
    visitor: Any,
    skill_docs: List[Any],
    actions: List[Any],
    *,
    lock_active_flow: bool,
    auto_start_names: Optional[List[str]] = None,
) -> Optional[Any]:
    """Return the SkillDoc for an active task-lock skill, if any."""
    if not lock_active_flow:
        return None
    conversation = getattr(visitor, "conversation", None)
    if conversation is None:
        return None

    skill_by_name = {d.name: d for d in skill_docs if getattr(d, "name", None)}

    for action in resolver_actions_for_task_lock_skills(skill_docs, actions):
        resolve_fn = getattr(action, "resolve_task_lock_skill", None)
        if not callable(resolve_fn):
            continue
        try:
            result = await resolve_fn(visitor, skill_docs)
            if result is not None:
                return result
        except Exception as exc:
            logger.warning(
                "skill_tasks: resolve_task_lock_skill failed on %s: %s",
                type(action).__name__,
                exc,
            )

    doc = _task_lock_skill_from_task_store(conversation, skill_by_name)
    if doc is not None:
        return doc

    if auto_start_names:
        return _task_lock_skill_from_auto_start(visitor, skill_docs, auto_start_names)

    return None


async def ensure_task_lock_task(visitor: Any, doc: Any) -> None:
    """Create a SKILL TaskStore task for a task-lock skill if none is active."""
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
            "skill_tasks: failed to create task for task-lock skill %s: %s",
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
        if getattr(doc, "task_lock", False):
            await ensure_task_lock_task(visitor, doc)

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
        if bound is None or not hasattr(bound, "needs_task_lock_rebootstrap"):
            return False
        try:
            return bool(await bound.needs_task_lock_rebootstrap(doc.name, visitor))
        except Exception as exc:
            logger.warning(
                "skill_tasks: needs_task_lock_rebootstrap failed for %s via %s: %s",
                doc.name,
                type(bound).__name__,
                exc,
            )
            return False

    return _activate, _reactivate


async def ensure_task_lock_session(
    doc: Any,
    actions: List[Any],
    visitor: Any,
    *,
    user_message: str = "",
) -> Optional[str]:
    """Re-bootstrap bound-action runtime when a task-lock skill is active but not ready."""
    bound = action_for_skill(doc, actions)
    if bound is None or not hasattr(bound, "needs_task_lock_rebootstrap"):
        return None
    try:
        if hasattr(bound, "_ensure_specs_loaded"):
            await bound._ensure_specs_loaded()
        needs = await bound.needs_task_lock_rebootstrap(doc.name, visitor)
        if needs and hasattr(bound, "on_skill_activate"):
            note = await bound.on_skill_activate(
                doc.name,
                visitor,
                user_message=user_message,
            )
        else:
            note = None
        ready_fn = getattr(bound, "task_lock_runtime_ready", None)
        if callable(ready_fn):
            try:
                if await ready_fn(doc.name, visitor):
                    return note
            except Exception:
                pass
        if needs:
            logger.warning(
                "skill_tasks: task-lock skill %r runtime not ready after bootstrap",
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
            "skill_tasks: ensure_task_lock_session failed for %s via %s: %s",
            doc.name,
            type(bound).__name__,
            exc,
        )
    return None


@dataclass
class TaskLockPrep:
    """Optional bound-action output when a task-lock skill turn starts."""

    observations: List[Dict[str, Any]] = field(default_factory=list)
    runtime_ready: Optional[bool] = None
    pending_directive: Optional[str] = None


def task_lock_section_text(
    skill_doc: Any,
    *,
    pending_directive: Optional[str] = None,
    companion_names: Tuple[str, ...] = (),
) -> str:
    """Build the turn-lock PROCEDURE block surfaced to the model each tick."""
    if companion_names:
        comp = ", ".join(companion_names)
        lock_line = (
            "Turn-lock is ON. Stay on this skill. You MAY use these companion "
            f"capabilities to handle a side question: {comp}. After handling it, "
            "return to this skill and continue from its current step. Use only "
            "this skill's tools, the companions just listed, and reply/respond.\n"
        )
    else:
        lock_line = (
            "Turn-lock is ON — complete this skill before routing to any other "
            "capability. Use only the tools listed below plus reply/respond.\n"
        )
    header = f"ACTIVE SKILL IN PROGRESS: {skill_doc.name}\n{lock_line}"
    if pending_directive:
        header += f"{pending_directive}\n"
    return f"{header}PROCEDURE:\n{skill_doc.body}"


def resolve_lock_companions(
    skill_doc: Any, skill_docs: List[Any]
) -> Tuple[List[Any], List[str]]:
    """Split a locked skill's ``lock_companions`` into (companion SkillDocs, tool
    globs). A companion that is itself ``task_lock`` is rejected — it would seize
    the turn-lock from the active skill instead of returning control to it."""
    by_name = {d.name: d for d in skill_docs if getattr(d, "name", None)}
    skills: List[Any] = []
    globs: List[str] = []
    for entry in getattr(skill_doc, "lock_companions", ()) or ():
        name = str(entry).strip()
        if not name:
            continue
        doc = by_name.get(name)
        if doc is not None:
            if getattr(doc, "task_lock", False):
                logger.warning(
                    "skill_tasks: companion %r of %r is task_lock; ignoring "
                    "(a companion must not seize the turn-lock)",
                    name,
                    getattr(skill_doc, "name", "?"),
                )
                continue
            skills.append(doc)
        else:
            globs.append(name)
    return skills, globs


def _companion_surface(
    companion_skills: List[Any],
    companion_tool_globs: List[str],
    tools: Dict[str, Any],
) -> Tuple[Set[str], List[str]]:
    """Resolve companions to (allowed tool names, human display names).

    Companion skills contribute their ``requires_tools`` plus ``use_skill`` (so
    the model can activate them mid-lock); tool globs match the live surface.
    """
    allowed: Set[str] = set()
    display: List[str] = []
    for doc in companion_skills:
        allowed.update(getattr(doc, "requires_tools", ()) or ())
        display.append(doc.name)
    if companion_skills:
        allowed.add("use_skill")
    for glob in companion_tool_globs:
        matched = [t for t in tools if fnmatch.fnmatch(t, glob)]
        allowed.update(matched)
        display.extend(matched or [glob])
    return allowed, display


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


def restrict_tools_to_task_lock_skill(
    skill_doc: Any,
    tools: Dict[str, Any],
    visible: Set[str],
    activated: List[str],
    *,
    pending_directive: Optional[str] = None,
    companion_skills: Optional[List[Any]] = None,
    companion_tool_globs: Optional[List[str]] = None,
) -> Tuple[Dict[str, Any], Set[str], str]:
    """Restrict the callable surface to a task-lock skill's tools + egress, plus
    any whitelisted companion capabilities (so a locked skill can field a side
    question and return to its step)."""
    if skill_doc.name not in activated:
        activated.append(skill_doc.name)
    allowed_names = set(getattr(skill_doc, "requires_tools", ()) or ())
    allowed_names.update({"reply", "respond"})
    companion_allowed, companion_display = _companion_surface(
        companion_skills or [], companion_tool_globs or [], tools
    )
    allowed_names.update(companion_allowed)
    restricted_tools = {k: v for k, v in tools.items() if k in allowed_names}
    restricted_visible = {k for k in visible if k in allowed_names}
    restricted_visible.update(k for k in allowed_names if k in restricted_tools)
    skills_section = task_lock_section_text(
        skill_doc,
        pending_directive=pending_directive,
        companion_names=tuple(dict.fromkeys(companion_display)),
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


def _append_session_note(observations: List[Dict[str, Any]], note: str) -> None:
    """Record a server-injected skill-session note (visualized via server_prep)."""
    observations.append(
        {
            "tool": "(skill-session)",
            "args": {},
            "observation": note,
            "kind": "server_prep",
        }
    )


async def apply_task_lock_turn(
    skill_doc: Any,
    actions: List[Any],
    visitor: Any,
    *,
    user_message: str,
    tools: Dict[str, Any],
    visible: Set[str],
    activated: List[str],
    observations: List[Dict[str, Any]],
    skill_docs: Optional[List[Any]] = None,
) -> Tuple[Dict[str, Any], Set[str], str]:
    """Session bootstrap + bound-action prep + turn-lock tool restriction."""
    note = await ensure_task_lock_session(
        skill_doc,
        actions,
        visitor,
        user_message=user_message,
    )
    if note:
        _append_session_note(observations, note)

    bound = action_for_skill(skill_doc, actions)
    runtime_ready = True
    pending_directive: Optional[str] = None

    if bound is not None and hasattr(bound, "task_lock_runtime_ready"):
        try:
            runtime_ready = await bound.task_lock_runtime_ready(skill_doc.name, visitor)
        except Exception:
            runtime_ready = False

    if bound is not None and hasattr(bound, "prepare_task_lock_turn"):
        try:
            prep = await bound.prepare_task_lock_turn(skill_doc.name, visitor)
            if prep.observations:
                for ob in prep.observations:
                    if isinstance(ob, dict):
                        ob.setdefault("kind", "server_prep")
                observations.extend(prep.observations)
            if prep.runtime_ready is not None:
                runtime_ready = prep.runtime_ready
            if prep.pending_directive:
                pending_directive = prep.pending_directive
        except Exception as exc:
            logger.warning(
                "skill_tasks: prepare_task_lock_turn failed for %s via %s: %s",
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

    companion_skills, companion_tool_globs = resolve_lock_companions(
        skill_doc, skill_docs or []
    )
    return restrict_tools_to_task_lock_skill(
        skill_doc,
        tools,
        visible,
        activated,
        pending_directive=pending_directive,
        companion_skills=companion_skills,
        companion_tool_globs=companion_tool_globs,
    )


async def prune_task_lock_tools_for_actions(
    actions: List[Any],
    visitor: Any,
    tools: Dict[str, Any],
    visible: Set[str],
) -> None:
    """Let bound actions drop tools when task-lock runtime is not ready."""
    for action in enabled_actions(actions):
        prune_fn = getattr(action, "prune_task_lock_tools", None)
        if not callable(prune_fn):
            continue
        try:
            await prune_fn(tools, visible, visitor)
        except Exception as exc:
            logger.warning(
                "skill_tasks: prune_task_lock_tools failed on %s: %s",
                type(action).__name__,
                exc,
            )
