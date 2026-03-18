"""CLI command handlers for jvagent."""

import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

from jvagent.cli.bootstrap import bootstrap_application_graph, ensure_admin_user
from jvagent.cli.server_config import (
    _set_db_env_from_config,
    create_server_from_config,
    pre_startup_bootstrap,
)
from jvagent.core.bootstrap_logger import BootstrapLogger
from jvagent.core.config import (
    get_config_value,
    is_development_mode,
    load_app_config,
    resolve_db_path,
    resolve_log_db_path,
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


def purge_app_data(app_root: str) -> None:
    """Purge application data (database and logs).

    Reads database configuration from app.yaml and environment variables to determine
    the actual paths to purge. Resolves relative paths relative to app_root.

    Args:
        app_root: Path to the app root directory.
    """
    if app_root is None:
        app_root = os.getcwd()

    app_config = load_app_config(app_root)
    db_type = get_config_value(app_config, "database.type", "JVSPATIAL_DB_TYPE", "json")
    db_path = resolve_db_path(app_root, app_config, db_type)
    log_db_path = resolve_log_db_path(app_root, app_config)

    paths_to_purge = []
    if db_path:
        paths_to_purge.append(Path(db_path).resolve())
    if log_db_path:
        paths_to_purge.append(Path(log_db_path).resolve())

    logger.warning(f"Purging application data from {app_root}...")

    for dir_path in paths_to_purge:
        if dir_path.exists():
            try:
                shutil.rmtree(dir_path)
                logger.info(f"Deleted directory: {dir_path}")
            except Exception as e:
                logger.error(f"Failed to delete {dir_path}: {e}")
        else:
            logger.debug(f"Directory not found (skipping): {dir_path}")

    logger.info("Purge complete.")


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
    jvagent [<app_root>] status             Show application status
    jvagent [<app_root>] bootstrap [--update]  Bootstrap application graph
                                  --update: Update existing agents/actions from YAML files
    jvagent [<app_root>] bundle [<app_root>]
                                  Generate Dockerfile in app directory
                                  Discovers action dependencies from info.yaml files
                                  App root can be specified before or after 'bundle' command
                                  Defaults to current working directory if not specified
    jvagent [<app_root>] agent list         List all installed agents
    jvagent [<app_root>] agent uninstall <name>    Uninstall an agent
    jvagent [<app_root>] action list <agent_name>  List actions for an agent
    jvagent [<app_root>] action enable <agent_name> <action_id>   Enable an action
    jvagent [<app_root>] action disable <agent_name> <action_id>  Disable an action

Note: Agents are installed automatically from app.yaml when you run jvagent or bootstrap.
      There is no direct agent installation command - agents must be defined in app.yaml.

Arguments:
    <app_root>                Path to the app root directory (default: current directory)
                              Must be a valid directory path. If not provided, uses current working directory.

Flags:
    --update                   Update existing agents and actions from YAML files (non-destructive merge).
                                Applies source changes while preserving database state.
    --update --source          Destructive update: fully overwrite database state from source YAML files.
                                Deletes and recreates action nodes (child nodes are lost).
    --update --merge           Explicit non-destructive merge (same as --update alone).
    --purge                    Delete existing database and logs before starting (development mode only)
    --debug                    Enable debug logging (verbose output for troubleshooting)
    --serverless              Simulate serverless execution environment (single-threaded, no background tasks)

Environment Variables:
    JVAGENT_ADMIN_PASSWORD     Admin user password (required)
    JVAGENT_HOST              Server host (default: 127.0.0.1)
    JVAGENT_PORT              Server port (default: 8000)
    JVSPATIAL_DB_PATH         Database path (default: ./jvagent_db)
    JVSPATIAL_FILES_ROOT_PATH File storage path (default: .files)

Examples:
    jvagent                                    # Run from current directory
    jvagent /path/to/my_app                    # Run from specified app directory
    jvagent /path/to/my_app --update           # Run with merge update (non-destructive)
    jvagent /path/to/my_app --update --source  # Run with source update (destructive)
    jvagent --serverless                      # Run with serverless runtime simulation
    jvagent /path/to/my_app bootstrap          # Bootstrap from specified directory
    jvagent /path/to/my_app bootstrap --update # Bootstrap with merge update
    jvagent /path/to/my_app bundle             # Generate Dockerfile in app directory
    jvagent bundle /path/to/my_app             # Generate Dockerfile (path after command)
    jvagent bundle                             # Generate Dockerfile in current directory
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
    update_mode: Optional[str] = None, debug: bool = False, app_root: str = None
) -> None:
    """Start the jvagent server.

    Args:
        update_mode: Update strategy - "merge" for non-destructive merge, "source" for
                     destructive overwrite from YAML, or None to skip existing.
        debug: If True, enable debug logging.
        app_root: Path to the app root directory. If None, uses current working directory.
    """
    if app_root is None:
        app_root = os.getcwd()

    # Note: Database path environment variables are set in create_server_from_config
    # with proper resolution against app_root

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
        if os.environ.get("AWS_LAMBDA_FUNCTION_NAME") or os.environ.get(
            "JVAGENT_SERVERLESS"
        ):
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

    # Install log counter to track warnings and errors during bootstrap
    log_counter = StartupLogCounter()
    root_logger = logging.getLogger()
    root_logger.addHandler(log_counter)

    try:
        await bootstrap_application_graph(update_mode=update_mode, app_root=app_root)

        # Initialize all actions by calling their on_startup() hooks
        # This ensures runtime components like channel adapters are initialized
        from jvagent.core.startup import run_app_startup

        await run_app_startup()

        await ensure_admin_user()

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

        if update_mode == "source":
            print(
                "Bootstrap complete! (Updated existing agents and actions from source)"
            )
        elif update_mode == "merge":
            print("Bootstrap complete! (Merged source changes, preserved DB state)")
        else:
            print("Bootstrap complete! (Used existing agents and actions)")
    finally:
        # Remove the log counter handler
        root_logger.removeHandler(log_counter)


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
    """Handle agent management commands.

    Note: Agents are installed automatically from app.yaml when running jvagent or bootstrap.
    This command is for listing and uninstalling existing agents only.

    Args:
        args: Command arguments
        app_root: Path to the app root directory. If None, uses current working directory.
    """
    if app_root is None:
        app_root = os.getcwd()

    if not args:
        print("Usage: jvagent agent <command>")
        print("Commands: list, uninstall")
        print("\nNote: Agents are installed automatically from app.yaml.")
        print(
            "      To install agents, add them to app.yaml and run 'jvagent' or 'jvagent bootstrap'."
        )
        return

    command = args[0]

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
        print("Available commands: list, uninstall")
        print("\nNote: Agents are installed automatically from app.yaml.")
        print(
            "      To install agents, add them to app.yaml and run 'jvagent' or 'jvagent bootstrap'."
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
