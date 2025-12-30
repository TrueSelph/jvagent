"""jvagent CLI Entry Point

Command-line interface for the jvagent application.
"""

import logging
import os
from typing import List

from dotenv import load_dotenv
from jvspatial.api import Server
from jvspatial.api.auth.models import User
from jvspatial.api.auth.service import AuthenticationService
from jvspatial.core import Root

from jvagent import __version__
from jvagent.core import Agents
from jvagent.core.app import App
from jvagent.core.app_loader import AppLoader
from jvagent.core.bootstrap_logger import BootstrapLogger

# Configure logging (will be updated based on --debug flag)
from jvspatial.logging import configure_standard_logging

configure_standard_logging(
    level=os.getenv("JVAGENT_LOG_LEVEL", "INFO"), enable_colors=True
)
logger = logging.getLogger(__name__)

# Suppress noisy asyncio selector logs
logging.getLogger("asyncio").setLevel(logging.WARNING)

from jvagent.utils.env import (
    EnvironmentMode,
    get_environment_mode,
    is_development_mode,
    is_production_mode,
)


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

    if os.path.exists(env_path):
        load_dotenv(env_path, override=True)
        logger.info(f"Loaded .env file from: {env_path}")
    else:
        # Still try to load from app root directory
        load_dotenv(env_path, override=True)
        if os.path.exists(env_path):
            logger.info(f"Loaded .env file from: {env_path}")
        else:
            logger.debug(f"No .env file found in app root: {app_root}")


async def bootstrap_application_graph(update_if_exists: bool = False, app_root: str = None) -> None:
    """Bootstrap the application graph with App and Agents nodes.

    If an app.yaml file is found in the app root directory, uses AppLoader to
    bootstrap the application declaratively, including:
    - Creating/updating the App node from app.yaml
    - Installing all agents listed in app.yaml
    - Loading and registering all actions for each agent from agent.yaml files

    Otherwise, falls back to manual bootstrap with basic configuration.

    Args:
        update_if_exists: If True, update existing agents and actions with values from YAML files.
                         If False (default), use existing agents/actions without overwriting their context.
        app_root: Path to the app root directory. If None, uses current working directory.

    All operations are idempotent - existing nodes and connections are preserved.
    """
    if app_root is None:
        app_root = os.getcwd()

    bootstrap_log = BootstrapLogger("Bootstrap")

    # Check if app.yaml exists in app root directory
    app_yaml_path = os.path.join(app_root, "app.yaml")

    if os.path.exists(app_yaml_path):
        mode = "update" if update_if_exists else "sync"
        bootstrap_log.start(f"Application graph ({mode} mode)")

        # Use AppLoader for declarative bootstrap
        app_loader = AppLoader(app_root)
        app = await app_loader.bootstrap_application(update_if_exists=update_if_exists)

        if app:
            bootstrap_log.complete("Application graph ready")
        else:
            bootstrap_log.error("Declarative bootstrap failed - falling back to manual bootstrap")
            await _manual_bootstrap()
    else:
        bootstrap_log.start("Application graph (manual mode, no app.yaml)")
        bootstrap_log.info("No app.yaml found - using manual bootstrap")
        await _manual_bootstrap()
        bootstrap_log.complete("Manual bootstrap complete")


async def _manual_bootstrap() -> None:
    """Manual bootstrap when no app.yaml is available.

    Creates basic App and Agents nodes with default configuration.
    """
    # Step 1: Ensure Root node exists
    root = await Root.get()
    logger.info(f"Root node ready: {root.id}")

    # Step 2: Create App node if it doesn't exist
    app = await App.find_one({"context.name": "jvAgent"})

    if app:
        logger.info(f"App node already exists: {app.id}")
        App._cached_app = app
    else:
        # Create App node with file storage configuration
        app = await App.create(
            name="jvAgent",
            version=__version__,
            description="jvAgent Application",
            file_storage_provider=os.getenv("JVSPATIAL_FILE_INTERFACE", "local"),
            file_storage_root_dir=os.getenv("JVSPATIAL_FILES_ROOT_PATH", ".files"),
            file_storage_enabled=True,
        )
        logger.info(f"Created App node: {app.id}")
        App._cached_app = app

    # Step 3: Ensure App node is connected to Root node
    if not await root.is_connected_to(app):
        await root.connect(app)
        logger.info("Connected App node to Root node")
    else:
        logger.info("App node already connected to Root node")

    # Step 4: Create Agents node if it doesn't exist
    app_connected_nodes = await app.nodes()
    agents = None

    for node in app_connected_nodes:
        if isinstance(node, Agents):
            agents = node
            break

    if agents:
        logger.info(f"Agents node already exists: {agents.id}")
    else:
        agents = await Agents.create(total_agents=0, active_agents=0)
        logger.info(f"Created Agents node: {agents.id}")

    # Step 5: Ensure Agents node is connected to App node
    if not await app.is_connected_to(agents):
        await app.connect(agents)
        logger.info("Connected Agents node to App node")
    else:
        logger.info("Agents node already connected to App node")

    logger.info("Application graph bootstrap complete")


