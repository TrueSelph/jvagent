"""jvagent CLI Entry Point

Command-line interface for the jvagent application.
"""

import os
import logging
from typing import List
from dotenv import load_dotenv

from jvspatial.api import Server
from jvspatial.core import Root
from jvspatial.api.auth.models import User
from jvspatial.api.auth.service import AuthenticationService

from jvagent.core.app import App
from jvagent.core import Agents
from jvagent.core.app_loader import AppLoader
from jvagent import __version__

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def load_app_env() -> None:
    """Load .env file from the current working directory.
    
    This ensures that when running jvagent from an app directory,
    the .env file in that directory is loaded. The function will:
    1. Try to load .env from the current working directory
    2. Log if a .env file was found and loaded
    """
    cwd = os.getcwd()
    env_path = os.path.join(cwd, ".env")
    
    if os.path.exists(env_path):
        load_dotenv(env_path, override=True)
        logger.info(f"Loaded .env file from: {env_path}")
    else:
        # Still try to load from current directory (dotenv will search automatically)
        # This handles cases where .env might be in a parent directory
        load_dotenv(override=True)
        if os.path.exists(".env"):
            logger.info(f"Loaded .env file from current directory")
        else:
            logger.debug("No .env file found in current directory")


async def bootstrap_application_graph(update_if_exists: bool = False) -> None:
    """Bootstrap the application graph with App and Agents nodes.
    
    If an app.yaml file is found in the current directory, uses AppLoader to
    bootstrap the application declaratively, including:
    - Creating/updating the App node from app.yaml
    - Installing all agents listed in app.yaml
    - Loading and registering all actions for each agent from agent.yaml files
    
    Otherwise, falls back to manual bootstrap with basic configuration.
    
    Args:
        update_if_exists: If True, update existing agents and actions with values from YAML files.
                         If False (default), use existing agents/actions without overwriting their context.
    
    All operations are idempotent - existing nodes and connections are preserved.
    """
    logger.info("Bootstrapping application graph...")
    
    # Check if app.yaml exists in current directory
    app_yaml_path = os.path.join(os.getcwd(), "app.yaml")
    
    if os.path.exists(app_yaml_path):
        logger.info(f"Found app.yaml at {app_yaml_path} - using declarative bootstrap")
        if update_if_exists:
            logger.info("Update mode: Existing agents and actions will be updated from YAML files")
            logger.info("This will:")
            logger.info("  1. Create/update App node from app.yaml")
            logger.info("  2. Install/update agents from agents/ directory as specified in app.yaml")
            logger.info("  3. Load and register/update actions for each agent from agent.yaml files")
        else:
            logger.info("Default mode: Using existing agents and actions (no updates)")
            logger.info("This will:")
            logger.info("  1. Create App node if it doesn't exist")
            logger.info("  2. Install new agents from agents/ directory (skip existing)")
            logger.info("  3. Register new actions (skip existing)")
            logger.info("Use --update flag to update existing agents and actions from YAML files")
        
        # Use AppLoader for declarative bootstrap
        app_loader = AppLoader(os.getcwd())
        app = await app_loader.bootstrap_application(update_if_exists=update_if_exists)
        
        if app:
            logger.info("✓ Declarative bootstrap complete - agents and actions installed")
        else:
            logger.error("✗ Declarative bootstrap failed - falling back to manual bootstrap")
            await _manual_bootstrap()
    else:
        logger.info("No app.yaml found - using manual bootstrap (no agents will be installed)")
        logger.info("To install agents, create an app.yaml file in the current directory")
        await _manual_bootstrap()


