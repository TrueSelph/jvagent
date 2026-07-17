"""Server lifecycle CLI handlers."""

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Optional, Set

from dotenv import load_dotenv
from jvspatial import is_serverless_mode

from jvagent.cli.bootstrap import bootstrap_application_graph, ensure_admin_user
from jvagent.cli.server_config import (
    _set_db_env_from_config,
    create_server_from_config,
    pre_startup_bootstrap,
)
from jvagent.core.bootstrap_logger import BootstrapLogger
from jvagent.core.bootstrap_update_mode import (
    reset_app_update_mode_after_successful_bootstrap,
    resolve_bootstrap_update_mode,
)
from jvagent.core.config import (
    effective_log_db_type,
    get_config_value,
    load_app_config,
    normalize_empty,
    resolve_db_path,
    resolve_log_db_path,
    resolve_pageindex_purge_path,
)

logger = logging.getLogger(__name__)


def load_app_env(app_root: str = None) -> None:
    """Load .env file from the app root directory.

    This ensures that when running jvagent from an app directory,
    the .env file in that directory is loaded. The function will:
    1. Try to load .env from the app root directory
    2. Log if a .env file was found and loaded

    Args:
        app_root: Path to the app root directory. If None, uses current working directory.
    """
    if app_root is None:
        app_root = os.getcwd()

    env_path = os.path.join(app_root, ".env")
    load_dotenv(env_path, override=True)
    if os.path.exists(env_path):
        logger.info(f"Loaded .env from: {env_path}")
    else:
        logger.debug(f"No .env in app root: {app_root}")


def _unsafe_purge_reason(target: Path, app_root: Path) -> Optional[str]:
    """Return a reason string if *target* is unsafe to delete, else None.

    ``--purge`` deletes whatever the resolved DB/log paths point at. A
    misconfigured ``database.path: "."`` (or an env override pointing at the
    app root or ``$HOME``) would otherwise ``rmtree`` the entire app tree —
    YAML sources and all. Refuse targets that are the app root itself, an
    ancestor of it, the filesystem root, or the home directory.
    """
    try:
        target = target.resolve()
        app_root = app_root.resolve()
    except Exception as e:  # pragma: no cover - defensive
        return f"could not resolve path ({e})"

    if target == Path(target.anchor):
        return "filesystem root"
    try:
        if target == Path.home().resolve():
            return "home directory"
    except Exception:  # pragma: no cover - home may be undefined
        pass
    if target == app_root:
        return "the app root directory"
    if target in app_root.parents:
        return "an ancestor of the app root (would delete app sources)"
    return None


def _remove_fs_target(path: Path) -> None:
    """Remove a local database path (directory tree or single file)."""
    if not path.exists():
        logger.debug("Path not found (skipping): %s", path)
        return
    try:
        if path.is_dir():
            shutil.rmtree(path)
            logger.info("Deleted directory: %s", path)
        elif path.is_file():
            path.unlink()
            logger.info("Deleted file: %s", path)
    except Exception as e:
        logger.error("Failed to delete %s: %s", path, e)


