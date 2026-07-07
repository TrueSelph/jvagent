"""CLI command handlers for jvagent."""

import argparse
import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

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
    is_development_mode,
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
                                  Seed synthetic UserLongMemoryNode + Interaction graph (stress testing)
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


def _handle_agent_create_command(args: List[str], app_root: str = None) -> None:
    """Scaffold a new agent directory and register it in app.yaml."""
    if app_root is None:
        app_root = os.getcwd()

    parser = argparse.ArgumentParser(prog="jvagent agent create")
    parser.add_argument(
        "spec",
        nargs="?",
        help="namespace/agent_id or namespace/agent_id@profile",
    )
    parser.add_argument(
        "--profile",
        default="orchestrator",
        help=(
            "Profile when spec has no @profile (default: orchestrator). "
            "Builtins: orchestrator, minimal, conversational, research, "
            "whatsapp_voice."
        ),
    )
    parser.add_argument(
        "--action",
        action="append",
        dest="actions",
        default=[],
        help="Extra action id (repeatable)",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--author", default=None)
    parser.add_argument("--jvagent-version", default=None, dest="jvagent_spec")

    ns = parser.parse_args(args)
    spec = ns.spec
    if not spec:
        if sys.stdin.isatty():
            spec = input("Agent (namespace/agent or namespace/agent@profile): ").strip()
        if not spec:
            parser.error("agent spec is required")

    from jvagent import __version__ as jvagent_version
    from jvagent.scaffold.operations import CreateAgentContext, create_agent_in_app

    jv_spec = ns.jvagent_spec or f"~{jvagent_version}"

    try:
        create_agent_in_app(
            CreateAgentContext(
                app_root=Path(app_root),
                agent_spec=spec,
                default_profile=ns.profile,
                extra_action_flags=list(ns.actions or []),
                force=ns.force,
                author=ns.author,
                version=ns.version,
                jvagent_spec=jv_spec,
            )
        )
    except (FileExistsError, FileNotFoundError, ValueError) as e:
        logger.error("%s", e)
        sys.exit(1)

    print(f"\nAgent scaffolded under {app_root}/agents/")
    print(
        "Run: jvagent bootstrap --update   (or jvagent --update) to load the new agent."
    )


def _build_skill_stub(skill_name: str, description: str) -> str:
    return f"""---
name: {skill_name}
description: {description}
allowed-tools: []
---

## Workflow

1. Clarify the objective and constraints.
2. Use available tools to gather evidence.
3. Return a concise, actionable result.
"""


def _handle_skill_add_command(args: List[str], app_root: str = None) -> None:
    """Create a Claude-style SKILL.md bundle for an agent."""
    if app_root is None:
        app_root = os.getcwd()

    parser = argparse.ArgumentParser(prog="jvagent skill add")
    parser.add_argument("agent_ref", help="namespace/agent_id")
    parser.add_argument("skill_name", help="Skill bundle folder/name")
    parser.add_argument(
        "--description",
        default="A skill bundle for the thinking interact action.",
        help="Frontmatter description for SKILL.md",
    )
    parser.add_argument("--force", action="store_true")
    ns = parser.parse_args(args)

    if "/" not in ns.agent_ref:
        parser.error("agent_ref must be in format namespace/agent_id")

    namespace, agent_name = ns.agent_ref.split("/", 1)
    agent_dir = Path(app_root).resolve() / "agents" / namespace / agent_name
    if not agent_dir.is_dir():
        parser.error(f"agent directory not found: {agent_dir}")

    skills_dir = agent_dir / "skills"
    skill_dir = skills_dir / ns.skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"

    if skill_path.exists() and not ns.force:
        parser.error(f"{skill_path} already exists (use --force to overwrite)")

    skill_path.write_text(
        _build_skill_stub(ns.skill_name, ns.description), encoding="utf-8"
    )
    print(f"Wrote {skill_path}")


def _handle_skill_create_leadgen_command(args: List[str], app_root: str = None) -> None:
    """Scaffold a LeadGenAction skill from the example_leadgen template."""
    import shutil

    if app_root is None:
        app_root = os.getcwd()

    parser = argparse.ArgumentParser(prog="jvagent skill create-leadgen")
    parser.add_argument("agent_ref", help="namespace/agent_id")
    parser.add_argument("skill_name", help="Skill folder/name under agents/.../skills/")
    parser.add_argument(
        "--title",
        default="",
        help="Leadgen title in frontmatter (defaults to skill_name title case)",
    )
    parser.add_argument("--force", action="store_true")
    ns = parser.parse_args(args)

    if "/" not in ns.agent_ref:
        parser.error("agent_ref must be in format namespace/agent_id")

    namespace, agent_name = ns.agent_ref.split("/", 1)
    agent_dir = Path(app_root).resolve() / "agents" / namespace / agent_name
    if not agent_dir.is_dir():
        parser.error(f"agent directory not found: {agent_dir}")

    template_dir = (
        Path(__file__).resolve().parent.parent
        / "action"
        / "leadgen"
        / "examples"
        / "example_leadgen"
    )
    if not template_dir.is_dir():
        parser.error(f"example_leadgen template not found: {template_dir}")

    dest = agent_dir / "skills" / ns.skill_name
    if dest.exists() and not ns.force:
        parser.error(f"{dest} already exists (use --force to overwrite)")

    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(template_dir, dest)

    title = ns.title or ns.skill_name.replace("_", " ").title()
    skill_md = dest / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    text = text.replace("name: example_leadgen", f"name: {ns.skill_name}", 1)
    text = text.replace("title: Product Inquiry Leads", f"title: {title}", 1)
    skill_md.write_text(text, encoding="utf-8")

    custom_tools = dest / "scripts" / "custom_tools.py"
    ct = custom_tools.read_text(encoding="utf-8")
    ct = ct.replace(
        '_SKILL_NAME = "example_leadgen"', f'_SKILL_NAME = "{ns.skill_name}"'
    )
    custom_tools.write_text(ct, encoding="utf-8")

    from jvagent.action.leadgen._validate_contract import validate_leadgen_skill_dir

    ok, issues = validate_leadgen_skill_dir(dest)
    print(f"Created leadgen skill at {dest}")
    if ok:
        print("Contract validation: PASSED")
    else:
        print("Contract validation: FAILED")
        for issue in issues:
            print(f"  - {issue}")
    print(
        "Next: register the skill in agent.yaml orchestrator skills: "
        "and enable jvagent/leadgen in actions."
    )


def _handle_skill_create_interview_command(
    args: List[str], app_root: str = None
) -> None:
    """Scaffold an InterviewAction skill from the example_interview template."""
    import shutil

    if app_root is None:
        app_root = os.getcwd()

    parser = argparse.ArgumentParser(prog="jvagent skill create-interview")
    parser.add_argument("agent_ref", help="namespace/agent_id")
    parser.add_argument("skill_name", help="Skill folder/name under agents/.../skills/")
    parser.add_argument(
        "--title",
        default="",
        help="Interview title in frontmatter (defaults to skill_name title case)",
    )
    parser.add_argument("--force", action="store_true")
    ns = parser.parse_args(args)

    if "/" not in ns.agent_ref:
        parser.error("agent_ref must be in format namespace/agent_id")

    namespace, agent_name = ns.agent_ref.split("/", 1)
    agent_dir = Path(app_root).resolve() / "agents" / namespace / agent_name
    if not agent_dir.is_dir():
        parser.error(f"agent directory not found: {agent_dir}")

    template_dir = (
        Path(__file__).resolve().parent.parent
        / "action"
        / "interview"
        / "examples"
        / "example_interview"
    )
    if not template_dir.is_dir():
        parser.error(f"example_interview template not found: {template_dir}")

    dest = agent_dir / "skills" / ns.skill_name
    if dest.exists() and not ns.force:
        parser.error(f"{dest} already exists (use --force to overwrite)")

    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(template_dir, dest)

    title = ns.title or ns.skill_name.replace("_", " ").title()
    skill_md = dest / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    text = text.replace("name: example_interview", f"name: {ns.skill_name}", 1)
    text = text.replace("example_interview__", f"{ns.skill_name}__")
    text = text.replace("title: Product Feedback", f"title: {title}", 1)
    skill_md.write_text(text, encoding="utf-8")

    custom_tools = dest / "scripts" / "custom_tools.py"
    ct = custom_tools.read_text(encoding="utf-8")
    ct = ct.replace(
        '_SKILL_NAME = "example_interview"', f'_SKILL_NAME = "{ns.skill_name}"'
    )
    custom_tools.write_text(ct, encoding="utf-8")

    from jvagent.action.interview._validate_contract import (
        validate_interview_skill_dir,
    )

    ok, issues = validate_interview_skill_dir(dest)
    print(f"Created interview skill at {dest}")
    if ok:
        print("Contract validation: PASSED")
    else:
        print("Contract validation: FAILED")
        for issue in issues:
            print(f"  - {issue}")
    print(
        "Next: register the skill in agent.yaml orchestrator skills: "
        "and enable jvagent/interview in actions."
    )


def _resolve_skills_for_cli(
    *,
    app_root: str,
    agent_ref: Optional[str],
    include_builtin: bool,
) -> Dict[str, Dict[str, Any]]:
    from jvagent.scaffold.skill_resolve import (
        resolve_agent_skills,
        resolve_builtin_skills,
        resolve_merged_skill_bundles,
    )

    if agent_ref:
        if "/" not in agent_ref:
            raise ValueError("agent_ref must be in format namespace/agent_id")
        namespace, agent_name = agent_ref.split("/", 1)
        if include_builtin:
            return resolve_merged_skill_bundles(
                app_root=app_root,
                namespace=namespace,
                agent_name=agent_name,
                include_builtin=True,
            )
        return resolve_agent_skills(app_root, namespace, agent_name)

    # No agent specified: default to builtin catalog for discoverability.
    return resolve_builtin_skills()


def _load_agent_thinking_skill_config(
    app_root: str,
    agent_ref: str,
) -> Dict[str, Any]:
    """Load thinking skill selector settings from an agent's agent.yaml."""
    import yaml

    if "/" not in agent_ref:
        raise ValueError("agent_ref must be in format namespace/agent_id")

    namespace, agent_name = agent_ref.split("/", 1)
    agent_yaml = (
        Path(app_root).resolve() / "agents" / namespace / agent_name / "agent.yaml"
    )
    if not agent_yaml.is_file():
        return {"skills": None, "denied_skills": [], "skills_source": "both"}

    try:
        data = yaml.safe_load(agent_yaml.read_text(encoding="utf-8")) or {}
    except Exception:
        return {"skills": None, "denied_skills": [], "skills_source": "both"}

    actions = data.get("actions", []) if isinstance(data, dict) else []
    if not isinstance(actions, list):
        return {"skills": None, "denied_skills": [], "skills_source": "both"}

    for action in actions:
        if not isinstance(action, dict):
            continue
        if action.get("action") != "jvagent/skill_interact_action":
            continue
        context = action.get("context", {})
        if not isinstance(context, dict):
            context = {}
        return {
            "skills": context.get("skills"),
            "denied_skills": context.get("denied_skills", []),
            "skills_source": context.get("skills_source", "both"),
        }

    return {"skills": None, "denied_skills": [], "skills_source": "both"}


def _handle_skill_list_command(args: List[str], app_root: str = None) -> None:
    """List available skill bundles."""
    if app_root is None:
        app_root = os.getcwd()

    parser = argparse.ArgumentParser(prog="jvagent skill list")
    parser.add_argument("--agent", default=None, help="namespace/agent_id")
    parser.add_argument(
        "--builtin",
        action="store_true",
        help="Include built-in skills when --agent is used",
    )
    ns = parser.parse_args(args)

    try:
        bundles = _resolve_skills_for_cli(
            app_root=app_root,
            agent_ref=ns.agent,
            include_builtin=ns.builtin,
        )
    except ValueError as exc:
        parser.error(str(exc))

    if not bundles:
        print("No skills found.")
        return

    active_skill_names: Set[str] = set()
    if ns.agent:
        from jvagent.scaffold.skill_resolve import apply_skill_selector

        config = _load_agent_thinking_skill_config(
            app_root=app_root, agent_ref=ns.agent
        )
        selected = apply_skill_selector(
            bundles=bundles,
            selector=config.get("skills"),
            denied=config.get("denied_skills"),
        )
        active_skill_names = set(selected.keys())

        selector = config.get("skills")
        source = config.get("skills_source", "both")
        denied = config.get("denied_skills", [])
        print("\n=== Skill selector ===\n")
        print(f"agent: {ns.agent}")
        print(f"skills_source: {source}")
        print(f"skills: {selector!r}")
        print(f"denied_skills: {denied!r}")

    print("\n=== Skills ===\n")
    for skill_name in sorted(bundles.keys()):
        bundle = bundles[skill_name]
        source = bundle.get("source", "unknown")
        description = bundle.get("description", "")
        if ns.agent:
            status = "active" if skill_name in active_skill_names else "excluded"
            print(f"- {skill_name} [{source}] [{status}]")
        else:
            print(f"- {skill_name} [{source}]")
        if description:
            print(f"  {description}")
    print()


def _handle_skill_show_command(args: List[str], app_root: str = None) -> None:
    """Show one skill bundle in detail."""
    if app_root is None:
        app_root = os.getcwd()

    parser = argparse.ArgumentParser(prog="jvagent skill show")
    parser.add_argument("skill_name")
    parser.add_argument("--agent", default=None, help="namespace/agent_id")
    parser.add_argument(
        "--builtin",
        action="store_true",
        help="Include built-in skills when --agent is used",
    )
    ns = parser.parse_args(args)

    try:
        bundles = _resolve_skills_for_cli(
            app_root=app_root,
            agent_ref=ns.agent,
            include_builtin=ns.builtin,
        )
    except ValueError as exc:
        parser.error(str(exc))

    bundle = bundles.get(ns.skill_name)
    if not bundle:
        print(f"Skill not found: {ns.skill_name}")
        return

    print(f"\n=== Skill: {ns.skill_name} ===")
    print(f"Source: {bundle.get('source', 'unknown')}")
    print(f"Directory: {bundle.get('dir', '')}")
    print(f"Description: {bundle.get('description', '')}")
    allowed_tools = bundle.get("allowed_tools", [])
    print(f"Allowed tools: {', '.join(allowed_tools) if allowed_tools else '(all)'}")
    tool_files = bundle.get("tool_files", [])
    print(f"Tool files: {len(tool_files)}")
    if tool_files:
        for path in tool_files:
            print(f"  - {path}")
    content = bundle.get("content", "")
    if content:
        print("\n--- SKILL.md content ---\n")
        print(content)
    print()


def handle_bundle_command(args: List[str], app_root: str = None) -> None:
    """Handle bundle command - generates Dockerfile in app directory.

    Supports both:
    - jvagent /path/to/app bundle
    - jvagent bundle /path/to/app
    - jvagent bundle (uses current working directory)

    Args:
        args: Command arguments (may contain app root path)
        app_root: Path to the app root directory. If None, checks args or uses current working directory.
    """
    # If app_root not provided, check if first arg is a path
    if app_root is None:
        if args and args[0]:
            potential_path = Path(args[0]).expanduser().resolve()
            if potential_path.exists() and potential_path.is_dir():
                app_root = str(potential_path)
                logger.debug(f"Using app root from command argument: {app_root}")
            else:
                app_root = os.getcwd()
                logger.debug(
                    f"Argument '{args[0]}' is not a valid path, using current working directory"
                )
        else:
            app_root = os.getcwd()
            logger.debug(f"Using current working directory as app root: {app_root}")

    # Create bundler
    from jvagent.bundle import Bundler

    bundler = Bundler(app_root=app_root)

    # Generate Dockerfile
    success = bundler.generate_dockerfile()

    if not success:
        logger.error("Dockerfile generation failed")
        sys.exit(1)

    print(f"\n✓ Dockerfile generated successfully in {app_root}")


def handle_agent_command(args: List[str], app_root: str = None) -> None:
    """Handle agent management commands (create, list, uninstall).

    Agents are loaded from ``app.yaml`` on bootstrap/run. Use ``agent create`` to scaffold
    YAML under ``agents/`` and register the agent, then ``bootstrap --update``.

    Args:
        args: Command arguments
        app_root: Path to the app root directory. If None, uses current working directory.
    """
    if app_root is None:
        app_root = os.getcwd()

    if not args:
        print("Usage: jvagent agent <command>")
        print("Commands: create, list, uninstall")
        print("\nNote: Agents are installed automatically from app.yaml.")
        print(
            "      To install agents, add them to app.yaml and run 'jvagent' or 'jvagent bootstrap'."
        )
        return

    command = args[0]

    if command == "create":
        _handle_agent_create_command(args[1:], app_root=app_root)
        return

    _set_db_env_from_config(app_root)

    if command == "list":
        asyncio.run(list_agents())
    elif command == "uninstall":
        if len(args) < 2:
            print("Usage: jvagent agent uninstall <namespace/agent_name>")
            return
        agent_ref = args[1]

        # Parse namespace/agent_name format
        if "/" not in agent_ref:
            print("Error: Agent reference must be in format 'namespace/agent_name'")
            return

        namespace, agent_name = agent_ref.split("/", 1)
        asyncio.run(uninstall_agent(namespace, agent_name, app_root=app_root))
    else:
        print(f"Unknown agent command: {command}")
        print("Available commands: create, list, uninstall")
        print("\nNote: Agents are installed automatically from app.yaml.")
        print(
            "      To install agents, add them to app.yaml and run 'jvagent' or 'jvagent bootstrap'."
        )


def _handle_skill_validate_command(args: List[str], app_root: str = None) -> None:
    """Validate a SKILL.md file without requiring agent context."""
    from pathlib import Path as _Path

    parser = argparse.ArgumentParser(prog="jvagent skill validate")
    parser.add_argument(
        "path",
        help="Path to a SKILL.md file or a skill bundle directory containing one.",
    )
    ns = parser.parse_args(args)

    target = _Path(ns.path).expanduser().resolve()
    if target.is_dir():
        target = target / "SKILL.md"
    if not target.is_file():
        parser.error(f"SKILL.md not found at {target}")

    from jvagent.scaffold.skill_resolve import parse_skill_bundle

    print(f"Validating: {target}")
    bundle = parse_skill_bundle(target.parent, source="builtin")
    if bundle is None:
        print("FAILED: Could not parse SKILL.md")
        return

    print("PASSED (bundle parse)")
    print(f"  name:            {bundle['name']}")
    print(f"  description:     {bundle.get('description', '')[:80]}")
    print(f"  tools:           {len(bundle.get('tool_files', []))}")
    print(f"  requires_actions: {bundle.get('requires_actions', [])}")
    print(f"  exports:         {bundle.get('exports', [])}")
    print(f"  imports:         {bundle.get('imports', [])}")
    print(f"  allowed_tools:   {bundle.get('allowed_tools', [])}")

    from jvagent.action.interview._validate_contract import (
        validate_interview_skill_dir,
    )
    from jvagent.action.interview.spec import (
        load_interview_spec_from_skill,
    )

    if load_interview_spec_from_skill(target.parent) is not None:
        ok, issues = validate_interview_skill_dir(target.parent)
        if ok:
            print("PASSED (interview contract)")
        else:
            print("FAILED (interview contract)")
            for issue in issues:
                print(f"  - {issue}")

    from jvagent.action.leadgen._validate_contract import validate_leadgen_skill_dir
    from jvagent.action.leadgen.spec import load_leadgen_spec_from_skill

    if load_leadgen_spec_from_skill(target.parent) is not None:
        ok, issues = validate_leadgen_skill_dir(target.parent)
        if ok:
            print("PASSED (leadgen contract)")
        else:
            print("FAILED (leadgen contract)")
            for issue in issues:
                print(f"  - {issue}")


def handle_skill_command(args: List[str], app_root: str = None) -> None:
    """Handle skill bundle commands."""
    if app_root is None:
        app_root = os.getcwd()

    if not args:
        print(
            "Usage: jvagent skill <add|create-interview|create-leadgen|list|show|validate> ..."
        )
        return

    command = args[0]
    if command == "add":
        _handle_skill_add_command(args[1:], app_root=app_root)
        return
    if command == "create-interview":
        _handle_skill_create_interview_command(args[1:], app_root=app_root)
        return
    if command == "create-leadgen":
        _handle_skill_create_leadgen_command(args[1:], app_root=app_root)
        return
    if command == "list":
        _handle_skill_list_command(args[1:], app_root=app_root)
        return
    if command == "show":
        _handle_skill_show_command(args[1:], app_root=app_root)
        return
    if command == "validate":
        _handle_skill_validate_command(args[1:], app_root=app_root)
        return

    print(f"Unknown skill command: {command}")
    print(
        "Available commands: add, create-interview, create-leadgen, list, show, validate"
    )


def handle_action_command(args: List[str], app_root: str = None) -> None:
    """Handle action management commands.

    Args:
        args: Command arguments
        app_root: Path to the app root directory. If None, uses current working directory.
    """
    if app_root is None:
        app_root = os.getcwd()

    if not args:
        print("Usage: jvagent action <command>")
        print("Commands: list, enable, disable")
        return

    command = args[0]

    _set_db_env_from_config(app_root)

    if command == "list":
        if len(args) < 2:
            print("Usage: jvagent action list <agent_name>")
            return
        agent_name = args[1]
        asyncio.run(list_actions(agent_name))
    elif command == "enable":
        if len(args) < 3:
            print("Usage: jvagent action enable <agent_name> <action_id>")
            return
        agent_name = args[1]
        action_id = args[2]
        asyncio.run(enable_action(agent_name, action_id))
    elif command == "disable":
        if len(args) < 3:
            print("Usage: jvagent action disable <agent_name> <action_id>")
            return
        agent_name = args[1]
        action_id = args[2]
        asyncio.run(disable_action(agent_name, action_id))
    else:
        print(f"Unknown action command: {command}")


async def list_agents() -> None:
    """List all agents."""
    from jvagent.core.agent import Agent

    agents = await Agent.find({})

    if not agents:
        print("No agents found")
        return

    print(f"\n=== Agents ({len(agents)}) ===\n")
    for agent in agents:
        namespace = getattr(agent, "namespace", "")
        alias = getattr(agent, "alias", "")

        # Display alias if available, otherwise use name
        display_name = alias if alias else agent.name

        if namespace:
            print(f"  - {display_name} ({namespace}/{agent.name})")
        else:
            print(f"  - {display_name} ({agent.name})")
        print(f"    ID: {agent.id}")
        print(f"    Enabled: {agent.enabled}")
        print(f"    Description: {agent.description}")
        print()


async def uninstall_agent(
    namespace: str, agent_name: str, app_root: str = None
) -> None:
    """Uninstall an agent.

    Args:
        namespace: Agent namespace
        agent_name: Agent name
        app_root: Path to the app root directory. If None, uses current working directory.
    """
    from jvagent.core.agent_loader import AgentLoader

    if app_root is None:
        app_root = os.getcwd()

    loader = AgentLoader(app_root)
    success = await loader.uninstall_agent(namespace, agent_name)

    if success:
        print(f"Uninstalled agent: {namespace}/{agent_name}")
    else:
        print(f"Failed to uninstall agent: {namespace}/{agent_name}")


async def list_actions(agent_name: str) -> None:
    """List actions for an agent."""
    from jvagent.core.agent import Agent

    # Find the agent
    agent = await Agent.find_one({"context.name": agent_name})
    if not agent:
        print(f"Agent not found: {agent_name}")
        return

    # Get Actions manager
    actions_manager = await agent.node(node="Actions")

    if not actions_manager:
        print(f"No Actions manager found for agent: {agent_name}")
        return

    # Get actions
    actions_list = await actions_manager.list_actions()

    if not actions_list:
        print(f"No actions found for agent: {agent_name}")
        return

    print(f"\n=== Actions for {agent_name} ({len(actions_list)}) ===\n")
    for action in actions_list:
        print(f"  - {action.get('label')} ({action.get('package_name')})")
        print(f"    ID: {action.get('id')}")
        print(f"    Enabled: {action.get('enabled')}")
        print(f"    Description: {action.get('description')}")
        print(f"    Version: {action.get('version')}")
        print()


async def enable_action(agent_name: str, action_label: str) -> None:
    """Enable an action for an agent."""
    from jvagent.core.agent import Agent

    # Find the agent
    agent = await Agent.find_one({"context.name": agent_name})
    if not agent:
        print(f"Agent not found: {agent_name}")
        return

    # Get Actions manager
    actions_manager = await agent.node(node="Actions")

    if not actions_manager:
        print(f"No Actions manager found for agent: {agent_name}")
        return

    # Get the action
    action = await actions_manager.get_action_by_label(action_label)
    if not action:
        print(f"Action not found: {action_label}")
        return

    # Enable using Action method directly
    await action.enable()
    print(f"Enabled action: {action_label}")


async def disable_action(agent_name: str, action_label: str) -> None:
    """Disable an action for an agent."""
    from jvagent.core.agent import Agent

    # Find the agent
    agent = await Agent.find_one({"context.name": agent_name})
    if not agent:
        print(f"Agent not found: {agent_name}")
        return

    # Get Actions manager
    actions_manager = await agent.node(node="Actions")

    if not actions_manager:
        print(f"No Actions manager found for agent: {agent_name}")
        return

    # Get the action
    action = await actions_manager.get_action_by_label(action_label)
    if not action:
        print(f"Action not found: {action_label}")
        return

    # Disable using Action method directly
    await action.disable()
    print(f"Disabled action: {action_label}")
