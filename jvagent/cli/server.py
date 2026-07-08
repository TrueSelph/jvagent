"""Server lifecycle CLI handlers."""

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Any, List, Optional, Set

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


def purge_app_data(app_root: str) -> None:
    """Purge local application data (JSON/SQLite stores only).

    Reads database configuration from app.yaml and environment variables.
    Remote backends (MongoDB, DynamoDB) are not modified; a warning is logged.
    Resolves relative paths relative to app_root.

    Args:
        app_root: Path to the app root directory.
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

    logger.warning("Purging local application data under %s...", app_root)

    for target in sorted(paths_to_purge, key=lambda p: str(p)):
        _remove_fs_target(target)

    logger.info("Purge complete.")


def run_validate(app_root: str) -> int:
    """Validate ``app.yaml`` and discovered ``agent.yaml`` files.

    Runs the same structural checks as runtime (``validate_*`` helpers).
    Prints issues to the log and returns 1 if any warning-level issue is found
    (suitable for CI).

    Args:
        app_root: Application root directory containing ``app.yaml``.

    Returns:
        0 if no issues, 1 otherwise.
    """
    import yaml

    from jvagent.core.agent_loader import AgentLoader
    from jvagent.core.agent_yaml_validator import (
        _reset_warning_cache_for_tests as reset_agent_yaml_warnings,
    )
    from jvagent.core.agent_yaml_validator import (
        validate_agent_yaml,
    )
    from jvagent.core.app_yaml_validator import (
        _reset_warning_cache_for_tests as reset_app_yaml_warnings,
    )
    from jvagent.core.app_yaml_validator import (
        validate_app_yaml_descriptor,
    )
    from jvagent.core.env_resolver import resolve_env_placeholders

    root = Path(app_root).resolve()
    app_yaml = root / "app.yaml"

    reset_app_yaml_warnings()
    reset_agent_yaml_warnings()

    if not app_yaml.is_file():
        logger.error("app.yaml not found: %s", app_yaml)
        return 1

    try:
        with open(app_yaml, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except Exception as e:
        logger.error("Failed to read or parse app.yaml: %s", e, exc_info=True)
        return 1

    if not isinstance(raw, dict):
        logger.error("app.yaml must contain a mapping at the root")
        return 1

    data = resolve_env_placeholders(raw)
    issues: List[str] = []
    for w in validate_app_yaml_descriptor(data):
        suffix = f" Hint: {w.hint}" if w.hint else ""
        issues.append(f"app.yaml [{w.path}] {w.message}{suffix}")

    loader = AgentLoader(str(root))
    for namespace, agent_name in loader.discover_agents():
        agent_file = root / "agents" / namespace / agent_name / "agent.yaml"
        try:
            with open(agent_file, encoding="utf-8") as f:
                agent_raw = yaml.safe_load(f)
        except Exception as e:
            issues.append(f"{agent_file}: failed to load ({e})")
            continue
        if not isinstance(agent_raw, dict):
            issues.append(f"{agent_file}: expected mapping at root")
            continue
        agent_data = resolve_env_placeholders(agent_raw)
        for agent_issue in validate_agent_yaml(agent_data):
            suffix = f" Hint: {agent_issue.hint}" if agent_issue.hint else ""
            issues.append(
                f"{agent_file} [{agent_issue.path}] {agent_issue.message}{suffix}"
            )

    if issues:
        for line in issues:
            logger.error("validate: %s", line)
        logger.error("validate failed: %d issue(s) in %s", len(issues), root)
        return 1

    logger.info("validate OK: %s", root)
    return 0


def print_usage() -> None:
    """Print CLI usage information."""
    print(
        """