async def _manual_bootstrap() -> None:
    """Manual bootstrap when no app.yaml is available.
    
    Creates basic App and Agents nodes with default configuration.
    """
    # Step 1: Ensure Root node exists
    root = await Root.get()
    logger.info(f"Root node ready: {root.id}")
    
    # Step 2: Create App node if it doesn't exist
    existing_apps = await App.find({"context.name": "jvAgent"})
    
    if existing_apps:
        app = existing_apps[0]
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
            file_storage_enabled=True
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
        agents = await Agents.create(
            total_agents=0,
            active_agents=0
        )
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
    logger.info("Checking for admin user...")
    
    # Get admin credentials from environment
    admin_username = os.getenv("JVAGENT_ADMIN_USERNAME", "admin")
    admin_password = os.getenv("JVAGENT_ADMIN_PASSWORD")
    admin_email = os.getenv("JVAGENT_ADMIN_EMAIL", f"{admin_username}@jvagent.example")
    
    if not admin_password:
        logger.warning(
            "JVAGENT_ADMIN_PASSWORD not set in .env. "
            "Admin user will not be created."
        )
        return False
    
    # Check if admin user already exists by email
    existing_users = await User.find({"context.email": admin_email})
    
    if existing_users:
        logger.info(f"Admin user already exists: {admin_email}")
        return True
    
    # Create admin user
    # Use AuthenticationService to hash password properly
    auth_service = AuthenticationService()
    
    # Hash the password
    password_hash = auth_service._hash_password(admin_password)
    
    # Create user
    admin_user = await User.create(
        email=admin_email,
        password_hash=password_hash,
        name=admin_username,
        is_active=True
    )
    
    logger.info(f"Created admin user: {admin_email} (ID: {admin_user.id})")
    return True


def create_server_from_config() -> Server:
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
    db_path = os.getenv("JVSPATIAL_DB_PATH", "./jvdb")
    
    # Authentication configuration (enabled by default for jvagent)
    auth_enabled = os.getenv("JVAGENT_AUTH_ENABLED", "true").lower() == "true"
    jwt_auth_enabled = os.getenv("JVSPATIAL_JWT_AUTH_ENABLED", "true").lower() == "true"
    jwt_secret = os.getenv("JVSPATIAL_JWT_SECRET", "jvagent-secret-key-change-in-production")
    jwt_expire_minutes = int(os.getenv("JVSPATIAL_JWT_EXPIRE_MINUTES", "60"))
    
    logger.info(f"Creating server: {title} v{version}")
    logger.info(f"Database: {db_type} at {db_path}")
    logger.info(f"Authentication: {'enabled' if auth_enabled else 'disabled'}")
    
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
    )
    
    return server


async def pre_startup_bootstrap(server: Server, update_if_exists: bool = False) -> bool:
    """Perform bootstrap tasks before server starts.
    
    This runs after the server is created (so context is initialized)
    but before the server starts running.
    
    Args:
        server: Server instance with initialized context
        update_if_exists: If True, update existing agents and actions from YAML files.
                         If False (default), use existing agents/actions without overwriting.
        
    Returns:
        True if admin user exists, False otherwise
    """
    try:
        # Bootstrap application graph
        await bootstrap_application_graph(update_if_exists=update_if_exists)
        
        # Ensure admin user exists
        admin_exists = await ensure_admin_user()
        
        logger.info("Pre-startup bootstrap completed successfully")
        return admin_exists
    except Exception as e:
        logger.error(f"Error during pre-startup bootstrap: {e}", exc_info=True)
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
        if success:
            logger.info("Disabled /auth/register endpoint (admin user exists)")
        else:
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
    
    # Load .env file from current working directory first
    load_app_env()
    
    # Parse command-line arguments
    args = sys.argv[1:]
    
    # Check for --update flag
    update_flag = "--update" in args or "--migrate" in args
    if update_flag:
        args = [arg for arg in args if arg not in ["--update", "--migrate"]]
    
    # If no arguments or "run" command, start the server
    if not args or args[0] == "run":
        run_server(update_if_exists=update_flag)
    elif args[0] == "status":
        # Show application status
        asyncio.run(show_status())
    elif args[0] == "agent":
        # Agent management commands
        handle_agent_command(args[1:])
    elif args[0] == "action":
        # Action management commands
        handle_action_command(args[1:])
    elif args[0] == "bootstrap":
        # Bootstrap application graph
        asyncio.run(bootstrap_only(update_if_exists=update_flag))
    else:
        print_usage()


