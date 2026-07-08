"""In-process interact, streaming, and user lifecycle for embed hosts."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional

from jvspatial.api.exceptions import (
    ResourceNotFoundError,
    ValidationError,
)

logger = logging.getLogger(__name__)

# In-flight embed interact walker tasks keyed by host thread_id and/or
# jvagent session_id — enables explicit cancel from the host API.
_interact_task_lock = asyncio.Lock()
_interact_tasks: Dict[str, asyncio.Task[Any]] = {}


async def _register_interact_task(key: str, task: asyncio.Task[Any]) -> None:
    if not key:
        return
    async with _interact_task_lock:
        existing = _interact_tasks.get(key)
        if existing is not None and not existing.done() and existing is not task:
            existing.cancel()
        _interact_tasks[key] = task


async def _clear_interact_task(key: str, task: asyncio.Task[Any]) -> None:
    if not key:
        return
    async with _interact_task_lock:
        if _interact_tasks.get(key) is task:
            del _interact_tasks[key]


def cancel_interact(
    *,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> bool:
    """Cancel an in-flight :func:`interact_stream` walker task, if any."""
    for key in (thread_id, session_id):
        if not key:
            continue
        task = _interact_tasks.get(key)
        if task is not None and not task.done():
            task.cancel()
            return True
    return False


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

    Drives jvagent's full interact pipeline (orchestrator routing → InteractWalker
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

    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.interact.response_builder import build_interact_response
    from jvagent.action.interact.webhook_pipeline import (
        finalize_usage as _finalize_usage,
    )
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
        trigger = (data or {}).get("trigger")
        if trigger != "agent_workstream":
            raise ValidationError(
                message="utterance is required and cannot be empty",
                details={"utterance": utterance},
            )
        utterance = (data or {}).get("system_utterance") or "[agent workstream]"

    from jvspatial import create_task, flush_deferred_entities

    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.interact.response_builder import build_interact_response
    from jvagent.action.interact.webhook_pipeline import (
        finalize_usage as _finalize_usage,
    )
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
    task_keys: List[str] = []
    try:
        walk_task = await create_task(
            walker.spawn(agent),
            name="embed_interact_stream_spawn",
            concurrent=True,
        )
        thread_key = str((data or {}).get("thread_id") or "").strip()
        if thread_key:
            task_keys.append(thread_key)
        if session_id:
            task_keys.append(session_id)
        for key in task_keys:
            await _register_interact_task(key, walk_task)

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

        if walk_task is not None and walker.session_id:
            sid = walker.session_id
            if sid not in task_keys:
                task_keys.append(sid)
                await _register_interact_task(sid, walk_task)

        # Stream messages off the response bus until the walker finishes.
        if walker.response_bus and walker.session_id:
            message_queue: asyncio.Queue[Any] = asyncio.Queue()
            # Per-stream dedupe ONLY for non-streaming-chunk
            # message types. ``ResponseBus`` deliberately re-uses
            # one ``acc.message_id`` across every ``stream_chunk``
            # of a single logical assistant turn (they're parts of
            # the same message, not independent emits) — so
            # deduping stream_chunks by id would drop every chunk
            # after the first and the FE would see a truncated
            # 3-5 char reply. ADHOC/FINAL messages do carry
            # one-shot unique ids, so deduping THOSE is the
            # actual defense (against duplicate publishes from
            # leaked subscribers or session-queue replay on
            # reconnect). See the ``message_type`` taxonomy in
            # ``response/message.py``.
            seen_message_ids: set = set()

            async def _on_message(message: Any) -> None:
                if getattr(message, "interaction_id", None) != interaction.id:
                    return
                mtype = getattr(message, "message_type", "")
                if mtype != "stream_chunk":
                    mid = getattr(message, "id", None)
                    if mid:
                        if mid in seen_message_ids:
                            return
                        seen_message_ids.add(mid)
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
        if walk_task is not None:
            for key in task_keys:
                await _clear_interact_task(key, walk_task)
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
      (e.g. to prefetch ``User.memory`` for a UI), or
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


async def enqueue_proactive_task(
    *,
    agent_id: str,
    user_id: str,
    spec: Any,
    session_id: Optional[str] = None,
    channel: str = "default",
    title: str = "",
) -> Optional[Dict[str, Any]]:
    """Enqueue a PROACTIVE task on a user's conversation via the embed surface."""
    from jvagent.core.cache import get_cached_agent

    agent = await get_cached_agent(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID {agent_id!r} not found",
            details={"agent_id": agent_id},
        )
    handle = await agent.enqueue_proactive_task(
        user_id=user_id,
        spec=spec,
        session_id=session_id,
        channel=channel,
        owner_action="embed.enqueue_proactive_task",
        title=title,
    )
    if handle is None:
        return None
    return {"task_id": handle.id, "status": handle.status}