async def ensure_admin_user() -> bool:
    """Ensure a single admin user exists.

    Creates an admin user if one doesn't exist, using credentials from .env.

    Returns:
        True if admin user exists (either already existed or was just created),
        False if admin user could not be created (missing password).
    """
    logger.debug("Checking for admin user...")

    # Get admin credentials from environment
    admin_username = os.getenv("JVAGENT_ADMIN_USERNAME", "admin")
    admin_password = os.getenv("JVAGENT_ADMIN_PASSWORD")
    admin_email = os.getenv("JVAGENT_ADMIN_EMAIL", f"{admin_username}@jvagent.example")

    if not admin_password:
        logger.warning("JVAGENT_ADMIN_PASSWORD not set in .env. " "Admin user will not be created.")
        return False

    # Check if admin user already exists by email
    existing_user = await User.find_one({"context.email": admin_email})

    if existing_user:
        logger.debug(f"Admin user already exists: {admin_email}")
        return True

    # Create admin user
    # Use AuthenticationService to hash password properly
    auth_service = AuthenticationService()

    # Hash the password
    password_hash = auth_service._hash_password(admin_password)

    # Create user
    admin_user = await User.create(
        email=admin_email, password_hash=password_hash, name=admin_username, is_active=True
    )

    logger.info(f"Created admin user: {admin_email} (ID: {admin_user.id})")
    return True


def create_server_from_config(debug: bool = False) -> Server:
    """Create and configure Server instance from environment variables.

    Returns:
        Configured Server instance with authentication enabled by default.
    """
    # Get configuration from environment variables
    # Server configuration
    title = os.getenv("JVAGENT_TITLE", "jvagent API")
    description = os.getenv("JVAGENT_DESCRIPTION", "jvagent Agentive Platform API")
    version = os.getenv("JVAGENT_VERSION", __version__)
    host = os.getenv("JVAGENT_HOST", "127.0.0.1")
    port = int(os.getenv("JVAGENT_PORT", "8000"))

    # Database configuration
    db_type = os.getenv("JVSPATIAL_DB_TYPE", "json")
    db_path = os.getenv("JVSPATIAL_DB_PATH", "./jvagent_db")
    # Ensure db_path is never None or empty (jvspatial falls back to "./jvdb" if None)
    if not db_path or db_path.strip() == "":
        db_path = "./jvagent_db"
    
    # Set JVSPATIAL_JSONDB_PATH unconditionally to ensure DatabaseManager uses the correct path
    # (DatabaseManager uses JVSPATIAL_JSONDB_PATH, not JVSPATIAL_DB_PATH)
    # This must be set before any database initialization occurs
    if db_type == "json":
        os.environ["JVSPATIAL_JSONDB_PATH"] = db_path

    # Graph endpoint configuration
    graph_endpoint_enabled = os.getenv("JVSPATIAL_GRAPH_ENDPOINT_ENABLED", "false").lower() == "true"

    # Authentication configuration (enabled by default for jvagent)
    auth_enabled = os.getenv("JVAGENT_AUTH_ENABLED", "true").lower() == "true"
    jwt_auth_enabled = os.getenv("JVSPATIAL_JWT_AUTH_ENABLED", "true").lower() == "true"
    jwt_secret = os.getenv("JVSPATIAL_JWT_SECRET", "jvagent-secret-key-change-in-production")
    jwt_expire_minutes = int(os.getenv("JVSPATIAL_JWT_EXPIRE_MINUTES", "60"))

    # Log server creation details only in debug mode
    if debug:
        logger.debug(f"Creating server: {title} v{version}")
        logger.debug(f"Database: {db_type} at {db_path}")
        logger.debug(f"Authentication: {'enabled' if auth_enabled else 'disabled'}")

    # Determine log level based on debug flag or environment variable
    log_level = os.getenv("JVAGENT_LOG_LEVEL", "debug" if debug else "info")

    # Create server with configuration
    server = Server(
        title=title,
        description=description,
        version=version,
        host=host,
        port=port,
        db_type=db_type,
        db_path=db_path,
        auth_enabled=auth_enabled,
        jwt_auth_enabled=jwt_auth_enabled,
        jwt_secret=jwt_secret,
        jwt_expire_minutes=jwt_expire_minutes,
        graph_endpoint_enabled=graph_endpoint_enabled,
        log_level=log_level,
    )

    # Initialize logging database and conditionally load endpoints
    from jvagent.logging.config import initialize_logging_database, get_logging_config
    
    # Only import endpoints if logging is enabled (they will check app-level config at runtime)
    logging_config = get_logging_config()
    if logging_config.get("enabled", True):
        from jvagent.logging import endpoints  # noqa: F401 - Import to register endpoints
    
    initialize_logging_database()

    return server