def purge_app_data(app_root: str, assume_yes: bool = False) -> None:
    """Purge local application data (JSON/SQLite stores only).

    Reads database configuration from app.yaml and environment variables.
    Remote backends (MongoDB, DynamoDB) are not modified; a warning is logged.
    Resolves relative paths relative to app_root.

    Targets that are the app root, an ancestor of it, the filesystem root, or
    the home directory are refused (a misconfigured ``database.path`` must not
    delete app sources). Requires interactive confirmation unless
    ``assume_yes`` is set (``--yes`` / ``JVAGENT_ASSUME_YES``).

    Args:
        app_root: Path to the app root directory.
        assume_yes: Skip the interactive confirmation prompt.
    """
    if app_root is None:
        app_root = os.getcwd()

    app_config = load_app_config(app_root)
    db_type = (
        normalize_empty(
            get_config_value(app_config, "database.type", "JVSPATIAL_DB_TYPE", "json")
        )
        or "json"
    )
    db_path_str = resolve_db_path(app_root, app_config, db_type)
    log_type = effective_log_db_type(app_config)
    log_db_path_str = resolve_log_db_path(app_root, app_config)
    pageindex_path_str = resolve_pageindex_purge_path(app_root, app_config)

    paths_to_purge: Set[Path] = set()

    if db_type in ("json", "sqlite"):
        paths_to_purge.add(Path(db_path_str).resolve())
    else:
        logger.warning(
            "App database type is %s; --purge does not remove remote data. "
            "Skipping app database path.",
            db_type,
        )

    if log_type in ("json", "sqlite"):
        if log_db_path_str:
            paths_to_purge.add(Path(log_db_path_str).resolve())
    else:
        logger.warning(
            "Logging database type is %s; --purge does not remove remote log data. "
            "Skipping logging database path.",
            log_type,
        )

    if pageindex_path_str:
        paths_to_purge.add(Path(pageindex_path_str).resolve())
    else:
        pi_type = (
            normalize_empty(
                get_config_value(
                    app_config, "pageindex.db_type", "JVAGENT_PAGEINDEX_DB_TYPE", "json"
                )
            )
            or "json"
        )
        if pi_type not in ("json", "sqlite"):
            logger.warning(
                "PageIndex database type is %s; --purge does not remove remote data. "
                "Skipping PageIndex storage.",
                pi_type,
            )

    # Refuse unsafe targets before touching the filesystem.
    app_root_path = Path(app_root)
    safe_targets: Set[Path] = set()
    for target in paths_to_purge:
        reason = _unsafe_purge_reason(target, app_root_path)
        if reason:
            logger.error(
                "Refusing to purge %s: resolves to %s. Check database.path / "
                "JVSPATIAL_DB_PATH.",
                target,
                reason,
            )
            continue
        safe_targets.add(target)

    if not safe_targets:
        logger.warning("Purge: no safe local data paths to remove.")
        return

    sorted_targets = sorted(safe_targets, key=lambda p: str(p))

    if not assume_yes:
        print("The following local data paths will be permanently deleted:")
        for target in sorted_targets:
            print(f"  - {target}")
        try:
            answer = input("Type 'yes' to confirm: ").strip().lower()
        except EOFError:
            answer = ""
        if answer != "yes":
            logger.warning("Purge aborted by user.")
            return

    logger.warning("Purging local application data under %s...", app_root)

    for target in sorted_targets:
        _remove_fs_target(target)

    logger.info("Purge complete.")


class StartupLogCounter(logging.Handler):
    """Logging handler that counts warnings and errors during startup."""

    def __init__(self):
        super().__init__(level=logging.WARNING)  # Only capture WARNING and above
        self.warning_count = 0
        self.error_count = 0
        self.critical_count = 0

    def emit(self, record: logging.LogRecord) -> None:
        """Count log records by level."""
        if record.levelno >= logging.CRITICAL:
            self.critical_count += 1
        elif record.levelno >= logging.ERROR:
            self.error_count += 1
        elif record.levelno >= logging.WARNING:
            self.warning_count += 1

    def get_summary(self) -> dict:
        """Get summary of logged warnings and errors."""
        return {
            "warnings": self.warning_count,
            "errors": self.error_count,
            "critical": self.critical_count,
            "total": self.warning_count + self.error_count + self.critical_count,
        }


