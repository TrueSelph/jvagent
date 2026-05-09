"""Public embed surface for hosting jvagent inside another jvspatial process.

This module is the supported entry point for running jvagent as a library —
i.e. inside a host application that already owns the jvspatial ``Server`` and
its FastAPI app — instead of running jvagent's own CLI server.

The host is responsible for:

1. Constructing the jvspatial ``Server`` (or otherwise initializing the
   default jvspatial context with a ``Database``).
2. Calling :func:`bootstrap` once during application startup, after the
   default jvspatial context exists.
3. Mounting an interaction endpoint (or other surface) that calls into
   jvagent's interact subsystem with a host-resolved ``user_id``.

Stability contract
------------------

The names exported here (``bootstrap``, ``shutdown``, ``run_index_migration``,
``run_app_startup``) are part of jvagent's public, semver-tracked surface.
Internal helpers (anything starting with ``_``) are not.

Other modules under :mod:`jvagent` are considered internal until promoted
here. Hosts depending on internal modules must pin to an exact jvagent
version.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Union

from jvspatial.api.exceptions import (
    ResourceNotFoundError,
    ValidationError,
)

logger = logging.getLogger(__name__)


async def bootstrap(
    *,
    app_root: Optional[Union[str, Path]] = None,
    update_mode: Optional[str] = None,
    ensure_admin: bool = False,
    run_action_startup: bool = True,
) -> None:
    """Initialize jvagent inside an already-running jvspatial process.

    Runs the same startup sequence the standalone CLI server runs, minus
    HTTP server lifecycle. Safe to call multiple times — every step is
    idempotent.

    Order of operations:

    1. ``run_index_migration()`` — drop deprecated indexes, ensure current
       indexes for all jvagent entity classes (Conversation, Interaction,
       User, UserLongMemory, etc.) on the host's jvspatial database.
    2. ``bootstrap_application_graph()`` — if ``app_root/app.yaml`` exists,
       declaratively register the App + Agents + Actions described there.
       Otherwise registers a minimal App node only.
    3. ``run_app_startup()`` — invoke each registered Action's
       ``on_startup()`` hook (channel adapters, LM clients, schedulers).
    4. Optional ``ensure_admin_user()`` — create the admin user from
       ``JVAGENT_ADMIN_*`` env vars if absent. Hosts that own their own
       auth should leave this off.

    Args:
        app_root: Path to the directory containing ``app.yaml`` and the
            ``agent/`` tree. Defaults to the current working directory if
            omitted (matches CLI behavior). Pass an explicit path when
            embedding to avoid CWD surprises.
        update_mode: ``"merge"`` for non-destructive merge from YAML,
            ``"source"`` for destructive overwrite, or ``None`` to leave
            existing nodes untouched.
        ensure_admin: When True, ensure the admin user exists. Off by
            default — host-owned auth is the expected pattern in embed
            mode.
        run_action_startup: When True (default), invoke each Action's
            ``on_startup()`` hook. Set False for tests or for hosts that
            want to defer action initialization.

    Raises:
        Whatever the underlying bootstrap steps raise. Failures are
        logged with full context before propagation.
    """
    from jvagent.cli.bootstrap import bootstrap_application_graph
    from jvagent.core.app_context import set_app_root
    from jvagent.core.index_bootstrap import run_index_migration as _run_index_migration

    resolved_root = str(app_root) if app_root is not None else None

    logger.info(
        "jvagent embed bootstrap starting (app_root=%s, update_mode=%s, ensure_admin=%s)",
        resolved_root,
        update_mode,
        ensure_admin,
    )

    # Pin the global app_root used by app-config readers and the
    # SkillCatalog's filesystem walker. The standalone CLI sets this
    # from its working directory; under embed mode the host process's
    # cwd is unrelated to the agent app so we set it explicitly here.
    if resolved_root:
        set_app_root(resolved_root)

    await _run_index_migration()

    await bootstrap_application_graph(
        update_mode=update_mode,
        app_root=resolved_root,
    )

    if run_action_startup:
        from jvagent.core.startup import run_app_startup as _run_app_startup

        await _run_app_startup()

    if ensure_admin:
        from jvagent.cli.bootstrap import ensure_admin_user

        await ensure_admin_user()

    logger.info("jvagent embed bootstrap complete")


async def shutdown() -> None:
    """Stop background services started by :func:`bootstrap`.

    Currently shuts down the optional repair scheduler. Safe to call even
    if nothing was started. Hosts should call this from their FastAPI
    ``shutdown`` event handler.
    """
    from jvagent.core.startup import stop_repair_scheduler

    await stop_repair_scheduler()


async def run_index_migration() -> None:
    """Run jvagent's index migration on the current jvspatial context.

    Re-exported from :mod:`jvagent.core.index_bootstrap` for hosts that
    want to run only the schema step (e.g. during test fixtures).
    """
    from jvagent.core.index_bootstrap import run_index_migration as _impl

    await _impl()


async def run_app_startup() -> bool:
    """Run jvagent's per-Action ``on_startup()`` hooks.

    Re-exported from :mod:`jvagent.core.startup` for hosts that want to
    re-run startup after dynamically registering new Actions.
    """
    from jvagent.core.startup import run_app_startup as _impl

    return await _impl()


async def interact(
    *,
    agent_id: str,
    utterance: str,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    channel: str = "default",
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """In-process equivalent of ``POST /agents/{agent_id}/interact`` (non-streaming).

    Drives jvagent's full interact pipeline (Cockpit routing → InteractWalker
    traversal → response builder) without any HTTP layer. Hosts use this
    when they own their own request surface (auth, rate-limit, framing) and
    just want jvagent to produce a single consolidated agent response.

    Args:
        agent_id: jvspatial node id of the target Agent. Use
            :func:`get_agent_id_by_name` to resolve from a dotted
            ``namespace/name`` instead.
        utterance: User's input text. Must be non-empty.
        user_id: Optional user identifier. If omitted, jvagent creates an
            anonymous User on demand and returns the generated id in the
            response.
        session_id: Optional session id. Resume an existing Conversation, or
            pin a new Conversation to a host-supplied id. If omitted, jvagent
            generates one and returns it in the response.
        channel: Free-form channel hint passed through to InteractActions
            (``"default"`` matches the HTTP endpoint's web/empty fallback).
        data: Optional dictionary payload merged into the walker's ``data``
            field — surfaces in InteractActions as request-scoped context.

    Returns:
        Dict with the same shape as the HTTP endpoint's non-streaming
        response: ``{user_id, session_id, response, interaction?, report?}``.
        ``interaction`` and ``report`` are dev-mode-only (production filters
        them out per :func:`jvagent.action.interact.response_builder.build_interact_response`).

    Raises:
        ResourceNotFoundError: ``agent_id`` does not resolve.
        ValidationError: ``utterance`` is empty / whitespace-only or the
            walker fails to produce an Interaction.
        Anything the InteractWalker raises during traversal propagates
        unchanged so the host can apply its own error mapping.

    Notes:
        * **Rate limiting** is NOT applied here. Hosts that need it must
          enforce per-user / per-IP limits before calling.
        * **Streaming is not yet supported** through the embed surface. For
          progressive token delivery, fall back to jvagent's own SSE
          endpoint until a streaming embed callable lands.
    """
    if not utterance or not utterance.strip():
        raise ValidationError(
            message="utterance is required and cannot be empty",
            details={"utterance": utterance},
        )

    # Imports kept lazy so `import jvagent.embed` stays cheap and works even
    # in environments that haven't called `bootstrap()` yet.
    from jvspatial import flush_deferred_entities

    from jvagent.action.interact.endpoints import _finalize_usage
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.interact.response_builder import build_interact_response
    from jvagent.action.model.context import set_interaction
    from jvagent.core.cache import get_cached_agent
    from jvagent.core.channel import normalize_channel
    from jvagent.core.profiling import profiled_request, set_current_profile

    channel = normalize_channel(channel)

    async with profiled_request() as profile:
        set_current_profile(profile)
        try:
            agent = await get_cached_agent(agent_id)
            if not agent:
                raise ResourceNotFoundError(
                    message=f"Agent with ID '{agent_id}' not found",
                    details={"agent_id": agent_id},
                )

            walker = InteractWalker(
                agent_id=agent_id,
                utterance=utterance.strip(),
                channel=channel,
                data=data or {},
                session_id=session_id,
                user_id=user_id,
                stream=False,
            )

            await walker.spawn(agent)

            report = await walker.get_report()
            interaction = walker.interaction
            if not interaction:
                error_code = getattr(walker, "_bootstrap_error", None)
                error_detail = next(
                    (
                        item.get("error")
                        for item in (report or [])
                        if isinstance(item, dict) and "error" in item
                    ),
                    None,
                )
                logger.error(
                    "embed.interact: interaction not created (agent_id=%s, "
                    "request_id=%s, bootstrap_error=%s, detail=%s)",
                    agent_id,
                    profile.request_id,
                    error_code,
                    error_detail,
                )
                msg = "Interaction was not created during traversal"
                if error_code:
                    msg = f"{msg} [{error_code}]"
                raise ValidationError(
                    message=msg,
                    details={
                        "agent_id": agent_id,
                        "request_id": profile.request_id,
                        "bootstrap_error": error_code,
                    },
                )

            interaction.streamed = False
            set_interaction(None)
            await interaction.close_interaction()
            await flush_deferred_entities(
                interaction, walker.conversation, strict=False
            )
            await _finalize_usage(interaction)

            result = await build_interact_response(
                user_id=walker.user_id or "",
                session_id=walker.session_id or "",
                interaction=interaction,
                report=report,
            )

            # Background actions queued by the walker — embed mode runs them
            # inline so callers can rely on the returned response capturing
            # the full effect of the turn.
            if walker.background_actions:
                from jvagent.action.interact.endpoints import _run_background_actions

                await _run_background_actions(walker)

            return result
        finally:
            set_current_profile(None)
            try:
                from jvagent.core.cache import maybe_cleanup_on_request

                await maybe_cleanup_on_request()
            except Exception:
                pass


async def list_agents() -> List[Dict[str, Any]]:
    """Return all installed Agent nodes as ``[{id, namespace, name, alias, enabled, description}, ...]``.

    Useful for hosts that need to discover what agents are available after
    :func:`bootstrap` has loaded them from ``app.yaml`` — typically to
    populate a chat provider registry or surface an agent picker in the UI.
    """
    from jvagent.core.agent import Agent

    nodes = await Agent.find({})  # type: ignore[arg-type]
    out: List[Dict[str, Any]] = []
    for node in nodes:
        out.append(
            {
                "id": node.id,
                "namespace": getattr(node, "namespace", None),
                "name": getattr(node, "name", None),
                "alias": getattr(node, "alias", None),
                "enabled": getattr(node, "enabled", True),
                "description": getattr(node, "description", None),
            }
        )
    return out


async def get_agent_id_by_name(
    name: str, *, namespace: Optional[str] = None
) -> Optional[str]:
    """Resolve an Agent node id from its ``name`` (and optional ``namespace``).

    Convenience wrapper around :func:`list_agents` for hosts that wire
    chat threads against a stable dotted identifier (``"integral/integral_agent"``)
    rather than a jvspatial node id, which changes per environment.

    Accepts either:

    * a dotted form ``"<namespace>/<name>"`` (parsed into both halves), or
    * a bare ``name`` plus an explicit ``namespace`` kwarg.

    Returns the matching Agent's node id, or ``None`` if no Agent matches.
    """
    if namespace is None and "/" in name:
        namespace, name = name.split("/", 1)

    from jvagent.core.agent import Agent

    query: Dict[str, Any] = {"name": name}
    if namespace is not None:
        query["namespace"] = namespace

    nodes = await Agent.find(query)  # type: ignore[arg-type]
    if not nodes:
        return None
    return nodes[0].id


async def interact_stream(
    *,
    agent_id: str,
    utterance: str,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    channel: str = "default",
    data: Optional[Dict[str, Any]] = None,
    is_disconnected: Optional[Callable[[], Awaitable[bool]]] = None,
    poll_interval: float = 0.05,
) -> AsyncIterator[Dict[str, Any]]:
    """Streaming counterpart of :func:`interact`.

    Yields the same jvchat envelope dicts that the standalone HTTP endpoint
    serializes to SSE — ``start`` / ``message`` / ``final`` / ``error`` —
    so downstream translators built against the HTTP transport can be
    reused unchanged. The host is responsible for serializing these dicts
    to whatever transport its API surface uses (SSE, WebSocket, etc.).

    Envelope shapes (mirrors ``jvagent.action.interact.endpoints._stream_interaction``):

    * ``{"type": "start", "interaction_id": str, "session_id": str, "user_id": str}``
      — emitted exactly once after the InteractWalker creates the
      Interaction node.
    * ``{"type": "message", "message": <message_dict>}`` — emitted for
      every message (user text chunk, reasoning segment, tool call,
      status, ...) the InteractActions push onto the response bus.
    * ``{"type": "final", **build_interact_response(...)}`` — single
      consolidated envelope after the walker completes.
    * ``{"type": "error", "code": str, "message": str, "details": dict}``
      — terminal error event when the walker fails before producing an
      Interaction (rate limit / validation / bootstrap failure).

    Args:
        agent_id, utterance, user_id, session_id, channel, data: same as
            :func:`interact`.
        is_disconnected: optional async callable returning ``True`` when
            the host's downstream transport has gone away. The walker is
            cancelled and iteration ends. Defaults to never-disconnected.
        poll_interval: seconds between empty-queue polls when waiting on
            walker output. Defaults to 50ms — small enough to keep
            first-token latency low, large enough to avoid CPU spin.

    Raises:
        ValidationError: ``utterance`` is empty / whitespace-only. Raised
            *before* the generator yields its first envelope.
    """
    if not utterance or not utterance.strip():
        raise ValidationError(
            message="utterance is required and cannot be empty",
            details={"utterance": utterance},
        )

    from jvspatial import create_task, flush_deferred_entities

    from jvagent.action.interact.endpoints import _finalize_usage
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.interact.response_builder import build_interact_response
    from jvagent.action.model.context import set_interaction
    from jvagent.core.cache import get_cached_agent
    from jvagent.core.channel import normalize_channel

    channel = normalize_channel(channel)

    async def _disc() -> bool:
        if is_disconnected is None:
            return False
        try:
            return bool(await is_disconnected())
        except Exception:
            return False

    agent = await get_cached_agent(agent_id)
    if not agent:
        yield {
            "type": "error",
            "code": "agent_not_found",
            "message": f"Agent with ID {agent_id!r} not found",
            "details": {"agent_id": agent_id},
        }
        return

    walker = InteractWalker(
        agent_id=agent_id,
        utterance=utterance.strip(),
        channel=channel,
        data=data or {},
        session_id=session_id,
        user_id=user_id,
        stream=True,
    )

    walk_task: Optional[asyncio.Task[Any]] = None
    try:
        walk_task = await create_task(
            walker.spawn(agent),
            name="embed_interact_stream_spawn",
            concurrent=True,
        )

        # Wait up to 5s for InteractWalker to instantiate the Interaction.
        max_wait = 5.0
        waited = 0.0
        while not walker.interaction and waited < max_wait:
            if await _disc():
                if walk_task and not walk_task.done():
                    walk_task.cancel()
                return
            await asyncio.sleep(0.1)
            waited += 0.1

        if not walker.interaction:
            # Drain the walker for diagnostics, surface as terminal error.
            try:
                if walk_task is not None:
                    await walk_task
            except Exception:
                logger.exception(
                    "embed.interact_stream: walker failed before interaction"
                )
            report = await walker.get_report()
            error_code = getattr(walker, "_bootstrap_error", None)
            error_detail = next(
                (
                    item.get("error")
                    for item in (report or [])
                    if isinstance(item, dict) and "error" in item
                ),
                None,
            )
            for item in report or []:
                if isinstance(item, dict) and item.get("access_denied"):
                    yield {
                        "type": "error",
                        "code": "access_denied",
                        "message": "Access denied.",
                    }
                    return
            msg = "Interaction was not created during traversal"
            if error_code:
                msg = f"{msg} [{error_code}]"
            yield {
                "type": "error",
                "code": "interaction_not_created",
                "message": msg,
                "details": {
                    "bootstrap_error": error_code,
                    "detail": error_detail,
                },
            }
            return

        interaction = walker.interaction
        interaction.streamed = True

        yield {
            "type": "start",
            "interaction_id": interaction.id,
            "session_id": walker.session_id or "",
            "user_id": walker.user_id or "",
        }

        # Stream messages off the response bus until the walker finishes.
        if walker.response_bus and walker.session_id:
            message_queue: asyncio.Queue[Any] = asyncio.Queue()

            async def _on_message(message: Any) -> None:
                if getattr(message, "interaction_id", None) != interaction.id:
                    return
                await message_queue.put(message)

            await walker.response_bus.subscribe(
                walker.session_id, _on_message, receive_chunks=True
            )
            try:
                while True:
                    if (
                        walk_task is not None
                        and walk_task.done()
                        and message_queue.empty()
                    ):
                        # Surface walker exception (if any) as terminal error.
                        try:
                            await walk_task
                        except Exception as exc:
                            logger.exception(
                                "embed.interact_stream: walker task failed"
                            )
                            yield {
                                "type": "error",
                                "code": "walker_failed",
                                "message": f"{type(exc).__name__}: {exc}",
                            }
                            return
                        break

                    if await _disc():
                        if walk_task is not None and not walk_task.done():
                            walk_task.cancel()
                        return

                    try:
                        message = await asyncio.wait_for(
                            message_queue.get(), timeout=poll_interval
                        )
                        yield {"type": "message", "message": message.to_dict()}
                    except asyncio.TimeoutError:
                        continue
            finally:
                try:
                    await walker.response_bus.unsubscribe(
                        walker.session_id, _on_message
                    )
                except Exception:
                    pass
        else:
            # No response bus on this agent — wait for completion, then
            # emit the consolidated final envelope only.
            if walk_task is not None:
                try:
                    await walk_task
                except Exception as exc:
                    logger.exception(
                        "embed.interact_stream: walker task failed (no bus)"
                    )
                    yield {
                        "type": "error",
                        "code": "walker_failed",
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                    return

        # Common close-out path for both branches.
        set_interaction(None)
        await interaction.close_interaction()
        await flush_deferred_entities(interaction, walker.conversation, strict=False)
        await _finalize_usage(interaction)

        report = await walker.get_report()
        final_response = await build_interact_response(
            user_id=walker.user_id or "",
            session_id=walker.session_id or "",
            interaction=interaction,
            report=report,
        )
        yield {"type": "final", **final_response}

        if walker.background_actions:
            from jvagent.action.interact.endpoints import _run_background_actions

            await _run_background_actions(walker)
    finally:
        try:
            from jvagent.core.cache import maybe_cleanup_on_request

            await maybe_cleanup_on_request()
        except Exception:
            pass


async def ensure_user(*, agent_id: str, user_id: str) -> Dict[str, Any]:
    """Idempotent host-driven user binding.

    Looks up (or creates) the jvagent ``User`` node scoped to this agent's
    ``Memory`` and bound to ``user_id``. Returns a small dict suitable for
    surfacing back to the host (id, user_id, created_at, last_seen).

    The interact pipeline already calls the same ``Memory.get_user`` path
    on first message, so this is technically optional for hosts that just
    want chat to "work". Call it upfront when:

    * the host wants the jvagent ``User`` to exist before the first turn
      (e.g. to prefetch :class:`UserLongMemoryNode` content for a UI), or
    * the host wants explicit confirmation that the identity binding has
      landed (e.g. a ``whoami`` step at login that synchronizes the host's
      principal with the agent's user model).

    Args:
        agent_id: jvspatial node id of the target ``Agent``.
        user_id: Stable host identifier — typically the host's User node id
          or any other stable string. Becomes the jvagent ``User.user_id``;
          all subsequent memory/conversation queries scope to it.

    Returns:
        ``{"id": <jvagent User node id>, "user_id": <input user_id>,
        "created_at": ISO8601, "last_seen": ISO8601}``.

    Raises:
        ResourceNotFoundError: ``agent_id`` does not resolve, or the agent
          has no associated ``Memory`` (indicates a half-bootstrapped
          install — re-run ``embed.bootstrap``).
    """
    from jvagent.core.cache import get_cached_agent

    agent = await get_cached_agent(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID {agent_id!r} not found",
            details={"agent_id": agent_id},
        )

    memory = await agent.get_memory()
    if not memory:
        raise ResourceNotFoundError(
            message=f"Agent {agent_id!r} has no Memory node — re-run embed.bootstrap()",
            details={"agent_id": agent_id},
        )

    user = await memory.get_user(user_id, create_if_missing=True)
    if not user:
        # get_user returns None only when create_if_missing=False; we pass
        # True above, so this branch is unreachable. Surface defensively.
        raise ResourceNotFoundError(
            message=f"Failed to ensure user {user_id!r} on agent {agent_id!r}",
            details={"agent_id": agent_id, "user_id": user_id},
        )

    return {
        "id": user.id,
        "user_id": user.user_id,
        "created_at": (
            user.created_at.isoformat() if getattr(user, "created_at", None) else None
        ),
        "last_seen": (
            user.last_seen.isoformat() if getattr(user, "last_seen", None) else None
        ),
    }


async def list_user_conversations(
    *, agent_id: str, user_id: str
) -> List[Dict[str, Any]]:
    """Return jvagent ``Conversation`` summaries for a host user.

    Useful for admin / debug surfaces that want to see what the agent
    "knows" about a user's conversation history. The host (integral)
    already keeps its own thread list keyed on the same identifiers; this
    is the agent-side complement.

    Returns ``[]`` if the user is not yet bound (no jvagent User node).

    Each entry: ``{"id", "session_id", "status", "channel",
    "created_at", "last_interaction_at", "interaction_count"}``.
    """
    from jvagent.core.cache import get_cached_agent
    from jvagent.memory.conversation import Conversation

    agent = await get_cached_agent(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID {agent_id!r} not found",
            details={"agent_id": agent_id},
        )
    memory = await agent.get_memory()
    if not memory:
        return []

    user = await memory.get_user(user_id, create_if_missing=False)
    if not user:
        return []

    convs = await user.nodes(node=Conversation)
    out: List[Dict[str, Any]] = []
    for c in convs:
        out.append(
            {
                "id": c.id,
                "session_id": getattr(c, "session_id", None),
                "status": getattr(c, "status", None),
                "channel": getattr(c, "channel", None),
                "created_at": (
                    c.created_at.isoformat() if getattr(c, "created_at", None) else None
                ),
                "last_interaction_at": (
                    c.last_interaction_at.isoformat()
                    if getattr(c, "last_interaction_at", None)
                    else None
                ),
                "interaction_count": getattr(c, "interaction_count", 0),
            }
        )
    return out


async def delete_conversation(*, agent_id: str, session_id: str) -> bool:
    """Delete a single jvagent ``Conversation`` (cascades to its
    ``Interaction`` chain).

    Idempotent — returns ``False`` if no conversation matches the
    ``session_id``. Returns ``True`` after a successful delete.

    The host typically calls this from its own thread-deletion path so
    the agent-side state stays in sync (otherwise the conversation +
    interactions linger in the agent's memory subgraph).

    Args:
        agent_id: jvspatial node id of the target ``Agent``.
        session_id: jvagent session id captured by the host (typically
          stored on the host's thread row as ``provider_session_id``).
    """
    from jvagent.core.cache import get_cached_agent
    from jvagent.memory.conversation import Conversation

    agent = await get_cached_agent(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID {agent_id!r} not found",
            details={"agent_id": agent_id},
        )
    memory = await agent.get_memory()
    if not memory:
        return False

    conv = await Conversation.find_one({"context.session_id": session_id})
    if not conv:
        return False

    # purge_conversation handles cascade + counter decrements correctly.
    purged = await memory.purge_conversation(conversation_id=conv.id)
    return bool(purged)


async def purge_user(*, agent_id: str, user_id: str) -> bool:
    """Cascade-delete the user's ``User`` + ``Conversation`` + ``Interaction``
    subgraph in jvagent's memory.

    Idempotent — returns ``False`` if no user matches. Returns ``True`` on
    successful purge.

    Use case: "forget me" or account-deletion flow on the host. Runs the
    same code path jvagent's admin endpoint uses (``Memory.purge_user_memory``).
    """
    from jvagent.core.cache import get_cached_agent

    agent = await get_cached_agent(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID {agent_id!r} not found",
            details={"agent_id": agent_id},
        )
    memory = await agent.get_memory()
    if not memory:
        return False

    purged = await memory.purge_user_memory(user_id=user_id)
    return bool(purged)


__all__ = [
    "bootstrap",
    "shutdown",
    "run_index_migration",
    "run_app_startup",
    "interact",
    "interact_stream",
    "list_agents",
    "get_agent_id_by_name",
    "ensure_user",
    "list_user_conversations",
    "delete_conversation",
    "purge_user",
]
