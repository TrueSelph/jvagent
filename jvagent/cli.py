"""jvagent CLI Entry Point

Command-line interface for the jvagent application.
"""

import os
import logging
from dotenv import load_dotenv

from jvspatial.api import Server
from jvspatial.core import Root
from jvspatial.api.auth.models import User
from jvspatial.api.auth.service import AuthenticationService

from jvagent.core.app import App
from jvagent.core.agents import Agents
from jvagent import __version__

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def bootstrap_application_graph() -> None:
    """Bootstrap the application graph with App and Agents nodes.
    
    Ensures:
    1. Root node exists (created automatically by jvspatial if needed)
    2. App node is created and connected to Root node
    3. Agents node is created and connected to App node
    
    All operations are idempotent - existing nodes and connections are preserved.
    """
    logger.info("Bootstrapping application graph...")
    
    # Step 1: Ensure Root node exists
    # Root.get() automatically creates the Root node if it doesn't exist
    # This is handled by jvspatial's default behavior
    root = await Root.get()
    logger.info(f"Root node ready: {root.id}")
    
    # Step 2: Create App node if it doesn't exist
    # Search for App nodes by checking if any node with name="jvAgent" exists
    existing_apps = await App.find({"context.name": "jvAgent"})
    
    if existing_apps:
        app = existing_apps[0]
        logger.info(f"App node already exists: {app.id}")
    else:
        # Create App node
        app = await App.create(
            name="jvAgent",
            version=__version__,
            description="jvagent Application"
        )
        logger.info(f"Created App node: {app.id}")
    
    # Step 3: Ensure App node is connected to Root node
    # Use is_connected_to() for reliable bidirectional edge checking
    if not await root.is_connected_to(app):
        # Connect App to Root (Root -> App)
        await root.connect(app)
        logger.info("Connected App node to Root node")
    else:
        logger.info("App node already connected to Root node")
    
    # Step 4: Create Agents node if it doesn't exist
    # Check if any Agents node is already connected to App
    app_connected_nodes = await app.nodes()
    agents = None
    
    # Look for existing Agents node connected to App
    for node in app_connected_nodes:
        if isinstance(node, Agents):
            agents = node
            break
    
    if agents:
        logger.info(f"Agents node already exists: {agents.id}")
    else:
        # Create Agents node
        agents = await Agents.create(
            total_agents=0,
            active_agents=0
        )
        logger.info(f"Created Agents node: {agents.id}")
    
    # Step 5: Ensure Agents node is connected to App node
    # Use is_connected_to() for reliable bidirectional edge checking
    if not await app.is_connected_to(agents):
        # Connect Agents to App (App -> Agents)
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


async def pre_startup_bootstrap(server: Server) -> bool:
    """Perform bootstrap tasks before server starts.
    
    This runs after the server is created (so context is initialized)
    but before the server starts running.
    
    Args:
        server: Server instance with initialized context
        
    Returns:
        True if admin user exists, False otherwise
    """
    try:
        # Bootstrap application graph
        await bootstrap_application_graph()
        
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
    
    logger.info("Starting jvagent application...")
    
    # Create server from configuration
    # This initializes the database context
    server = create_server_from_config()
    
    # Import agent module to register endpoints
    # This must be done after server creation so the server is available as current server
    from jvagent.core import agent  # noqa: F401
    
    # Perform bootstrap tasks before server starts
    # This ensures App, Agents, and admin user are ready
    logger.info("Performing pre-startup bootstrap...")
    admin_exists = asyncio.run(pre_startup_bootstrap(server))
    
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


if __name__ == "__main__":
    main()