def print_usage() -> None:
    """Print CLI usage information."""
    print("""
jvagent - Agentive Platform

Usage:
    jvagent [run] [--update]   Start the jvagent server (default)
                                --update: Update existing agents/actions from YAML files
    jvagent status             Show application status
    jvagent bootstrap [--update]  Bootstrap application graph
                                  --update: Update existing agents/actions from YAML files
    jvagent agent list         List all agents
    jvagent agent install      Install agents from app.yaml
    jvagent agent uninstall <name>    Uninstall an agent
    jvagent action list <agent_name>  List actions for an agent
    jvagent action enable <agent_name> <action_id>   Enable an action
    jvagent action disable <agent_name> <action_id>  Disable an action
    
Flags:
    --update, --migrate        Force update of existing agents and actions from YAML files
                                By default, existing agents/actions are used as-is
    
Environment Variables:
    JVAGENT_ADMIN_PASSWORD     Admin user password (required)
    JVAGENT_HOST              Server host (default: 127.0.0.1)
    JVAGENT_PORT              Server port (default: 8000)
    JVSPATIAL_DB_PATH         Database path (default: ./jvdb)
    JVSPATIAL_FILES_ROOT_PATH File storage path (default: .files)
    """)


def run_server(update_if_exists: bool = False) -> None:
    """Start the jvagent server.
    
    Args:
        update_if_exists: If True, update existing agents and actions from YAML files.
                         If False (default), use existing agents/actions without overwriting.
    """
    import asyncio
    
    logger.info("Starting jvagent application...")
    
    # Create server from configuration
    server = create_server_from_config()
    
    # Perform bootstrap tasks before server starts
    logger.info("Performing pre-startup bootstrap...")
    admin_exists = asyncio.run(pre_startup_bootstrap(server, update_if_exists=update_if_exists))
    
    # If admin user exists, disable the register endpoint
    if admin_exists:
        logger.info("Admin user exists - disabling registration endpoint")
        disable_register_endpoint(server)
    else:
        logger.warning(
            "Admin user not found - registration endpoint will remain enabled. "
            "Set JVAGENT_ADMIN_PASSWORD in .env to create admin user."
        )
    
    # Start the server
    logger.info("Starting server...")
    server.run()


async def show_status() -> None:
    """Show application status."""
    from jvagent.core.app_loader import AppLoader
    
    # Initialize database context
    db_type = os.getenv("JVSPATIAL_DB_TYPE", "json")
    db_path = os.getenv("JVSPATIAL_DB_PATH", "./jvdb")
    
    # Import and initialize context
    from jvspatial.db import set_current_db_type, set_current_db_path
    set_current_db_type(db_type)
    set_current_db_path(db_path)
    
    app_loader = AppLoader(os.getcwd())
    status = await app_loader.get_app_status()
    
    print("\n=== jvagent Application Status ===\n")
    print(f"Status: {status.get('status', 'unknown')}")
    
    if "message" in status:
        print(f"Message: {status['message']}")
    
    if "app" in status:
        app_info = status["app"]
        print(f"\nApplication:")
        print(f"  ID: {app_info.get('id', 'N/A')}")
        print(f"  Name: {app_info.get('name', 'N/A')}")
        print(f"  Version: {app_info.get('version', 'N/A')}")
        print(f"  Description: {app_info.get('description', 'N/A')}")
        print(f"  File Storage: {'enabled' if app_info.get('file_storage_enabled') else 'disabled'}")
    
    if "agents" in status:
        agents_info = status["agents"]
        print(f"\nAgents:")
        print(f"  Total: {agents_info.get('total', 0)}")
        print(f"  Active: {agents_info.get('active', 0)}")
        
        agents_list = agents_info.get('list', [])
        if agents_list:
            print(f"\n  Installed Agents:")
            for agent in agents_list:
                print(f"    - {agent.get('name')} (ID: {agent.get('id')}, Enabled: {agent.get('enabled')})")
    
    print()