async def pre_startup_bootstrap(
    server: Server, update_if_exists: bool = False, app_root: str = None
) -> bool:
    """Perform bootstrap tasks before server starts.

    This runs after the server is created (so context is initialized)
    but before the server starts running.

    Args:
        server: Server instance with initialized context
        update_if_exists: If True, update existing agents and actions from YAML files.
                         If False (default), use existing agents/actions without overwriting.
        app_root: Path to the app root directory. If None, uses current working directory.

    Returns:
        True if admin user exists, False otherwise
    """
    try:
        # Bootstrap application graph
        await bootstrap_application_graph(update_if_exists=update_if_exists, app_root=app_root)

        # Ensure admin user exists
        admin_exists = await ensure_admin_user()

        return admin_exists
    except Exception as e:
        logger.error(f"❌ Bootstrap failed: {e}", exc_info=True)
        raise


def disable_register_endpoint(server: Server) -> None:
    """Disable the /auth/register endpoint if admin user exists.

    Uses the server's disable_auth_endpoint method to remove the endpoint
    from the auth router before the app is created.

    Args:
        server: Server instance
    """
    try:
        # Use server's disable_auth_endpoint method to remove the register endpoint
        # This works with auth endpoints registered through the auth router
        # The path should be relative to the router prefix ("/auth")
        # The method will automatically build the full path "/auth/register"
        success = server.disable_auth_endpoint("/register")
        # Endpoint disabling is logged by server.disable_auth_endpoint()
        if not success:
            # If the endpoint wasn't found, check if auth is enabled
            # This might happen if auth is disabled or the endpoint wasn't registered
            if hasattr(server, "_auth_endpoints_registered") and server._auth_endpoints_registered:
                logger.warning(
                    "Could not find /auth/register endpoint to disable "
                    "even though auth endpoints are registered. "
                    "The endpoint may have already been disabled or removed."
                )
            else:
                logger.debug(
                    "Could not find /auth/register endpoint to disable "
                    "(auth may not be enabled or endpoint not yet registered)"
                )
    except Exception as e:
        logger.warning(f"Could not disable register endpoint: {e}")


