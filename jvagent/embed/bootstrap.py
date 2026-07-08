"""Bootstrap, shutdown, and Server wiring for embed hosts."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)

_ENDPOINT_DISABLE_ENV_VAR = "JVAGENT_EMBED_ENDPOINTS_DISABLED"
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


def _should_register_endpoints(override: Optional[bool]) -> bool:
    """Resolve whether embed.bootstrap should register endpoints.

    Explicit ``override`` wins. Otherwise the kill-switch env var
    ``JVAGENT_EMBED_ENDPOINTS_DISABLED`` controls behavior: truthy values
    (``1``/``true``/``yes``/``on``, case-insensitive) disable registration;
    any other value (including unset) leaves registration enabled.
    """
    if override is not None:
        return bool(override)
    raw = os.environ.get(_ENDPOINT_DISABLE_ENV_VAR, "")
    return raw.strip().lower() not in _TRUTHY_ENV_VALUES


def _build_host_server_subclass() -> Any:
    """Build the ``Server`` subclass that auto-registers jvagent endpoints.

    Returned class overrides ``get_app()`` so the first invocation runs
    jvagent's endpoint registration on ``self`` before the original
    ``Server.get_app()`` builds the FastAPI app. This is the timing window
    where new endpoint-router additions still propagate into the live ASGI
    app via ``include_router``. Subsequent ``get_app()`` calls (which return
    the cached app) skip registration.

    Honors :envvar:`JVAGENT_EMBED_ENDPOINTS_DISABLED` via
    :func:`register_jvagent_endpoints_on_host`.
    """
    from jvspatial.api.server import Server as _Server

    class HostServer(_Server):  # type: ignore[misc, valid-type]
        """Drop-in ``jvspatial.api.Server`` that mounts jvagent's routes."""

        def get_app(self) -> Any:  # type: ignore[override]
            if getattr(self, "app", None) is None:
                try:
                    register_jvagent_endpoints_on_host(self)
                except Exception:
                    logger.exception(
                        "jvagent embed: endpoint auto-registration failed; "
                        "host app will be built without jvagent routes"
                    )
            return super().get_app()

    HostServer.__name__ = "Server"
    HostServer.__qualname__ = "Server"
    return HostServer


# Bound eagerly at module load. The subclass exists so hosts can swap
# ``from jvspatial.api import Server`` for ``from jvagent.embed import Server``
# and get automatic endpoint registration on ``get_app()``. Eager binding is
# cheap (jvspatial.api.server is already imported by jvspatial.api at top
# of this file) and avoids the ``__getattr__`` indirection that breaks
# ``from jvagent.embed import Server`` syntax.
Server = _build_host_server_subclass()