async def bootstrap_only(update_if_exists: bool = False) -> None:
    """Bootstrap the application graph without starting the server.
    
    Args:
        update_if_exists: If True, update existing agents and actions from YAML files.
                         If False (default), use existing agents/actions without overwriting.
    """
    # Load .env file from current working directory
    load_app_env()
    
    # Initialize database context
    db_type = os.getenv("JVSPATIAL_DB_TYPE", "json")
    db_path = os.getenv("JVSPATIAL_DB_PATH", "./jvdb")
    
    # Import and initialize context
    from jvspatial.db import set_current_db_type, set_current_db_path
    set_current_db_type(db_type)
    set_current_db_path(db_path)
    
    await bootstrap_application_graph(update_if_exists=update_if_exists)
    await ensure_admin_user()
    
    if update_if_exists:
        print("Bootstrap complete! (Updated existing agents and actions)")
    else:
        print("Bootstrap complete! (Used existing agents and actions)")


def handle_agent_command(args: List[str]) -> None:
    """Handle agent management commands."""
    import asyncio
    
    if not args:
        print("Usage: jvagent agent <command>")
        print("Commands: list, install, uninstall")
        return
    
    command = args[0]
    
    # Initialize database context
    db_type = os.getenv("JVSPATIAL_DB_TYPE", "json")
    db_path = os.getenv("JVSPATIAL_DB_PATH", "./jvdb")
    
    from jvspatial.db import set_current_db_type, set_current_db_path
    set_current_db_type(db_type)
    set_current_db_path(db_path)
    
    if command == "list":
        asyncio.run(list_agents())
    elif command == "install":
        asyncio.run(install_agents())
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
        asyncio.run(uninstall_agent(namespace, agent_name))
    else:
        print(f"Unknown agent command: {command}")


def handle_action_command(args: List[str]) -> None:
    """Handle action management commands."""
    import asyncio
    
    if not args:
        print("Usage: jvagent action <command>")
        print("Commands: list, enable, disable")
        return
    
    command = args[0]
    
    # Initialize database context
    db_type = os.getenv("JVSPATIAL_DB_TYPE", "json")
    db_path = os.getenv("JVSPATIAL_DB_PATH", "./jvdb")
    
    from jvspatial.db import set_current_db_type, set_current_db_path
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


async def install_agents() -> None:
    """Install agents from app.yaml."""
    from jvagent.core.agent_loader import AgentLoader
    
    loader = AgentLoader(os.getcwd())
    agents = await loader.install_all_agents(update_if_exists=True)
    
    print(f"\nInstalled {len(agents)} agent(s)")


async def uninstall_agent(namespace: str, agent_name: str) -> None:
    """Uninstall an agent."""
    from jvagent.core.agent_loader import AgentLoader
    
    loader = AgentLoader(os.getcwd())
    success = await loader.uninstall_agent(namespace, agent_name)
    
    if success:
        print(f"Uninstalled agent: {namespace}/{agent_name}")
    else:
        print(f"Failed to uninstall agent: {namespace}/{agent_name}")


async def list_actions(agent_name: str) -> None:
    """List actions for an agent."""
    from jvagent.core.agent import Agent
    from jvagent.action.actions import Actions
    
    # Find the agent
    agents = await Agent.find({"context.name": agent_name})
    if not agents:
        print(f"Agent not found: {agent_name}")
        return
    
    agent = agents[0]
    
    # Get Actions manager
    connected_nodes = await agent.nodes()
    actions_manager = None
    for node in connected_nodes:
        if isinstance(node, Actions):
            actions_manager = node
            break
    
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
    from jvagent.action.actions import Actions
    
    # Find the agent
    agents = await Agent.find({"context.name": agent_name})
    if not agents:
        print(f"Agent not found: {agent_name}")
        return
    
    agent = agents[0]
    
    # Get Actions manager
    connected_nodes = await agent.nodes()
    actions_manager = None
    for node in connected_nodes:
        if isinstance(node, Actions):
            actions_manager = node
            break
    
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
    from jvagent.action.actions import Actions
    
    # Find the agent
    agents = await Agent.find({"context.name": agent_name})
    if not agents:
        print(f"Agent not found: {agent_name}")
        return
    
    agent = agents[0]
    
    # Get Actions manager
    connected_nodes = await agent.nodes()
    actions_manager = None
    for node in connected_nodes:
        if isinstance(node, Actions):
            actions_manager = node
            break
    
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