def main() -> None:
    """Main entry point for jvagent application."""
    import asyncio
    import sys
    from pathlib import Path

    # Parse command-line arguments
    args = sys.argv[1:]

    # Extract app root path (first positional argument that's not a flag or command)
    app_root = None
    commands = ["run", "status", "agent", "action", "bootstrap"]
    flags = ["--debug", "--update", "--migrate"]

    # Find app root: first argument that's not a command or flag
    for i, arg in enumerate(args):
        if arg not in commands and arg not in flags and not arg.startswith("-"):
            # Check if it's a valid path
            potential_path = Path(arg).expanduser().resolve()
            if potential_path.exists() and potential_path.is_dir():
                app_root = str(potential_path)
                args = args[:i] + args[i + 1 :]  # Remove from args
                break

    # Default to current working directory if not provided
    if app_root is None:
        app_root = os.getcwd()

    logger.debug(f"Using app root: {app_root}")

    # Load .env file from app root directory
    load_app_env(app_root=app_root)

    # Set database path environment variables BEFORE any database initialization
    # This must happen before any database operations to prevent jvspatial from using defaults
    db_type = os.getenv("JVSPATIAL_DB_TYPE", "json")
    db_path = os.getenv("JVSPATIAL_DB_PATH", "./jvagent_db")
    if not db_path or db_path.strip() == "":
        db_path = "./jvagent_db"
    # Set JVSPATIAL_JSONDB_PATH unconditionally to ensure DatabaseManager uses correct path
    # (DatabaseManager uses JVSPATIAL_JSONDB_PATH, not JVSPATIAL_DB_PATH)
    if db_type == "json":
        os.environ["JVSPATIAL_JSONDB_PATH"] = db_path

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

    # Check for --update flag
    update_flag = "--update" in args or "--migrate" in args
    if update_flag:
        args = [arg for arg in args if arg not in ["--update", "--migrate"]]

    # If no arguments or "run" command, start the server
    if not args or args[0] == "run":
        run_server(update_if_exists=update_flag, debug=debug_flag, app_root=app_root)
    elif args[0] == "status":
        # Show application status
        asyncio.run(show_status(app_root=app_root))
    elif args[0] == "agent":
        # Agent management commands
        handle_agent_command(args[1:], app_root=app_root)
    elif args[0] == "action":
        # Action management commands
        handle_action_command(args[1:], app_root=app_root)
    elif args[0] == "bootstrap":
        # Bootstrap application graph
        asyncio.run(bootstrap_only(update_if_exists=update_flag, app_root=app_root))
    else:
        print_usage()