def run_server(
    update_mode: Optional[str] = None,
    debug: bool = False,
    app_root: str = None,
    stress_seed: Any = None,
) -> None:
    """Start the jvagent server.

    Args:
        update_mode: Update strategy - "merge" for non-destructive merge, "source" for
                     destructive overwrite from YAML, or None to skip existing.
        debug: If True, enable debug logging.
        app_root: Path to the app root directory. If None, uses current working directory.
        stress_seed: When set (``StressSeedConfig``), populate the graph after bootstrap
            and before ``server.run()``, on the same database the server will use.
    """
    if app_root is None:
        app_root = os.getcwd()

    # Database path env vars: ``main`` sets ``_set_db_env_from_config`` before this runs;
    # ``create_server_from_config`` also sets ``JVSPATIAL_DB_PATH`` / pops forbidden keys.

    # Install log counter to track warnings and errors during startup
    log_counter = StartupLogCounter()
    root_logger = logging.getLogger()
    root_logger.addHandler(log_counter)

    bootstrap_log = BootstrapLogger("Startup")
    bootstrap_log.start("jvagent application")

    try:
        # Create server from configuration (pass app_root to load app.yaml)
        server = create_server_from_config(debug=debug, app_root=app_root)

        # Perform bootstrap tasks before server starts
        admin_exists = asyncio.run(
            pre_startup_bootstrap(server, update_mode=update_mode, app_root=app_root)
        )

        if stress_seed is not None:
            from jvagent.stress_seed_graph import (
                StressSeedConfig,
                execute_stress_seed_graph,
            )

            if not isinstance(stress_seed, StressSeedConfig):
                raise TypeError("stress_seed must be a StressSeedConfig or None")
            os.environ.setdefault("JVSPATIAL_ENABLE_DEFERRED_SAVES", "false")
            logger.info(
                "Graph stress-seed: writing %d user memory nodes with %d interactions each "
                "to the app database, then starting the server.",
                stress_seed.user_memory_nodes,
                stress_seed.interactions_per_user_memory_node,
            )
            asyncio.run(execute_stress_seed_graph(stress_seed))
            from jvagent.core.app import App

            App.clear_cache()

        if admin_exists:
            if debug:
                bootstrap_log.info("Admin user configured")
        else:
            bootstrap_log.warning(
                "Admin user not found. "
                "Set JVAGENT_ADMIN_PASSWORD in .env to create admin user on first run."
            )

        # Register startup event to display summary after server has started
        # This ensures the summary appears after all uvicorn logs
        async def show_startup_summary():
            """Display startup summary after server has started."""
            # Small delay to ensure uvicorn logs appear first
            await asyncio.sleep(0.5)

            summary = log_counter.get_summary()
            if summary["total"] > 0:
                summary_parts = []
                if summary["critical"] > 0:
                    summary_parts.append(f"❌ {summary['critical']} critical")
                if summary["errors"] > 0:
                    summary_parts.append(
                        f"❌ {summary['errors']} error{'s' if summary['errors'] != 1 else ''}"
                    )
                if summary["warnings"] > 0:
                    summary_parts.append(
                        f"⚠️  {summary['warnings']} warning{'s' if summary['warnings'] != 1 else ''}"
                    )

                summary_msg = " | ".join(summary_parts)
                logger.warning(f"⚠️  Startup Summary: {summary_msg}")
            else:
                logger.info("✓ Startup Summary: No warnings or errors")

            # Remove the log counter handler after displaying summary
            root_logger.removeHandler(log_counter)

        # Register the startup hook to display summary
        server.lifecycle_manager.add_startup_hook(show_startup_summary)

        # Start the server
        bootstrap_log.complete("Ready")
        # Always disable uvicorn auto-reload. jvagent boots the app object
        # (not an import string) and builds the graph in-process, so uvicorn's
        # reload path — which requires an import string — aborts the server
        # with SystemExit(1). Without this, `development.debug: true` in
        # app.yaml (which sets Server.config.debug, and reload defaults to it)
        # is a guaranteed startup crash *after* bootstrap already consumed the
        # one-shot update_mode. `debug` still controls verbose logging via
        # log_level; it must not imply auto-reload here. AUDIT-cli (debug reload).
        run_kwargs: dict[str, Any] = {"reload": False}
        if is_serverless_mode():
            run_kwargs["workers"] = 1
        server.run(**run_kwargs)
    except Exception:
        # If server fails to start, display summary and remove handler
        summary = log_counter.get_summary()
        if summary["total"] > 0:
            summary_parts = []
            if summary["critical"] > 0:
                summary_parts.append(f"❌ {summary['critical']} critical")
            if summary["errors"] > 0:
                summary_parts.append(
                    f"❌ {summary['errors']} error{'s' if summary['errors'] != 1 else ''}"
                )
            if summary["warnings"] > 0:
                summary_parts.append(
                    f"⚠️  {summary['warnings']} warning{'s' if summary['warnings'] != 1 else ''}"
                )

            summary_msg = " | ".join(summary_parts)
            logger.warning(f"⚠️  Startup Summary: {summary_msg}")
        root_logger.removeHandler(log_counter)
        raise
    finally:
        # Ensure handler is removed even if startup hook didn't run
        # (safe to call even if handler was already removed)
        try:
            root_logger.removeHandler(log_counter)
        except (ValueError, AttributeError):
            pass  # Handler already removed or doesn't exist