jvagent - Agentive Platform

    Usage:
        jvagent [<app_root>] [run] [--update] [--debug] [--serverless]   Start the jvagent server (default)
        jvagent <app_root> [run] [--update] [--debug] [--serverless]    Start server with app root path
                                --update: Update existing agents/actions from YAML files
                                --serverless: Simulate serverless runtime (single-threaded, no background tasks)
    jvagent [run] [--debug] --stress-seed --user-memory-nodes N --interactions-per-user-memory-node M ...
                                After bootstrap, populate the memory graph, then start the server (same DB)
    jvagent [<app_root>] status             Show application status
    jvagent [<app_root>] validate         Check app.yaml and agent.yaml structure (exit 1 if issues; for CI)
    jvagent [<app_root>] stress-seed --user-memory-nodes N --interactions-per-user-memory-node M
                                  Seed synthetic User + Interaction graph (stress testing)
    jvagent [<app_root>] bootstrap [--update]  Bootstrap application graph
                                  --update: Update existing agents/actions from YAML files
    jvagent [<app_root>] bundle [<app_root>]
                                  Generate Dockerfile in app directory
                                  Discovers action dependencies from info.yaml files
                                  App root can be specified before or after 'bundle' command
                                  Defaults to current working directory if not specified
    jvagent chat [--url URL] [--port N] [--host HOST] [--no-browser]
                                  Serve the bundled jvchat web UI on its own port (default 3000)
                                  --url injects the agent server URL the UI talks to (no rebuild)
    jvagent [<app_root>] agent create [SPEC] [--profile NAME] [--action ID]... [--force]
                                  Scaffold agents/<ns>/<id>/ and register in app.yaml
                                  SPEC: namespace/agent or namespace/agent@profile
    jvagent [<app_root>] skill add <agent_ref> <skill_name> [--description TEXT] [--force]
                                  Create agents/<ns>/<id>/skills/<skill_name>/SKILL.md starter
    jvagent [<app_root>] skill list [--agent <agent_ref>] [--builtin]
                                  List reusable and/or app-local skill bundles
    jvagent [<app_root>] skill show <skill_name> [--agent <agent_ref>] [--builtin]
                                  Show one skill bundle's metadata and SOP
    jvagent [<app_root>] agent list         List all installed agents
    jvagent [<app_root>] agent uninstall <name>    Uninstall an agent
    jvagent app create [--dir PATH] [--app-id ID] ...   Scaffold a new application tree
    jvagent app profile new <name> [--extends PROFILE]   Add profiles/<name>.yaml (from app root)
    jvagent [<app_root>] action list <agent_name>  List actions for an agent
    jvagent [<app_root>] action enable <agent_name> <action_id>   Enable an action
    jvagent [<app_root>] action disable <agent_name> <action_id>  Disable an action

Note: Agents are installed automatically from app.yaml when you run jvagent or bootstrap.
      Use `jvagent app create` or `jvagent agent create` to scaffold YAML, then bootstrap.
      Without `--update`, the next YAML sync mode can be set on the App node (`update_mode`: run | merge | source)
      via admin `PUT /api/app/update_mode` and applies on the next start; after a successful start it resets to run.
      CLI `--update` always overrides the stored value for that invocation.

Arguments:
    <app_root>                Path to the app root directory (default: current directory)
                              Must be a valid directory path. If not provided, uses current working directory.

Flags:
    --update                   Update existing agents and actions from YAML files (non-destructive merge).
                                Applies source changes while preserving database state.
    --update --source          Destructive update: fully overwrite database state from source YAML files.
                                Deletes and recreates action nodes (child nodes are lost).
    --update --merge           Explicit non-destructive merge (same as --update alone).
    --purge                    Delete local app, logging, and PageIndex stores (json/sqlite only; development mode)
    --debug                    Enable debug logging (verbose output for troubleshooting)
    --serverless              Simulate serverless execution environment (single-threaded, no background tasks)

Environment Variables:
    JVAGENT_ADMIN_PASSWORD     Admin user password (required)
    JVAGENT_HOST              Server host (default: 127.0.0.1)
    JVAGENT_PORT              Server port (default: 8000)
    JVSPATIAL_DB_PATH         Database path (default: ./jvagent_db)
    JVSPATIAL_FILES_ROOT_PATH File storage path (default: ./.files)

Examples:
    jvagent                                    # Run from current directory
    jvagent /path/to/my_app                    # Run from specified app directory
    jvagent /path/to/my_app --update           # Run with merge update (non-destructive)
    jvagent /path/to/my_app --update --source  # Run with source update (destructive)
    jvagent --serverless                      # Run with serverless runtime simulation
    jvagent /path/to/my_app bootstrap          # Bootstrap from specified directory
    jvagent /path/to/my_app bootstrap --update # Bootstrap with merge update
    jvagent --stress-seed --user-memory-nodes 50 --interactions-per-user-memory-node 20
    jvagent stress-seed --user-memory-nodes 50 --interactions-per-user-memory-node 20
    jvagent /path/to/my_app bundle             # Generate Dockerfile in app directory
    jvagent bundle /path/to/my_app             # Generate Dockerfile (path after command)
    jvagent bundle                             # Generate Dockerfile in current directory
    jvagent app create --yes --dir ./my_app --app-id my_app --title T --description D --author A --agent jvagent/bot@minimal
    jvagent agent create acme/bot@conversational
    jvagent app profile new my_profile --extends minimal
    """
    )


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
        run_kwargs = {}
        if is_serverless_mode():
            run_kwargs["workers"] = 1
            run_kwargs["reload"] = False
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