async def bootstrap(
    *,
    app_root: Optional[Union[str, Path]] = None,
    update_mode: Optional[str] = None,
    ensure_admin: bool = False,
    run_action_startup: bool = True,
    register_endpoints: Optional[bool] = None,
) -> None:
    """Initialize jvagent inside an already-running jvspatial process.

    Runs the same startup sequence the standalone CLI server runs, minus
    HTTP server lifecycle. Safe to call multiple times — every step is
    idempotent.

    HTTP endpoint registration is **NOT** done here — see
    :func:`register_jvagent_endpoints_on_host`, which the host must call
    synchronously between ``Server(...)`` and ``server.get_app()``. This
    bootstrap function runs inside the FastAPI lifespan startup hook,
    which is too late to mutate the app's router set.

    Order of operations:

    1. ``run_index_migration()`` — drop deprecated indexes, ensure current
       indexes for all jvagent entity classes (Conversation, Interaction,
       User, Conversation, etc.) on the host's jvspatial database.
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
        register_endpoints: Retained for forward compatibility. Bootstrap
            no longer performs HTTP route registration (call
            :func:`register_jvagent_endpoints_on_host` before
            ``server.get_app()`` instead). Passing ``True`` here logs a
            warning so misconfiguration is visible.

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

    # Pin the global app_root used by app-config readers and skill
    # discovery's filesystem walker. The standalone CLI sets this
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

    # Endpoint registration is intentionally NOT called here. The host must
    # call ``register_jvagent_endpoints_on_host`` synchronously between
    # ``Server(...)`` and ``server.get_app()`` so the route additions are
    # included when FastAPI's APIRouter is mounted on the app. Calling it
    # from this async ``bootstrap`` (which typically runs inside the
    # FastAPI lifespan startup hook) would be too late: the app has already
    # snapshotted ``endpoint_router`` via ``app.include_router(...)`` and
    # subsequent additions do not propagate.
    if register_endpoints is True:
        logger.warning(
            "jvagent embed: register_endpoints=True passed to bootstrap, but "
            "endpoint registration must happen before server.get_app(). "
            "Call jvagent.embed.register_jvagent_endpoints_on_host(server) "
            "right after constructing Server(...) and before get_app() instead."
        )

    if ensure_admin:
        from jvagent.cli.bootstrap import ensure_admin_user

        await ensure_admin_user()

    logger.info("jvagent embed bootstrap complete")


def register_jvagent_endpoints_on_host(
    server: Optional[Any] = None, *, force: Optional[bool] = None
) -> int:
    """Mount jvagent's HTTP routes on the host's jvspatial server.

    Imports jvagent's first-party ``@endpoint``-decorated modules (admin
    agents CRUD, status, app/update_mode, graph repair, memory admin,
    action endpoints, logging, optional google OAuth) and syncs them onto
    the host's jvspatial ``Server`` so its FastAPI app serves them
    alongside the host's own routes.

    Must be called **synchronously between** ``Server(...)`` **and**
    ``server.get_app()``. After ``get_app()`` runs, FastAPI has already
    snapshotted the server's endpoint router via ``include_router``, and
    later additions do not propagate to the live ASGI app.

    Args:
        server: The host's :class:`jvspatial.api.Server` instance. If
            omitted, falls back to ``jvspatial.api.context.get_current_server()``.
        force: ``None`` honors the ``JVAGENT_EMBED_ENDPOINTS_DISABLED``
            env var (default enables, set truthy to disable). ``True``
            always registers; ``False`` always skips.

    Returns:
        Count of endpoint modules synced onto the host server via
        ``jvspatial.api.sync_endpoint_modules``. ``0`` when registration
        is suppressed or no host server is available (the module imports
        still happen so the decorator-time deferred registry will flush
        on a later ``Server.__init__``).
    """
    if not _should_register_endpoints(force):
        logger.info(
            "jvagent embed endpoint registration disabled via %s",
            _ENDPOINT_DISABLE_ENV_VAR,
        )
        return 0

    from jvagent.core.embed_endpoints import import_jvagent_endpoint_modules

    import_jvagent_endpoint_modules()

    try:
        from jvspatial.api import sync_endpoint_modules
        from jvspatial.api.context import get_current_server
    except ImportError:
        logger.debug(
            "sync_endpoint_modules / get_current_server unavailable; "
            "relying on decorator-time registration"
        )
        return 0

    target_server = server if server is not None else get_current_server()
    if target_server is None:
        logger.debug(
            "jvagent embed: no host server provided or in context; endpoint "
            "modules imported and will register via deferred registry on a "
            "later Server.__init__"
        )
        return 0

    # Warn — but still sync — when the host has already built its FastAPI
    # app. The sync writes new routes onto the server's endpoint_router,
    # but FastAPI's app already copied that router via include_router, so
    # the new routes will NOT appear on the live app without a rebuild.
    if getattr(target_server, "app", None) is not None:
        logger.warning(
            "jvagent embed: register_jvagent_endpoints_on_host called after "
            "server.get_app(); new routes will register on the endpoint_router "
            "but will NOT appear on the live FastAPI app. Move the call "
            "between Server(...) and server.get_app()."
        )

    synced = sync_endpoint_modules(target_server)
    logger.info("jvagent embed registered %d endpoint module(s) on host server", synced)
    return synced


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