async def show_status(app_root: str = None) -> None:
    """Show application status.

    Args:
        app_root: Path to the app root directory. If None, uses current working directory.
    """
    from jvagent.core.app_loader import AppLoader

    if app_root is None:
        app_root = os.getcwd()

    _set_db_env_from_config(app_root)

    app_loader = AppLoader(app_root)
    status = await app_loader.get_app_status()

    print("\n=== jvagent Application Status ===\n")
    print(f"Status: {status.get('status', 'unknown')}")

    if "message" in status:
        print(f"Message: {status['message']}")

    if "app" in status:
        app_info = status["app"]
        print("\nApplication:")
        print(f"  ID: {app_info.get('id', 'N/A')}")
        print(f"  Name: {app_info.get('name', 'N/A')}")
        print(f"  Version: {app_info.get('version', 'N/A')}")
        print(f"  Description: {app_info.get('description', 'N/A')}")
        print(
            f"  File Storage: {'enabled' if app_info.get('file_storage_enabled') else 'disabled'}"
        )

    if "agents" in status:
        agents_info = status["agents"]
        print("\nAgents:")
        print(f"  Total: {agents_info.get('total', 0)}")
        print(f"  Active: {agents_info.get('active', 0)}")

        agents_list = agents_info.get("list", [])
        if agents_list:
            print("\n  Installed Agents:")
            for agent in agents_list:
                print(
                    f"    - {agent.get('name')} (ID: {agent.get('id')}, Enabled: {agent.get('enabled')})"
                )

    print()


async def bootstrap_only(
    update_mode: Optional[str] = None, app_root: str = None
) -> None:
    """Bootstrap the application graph without starting the server.

    Args:
        update_mode: Update strategy - "merge" for non-destructive merge, "source" for
                     destructive overwrite from YAML, or None to skip existing.
        app_root: Path to the app root directory. If None, uses current working directory.
    """
    if app_root is None:
        app_root = os.getcwd()

    load_app_env(app_root=app_root)
    _set_db_env_from_config(app_root)

    # Instantiate (but do not run) the Server so it registers in jvspatial's
    # context. jvspatial >=0.0.9 resolves the auth service from the current
    # Server (get_auth_service()), which ensure_admin_user() relies on; the
    # serve path gets this for free, so the standalone bootstrap path must do
    # the same or admin creation fails with "requires a Server in context".
    create_server_from_config(app_root=app_root)

    # Install log counter to track warnings and errors during bootstrap
    log_counter = StartupLogCounter()
    root_logger = logging.getLogger()
    root_logger.addHandler(log_counter)

    try:
        from jvagent.core.index_bootstrap import run_index_migration

        await run_index_migration()

        effective_update_mode = await resolve_bootstrap_update_mode(update_mode)
        await bootstrap_application_graph(
            update_mode=effective_update_mode, app_root=app_root
        )

        # Initialize all actions by calling their on_startup() hooks
        # This ensures runtime components like channel adapters are initialized
        from jvagent.core.startup import run_app_startup

        await run_app_startup()

        await ensure_admin_user()

        await reset_app_update_mode_after_successful_bootstrap()

        # Display bootstrap summary
        summary = log_counter.get_summary()
        if summary["total"] > 0:
            summary_parts = []
            if summary["critical"] > 0:
                summary_parts.append(f"❌ {summary['critical']} critical")
            if summary["errors"] > 0:
                summary_parts.append(
                    f"❌ {summary['errors']} error{'s' if summary['errors'] != 1 else ''}"
                )
            if summary["warnings"] > 0:
                summary_parts.append(
                    f"⚠️  {summary['warnings']} warning{'s' if summary['warnings'] != 1 else ''}"
                )

            summary_msg = " | ".join(summary_parts)
            logger.warning(f"⚠️  Bootstrap Summary: {summary_msg}")
        else:
            logger.info("✓ Bootstrap Summary: No warnings or errors")

        if effective_update_mode == "source":
            print(
                "Bootstrap complete! (Updated existing agents and actions from source)"
            )
        elif effective_update_mode == "merge":
            print("Bootstrap complete! (Merged source changes, preserved DB state)")
        else:
            print("Bootstrap complete! (Used existing agents and actions)")
    finally:
        # Remove the log counter handler
        root_logger.removeHandler(log_counter)
