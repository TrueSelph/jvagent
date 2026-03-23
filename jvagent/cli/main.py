"""jvagent CLI main entry point."""

import asyncio
import logging
import os
import sys
from pathlib import Path

from jvspatial.logging import configure_standard_logging

from jvagent.cli import app_commands
from jvagent.cli.commands import (
    bootstrap_only,
    handle_action_command,
    handle_agent_command,
    handle_bundle_command,
    load_app_env,
    print_usage,
    purge_app_data,
    run_server,
    show_status,
)
from jvagent.cli.server_config import _set_db_env_from_config
from jvagent.core.config import is_development_mode
from jvagent.env import load_env

configure_standard_logging(
    level=load_env().log_level or "INFO",
    enable_colors=True,
    preserve_handler_class_names=["DBLogHandler", "StartupLogCounter"],
)
logger = logging.getLogger(__name__)

# Suppress noisy asyncio selector logs
logging.getLogger("asyncio").setLevel(logging.WARNING)


def main() -> None:
    """Main entry point for jvagent application."""
    # Parse command-line arguments
    args = sys.argv[1:]

    # Extract app root path (first positional argument that's not a flag or command)
    # This handles both: "jvagent /path/to/app bundle" and "jvagent bundle /path/to/app"
    app_root = None
    commands = ["run", "status", "agent", "action", "bootstrap", "bundle", "app"]
    flags = ["--debug", "--update", "--purge", "--source", "--merge", "--serverless"]

    # Find app root: first argument that's not a command or flag
    # This extracts paths whether they appear before or after the command
    for i, arg in enumerate(args):
        if arg not in commands and arg not in flags and not arg.startswith("-"):
            # Check if it's a valid path
            potential_path = Path(arg).expanduser().resolve()
            if potential_path.exists() and potential_path.is_dir():
                app_root = str(potential_path)
                args = args[:i] + args[i + 1 :]  # Remove from args
                break

    # Default to current working directory if not provided
    # This handles: "cd /path/to/app && jvagent bundle"
    if app_root is None:
        app_root = os.getcwd()

    logger.debug(f"Using app root: {app_root}")

    # Load .env first so JVAGENT_APP_ID and other vars override app.yaml before any other code runs
    load_app_env(app_root=app_root)

    # Set the global app root for config loading in other modules
    from jvagent.core.app_context import set_app_root

    set_app_root(app_root)

    # Reload performance configs now that app root is set
    from jvagent.core.cache import reload_performance_config
    from jvagent.core.profiling import reload_profiling_config

    reload_performance_config()
    reload_profiling_config()

    # Check for --serverless flag (overrides .env; simulates serverless + Lambda for local dev)
    if "--serverless" in args:
        args = [arg for arg in args if arg != "--serverless"]
        os.environ["SERVERLESS_MODE"] = "true"
        os.environ["AWS_LAMBDA_FUNCTION_NAME"] = "jvagent-serverless"
        logger.info(
            "Serverless mode enabled (single worker, jvspatial SERVERLESS_MODE=true)"
        )

    _set_db_env_from_config(app_root)

    # Check for --debug flag
    debug_flag = "--debug" in args
    if debug_flag:
        args = [arg for arg in args if arg != "--debug"]
        # Set logging to DEBUG level for root and all jvagent loggers
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        for handler in root_logger.handlers:
            handler.setLevel(logging.DEBUG)
        # Also set DEBUG level for all jvagent loggers to ensure they inherit properly
        logging.getLogger("jvagent").setLevel(logging.DEBUG)

    # Check for --update flag and sub-flags (--source / --merge)
    has_update = "--update" in args
    has_source = "--source" in args
    has_merge = "--merge" in args

    if has_update:
        if has_source:
            update_mode = "source"
        else:
            update_mode = "merge"
    else:
        update_mode = None
        if has_source or has_merge:
            logger.warning("--source/--merge flags have no effect without --update")

    args = [arg for arg in args if arg not in ["--update", "--source", "--merge"]]

    # Check for --purge flag (development mode only)
    purge_flag = "--purge" in args
    if purge_flag:
        args = [arg for arg in args if arg != "--purge"]

        if not is_development_mode():
            logger.error("The --purge flag is only allowed in development mode.")
            logger.error(
                "Set JVAGENT_ENVIRONMENT=development or ensure you are not in production mode."
            )
            sys.exit(1)

        purge_app_data(app_root=app_root)

    # If no arguments or "run" command, start the server
    if not args or args[0] == "run":
        run_server(update_mode=update_mode, debug=debug_flag, app_root=app_root)
    elif args[0] == "status":
        # Show application status
        asyncio.run(show_status(app_root=app_root))
    elif args[0] == "app":
        app_commands.handle_app_command(args[1:], default_cwd=app_root)
    elif args[0] == "agent":
        # Agent management commands
        handle_agent_command(args[1:], app_root=app_root)
    elif args[0] == "action":
        # Action management commands
        handle_action_command(args[1:], app_root=app_root)
    elif args[0] == "bootstrap":
        # Bootstrap application graph
        asyncio.run(bootstrap_only(update_mode=update_mode, app_root=app_root))
    elif args[0] == "bundle":
        # Bundle application for deployment
        handle_bundle_command(args[1:], app_root=app_root)
    else:
        print_usage()