def print_usage() -> None:
    """Print CLI usage information."""
    print(
        """
jvagent - Agentive Platform

    Usage:
        jvagent [<app_root>] [run] [--update] [--debug]   Start the jvagent server (default)
        jvagent <app_root> [run] [--update] [--debug]    Start server with app root path
                                --update: Update existing agents/actions from YAML files
    jvagent [<app_root>] status             Show application status
    jvagent [<app_root>] bootstrap [--update]  Bootstrap application graph
                                  --update: Update existing agents/actions from YAML files
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
    --update, --migrate        Force update of existing agents and actions from YAML files
                                By default, existing agents/actions are used as-is
    --debug                    Enable debug logging (verbose output for troubleshooting)

Environment Variables:
    JVAGENT_ADMIN_PASSWORD     Admin user password (required)
    JVAGENT_HOST              Server host (default: 127.0.0.1)
    JVAGENT_PORT              Server port (default: 8000)
    JVSPATIAL_DB_PATH         Database path (default: ./jvagent_db)
    JVSPATIAL_FILES_ROOT_PATH File storage path (default: .files)

Examples:
    jvagent                                    # Run from current directory
    jvagent /path/to/my_app                    # Run from specified app directory
    jvagent /path/to/my_app --update           # Run with update flag
    jvagent /path/to/my_app bootstrap          # Bootstrap from specified directory
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


def run_server(update_if_exists: bool = False, debug: bool = False, app_root: str = None) -> None:
    """Start the jvagent server.

    Args:
        update_if_exists: If True, update existing agents and actions from YAML files.
                         If False (default), use existing agents/actions without overwriting.
        debug: If True, enable debug logging.
        app_root: Path to the app root directory. If None, uses current working directory.
    """
    import asyncio

    if app_root is None:
        app_root = os.getcwd()

    # Set database path environment variables BEFORE any database initialization
    # This must happen before creating the server to prevent jvspatial from using defaults
    db_type = os.getenv("JVSPATIAL_DB_TYPE", "json")
    db_path = os.getenv("JVSPATIAL_DB_PATH", "./jvagent_db")
    if not db_path or db_path.strip() == "":
        db_path = "./jvagent_db"
    # Set JVSPATIAL_JSONDB_PATH unconditionally to ensure DatabaseManager uses correct path
    if db_type == "json":
        os.environ["JVSPATIAL_JSONDB_PATH"] = db_path

    # Install log counter to track warnings and errors during startup
    log_counter = StartupLogCounter()
    root_logger = logging.getLogger()
    root_logger.addHandler(log_counter)

    bootstrap_log = BootstrapLogger("Startup")
    bootstrap_log.start("jvagent application")

    try:
        # Create server from configuration
        server = create_server_from_config(debug=debug)

        # Perform bootstrap tasks before server starts
        admin_exists = asyncio.run(
            pre_startup_bootstrap(server, update_if_exists=update_if_exists, app_root=app_root)
        )

        # If admin user exists, disable the register endpoint
        if admin_exists:
            disable_register_endpoint(server)
            if debug:
                bootstrap_log.info("Admin user configured - registration disabled")
        else:
            bootstrap_log.warning(
                "Admin user not found - registration enabled. "
                "Set JVAGENT_ADMIN_PASSWORD in .env to create admin user."
            )

        # Register startup event to display summary after server has started
        # This ensures the summary appears after all uvicorn logs
        async def show_startup_summary():
            """Display startup summary after server has started."""
            import asyncio
            # Small delay to ensure uvicorn logs appear first
            await asyncio.sleep(0.5)
            
            summary = log_counter.get_summary()
            if summary["total"] > 0:
                summary_parts = []
                if summary["critical"] > 0:
                    summary_parts.append(f"❌ {summary['critical']} critical")
                if summary["errors"] > 0:
                    summary_parts.append(f"❌ {summary['errors']} error{'s' if summary['errors'] != 1 else ''}")
                if summary["warnings"] > 0:
                    summary_parts.append(f"⚠️  {summary['warnings']} warning{'s' if summary['warnings'] != 1 else ''}")
                
                summary_msg = " | ".join(summary_parts)
                logger.warning(f"⚠️  Startup Summary: {summary_msg}")
            else:
                logger.info("✓ Startup Summary: No warnings or errors")
            
            # Remove the log counter handler after displaying summary
            root_logger.removeHandler(log_counter)
        
        # Register startup hook to ensure DBLogHandler is installed after server.run() calls configure_standard_logging()
        async def ensure_db_log_handler():
            """Ensure DBLogHandler is installed after server configuration."""
            import logging
            from jvagent.logging.handler import DBLogHandler as DBLogHandlerClass
            
            root_logger = logging.getLogger()
            handler_exists = any(
                isinstance(h, DBLogHandlerClass)
                for h in root_logger.handlers
            )
            
            if not handler_exists:
                # Handler was removed, re-install it
                from jvagent.logging.config import get_logging_config, initialize_logging_database
                # Re-initialize to ensure DBLogHandler is installed (database registration is idempotent)
                initialize_logging_database()
        
        server.lifecycle_manager.add_startup_hook(ensure_db_log_handler)
        
        # Register the startup hook using lifecycle manager directly (synchronous call)
        server.lifecycle_manager.add_startup_hook(show_startup_summary)

        # Start the server
        bootstrap_log.complete("Ready")
        server.run()
    except Exception:
        # If server fails to start, display summary and remove handler
        summary = log_counter.get_summary()
        if summary["total"] > 0:
            summary_parts = []
            if summary["critical"] > 0:
                summary_parts.append(f"❌ {summary['critical']} critical")
            if summary["errors"] > 0:
                summary_parts.append(f"❌ {summary['errors']} error{'s' if summary['errors'] != 1 else ''}")
            if summary["warnings"] > 0:
                summary_parts.append(f"⚠️  {summary['warnings']} warning{'s' if summary['warnings'] != 1 else ''}")
            
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
    # Set database path environment variables BEFORE any database initialization
    db_type = os.getenv("JVSPATIAL_DB_TYPE", "json")
    db_path = os.getenv("JVSPATIAL_DB_PATH", "./jvagent_db")
    if not db_path or db_path.strip() == "":
        db_path = "./jvagent_db"
    if db_type == "json":
        os.environ["JVSPATIAL_JSONDB_PATH"] = db_path
    """Show application status.

    Args:
        app_root: Path to the app root directory. If None, uses current working directory.
    """
    from jvagent.core.app_loader import AppLoader

    if app_root is None:
        app_root = os.getcwd()

    # Initialize database context
    db_type = os.getenv("JVSPATIAL_DB_TYPE", "json")
    db_path = os.getenv("JVSPATIAL_DB_PATH", "./jvagent_db")
    # Ensure db_path is never None or empty
    if not db_path or db_path.strip() == "":
        db_path = "./jvagent_db"
    
    # Set JVSPATIAL_JSONDB_PATH unconditionally to ensure DatabaseManager uses the correct path
    # This must be set before any database initialization occurs
    if db_type == "json":
        os.environ["JVSPATIAL_JSONDB_PATH"] = db_path

    # Import and initialize context
    from jvspatial.db import set_current_db_path, set_current_db_type

    set_current_db_type(db_type)
    set_current_db_path(db_path)

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


async def bootstrap_only(update_if_exists: bool = False, app_root: str = None) -> None:
    """Bootstrap the application graph without starting the server.

    Args:
        update_if_exists: If True, update existing agents and actions from YAML files.
                         If False (default), use existing agents/actions without overwriting.
        app_root: Path to the app root directory. If None, uses current working directory.
    """
    if app_root is None:
        app_root = os.getcwd()

    # Load .env file from app root directory
    load_app_env(app_root=app_root)

    # Initialize database context
    db_type = os.getenv("JVSPATIAL_DB_TYPE", "json")
    db_path = os.getenv("JVSPATIAL_DB_PATH", "./jvagent_db")
    # Ensure db_path is never None or empty
    if not db_path or db_path.strip() == "":
        db_path = "./jvagent_db"
    
    # Set JVSPATIAL_JSONDB_PATH unconditionally to ensure DatabaseManager uses the correct path
    # This must be set before any database initialization occurs
    if db_type == "json":
        os.environ["JVSPATIAL_JSONDB_PATH"] = db_path

    # Import and initialize context
    from jvspatial.db import set_current_db_path, set_current_db_type

    set_current_db_type(db_type)
    set_current_db_path(db_path)

    # Install log counter to track warnings and errors during bootstrap
    log_counter = StartupLogCounter()
    root_logger = logging.getLogger()
    root_logger.addHandler(log_counter)

    try:
        await bootstrap_application_graph(update_if_exists=update_if_exists, app_root=app_root)
        await ensure_admin_user()

        # Display bootstrap summary
        summary = log_counter.get_summary()
        if summary["total"] > 0:
            summary_parts = []
            if summary["critical"] > 0:
                summary_parts.append(f"❌ {summary['critical']} critical")
            if summary["errors"] > 0:
                summary_parts.append(f"❌ {summary['errors']} error{'s' if summary['errors'] != 1 else ''}")
            if summary["warnings"] > 0:
                summary_parts.append(f"⚠️  {summary['warnings']} warning{'s' if summary['warnings'] != 1 else ''}")
            
            summary_msg = " | ".join(summary_parts)
            logger.warning(f"⚠️  Bootstrap Summary: {summary_msg}")
        else:
            logger.info("✓ Bootstrap Summary: No warnings or errors")

        if update_if_exists:
            print("Bootstrap complete! (Updated existing agents and actions)")
        else:
            print("Bootstrap complete! (Used existing agents and actions)")
    finally:
        # Remove the log counter handler
        root_logger.removeHandler(log_counter)


def handle_agent_command(args: List[str], app_root: str = None) -> None:
    """Handle agent management commands.

    Note: Agents are installed automatically from app.yaml when running jvagent or bootstrap.
    This command is for listing and uninstalling existing agents only.

    Args:
        args: Command arguments
        app_root: Path to the app root directory. If None, uses current working directory.
    """
    import asyncio

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

    # Initialize database context
    db_type = os.getenv("JVSPATIAL_DB_TYPE", "json")
    db_path = os.getenv("JVSPATIAL_DB_PATH", "./jvagent_db")

    from jvspatial.db import set_current_db_path, set_current_db_type

    set_current_db_type(db_type)
    set_current_db_path(db_path)

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
    import asyncio

    if app_root is None:
        app_root = os.getcwd()

    if not args:
        print("Usage: jvagent action <command>")
        print("Commands: list, enable, disable")
        return

    command = args[0]

    # Initialize database context
    db_type = os.getenv("JVSPATIAL_DB_TYPE", "json")
    db_path = os.getenv("JVSPATIAL_DB_PATH", "./jvagent_db")

    from jvspatial.db import set_current_db_path, set_current_db_type

    set_current_db_type(db_type)
    set_current_db_path(db_path)

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


async def uninstall_agent(namespace: str, agent_name: str, app_root: str = None) -> None:
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
    from jvagent.action.actions import Actions
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
    from jvagent.action.actions import Actions
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
    from jvagent.action.actions import Actions
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


if __name__ == "__main__":
    main()
