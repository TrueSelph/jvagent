"""jvagent CLI Entry Point

Command-line interface for the jvagent application.
"""

import logging
import os
import shutil
import sys
from typing import Any, List, Optional

from dotenv import load_dotenv
from jvspatial.api import Server, get_auth_service
from jvspatial.api.auth.models import UserCreateAdmin
from jvspatial.api.config_groups import (
    AuthConfig,
    CORSConfig,
    DatabaseConfig,
    FileStorageConfig,
)
from jvspatial.core import Root

# Configure logging (will be updated based on --debug flag)
from jvspatial.logging import configure_standard_logging

from jvagent import __version__
from jvagent.core import Agents
from jvagent.core.app import App
from jvagent.core.app_loader import AppLoader
from jvagent.core.bootstrap_logger import BootstrapLogger
from jvagent.utils.env import is_development_mode

configure_standard_logging(
    level=os.getenv("JVAGENT_LOG_LEVEL", "INFO"),
    enable_colors=True,
    preserve_handler_class_names=["DBLogHandler", "StartupLogCounter"],
)
logger = logging.getLogger(__name__)

# Suppress noisy asyncio selector logs
logging.getLogger("asyncio").setLevel(logging.WARNING)


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


async def bootstrap_application_graph(
    update_mode: Optional[str] = None, app_root: str = None
) -> None:
    """Bootstrap the application graph with App and Agents nodes.

    If an app.yaml file is found in the app root directory, uses AppLoader to
    bootstrap the application declaratively, including:
    - Creating/updating the App node from app.yaml
    - Installing all agents listed in app.yaml
    - Loading and registering all actions for each agent from agent.yaml files

    Otherwise, falls back to manual bootstrap with basic configuration.

    Args:
        update_mode: Update strategy - "merge" for non-destructive merge, "source" for
                     destructive overwrite from YAML, or None to skip existing.
        app_root: Path to the app root directory. If None, uses current working directory.

    All operations are idempotent - existing nodes and connections are preserved.
    """
    if app_root is None:
        app_root = os.getcwd()

    bootstrap_log = BootstrapLogger("Bootstrap")

    # Check if app.yaml exists in app root directory
    app_yaml_path = os.path.join(app_root, "app.yaml")

    if os.path.exists(app_yaml_path):
        mode = update_mode if update_mode else "sync"
        bootstrap_log.start(f"Application graph ({mode} mode)")

        # Use AppLoader for declarative bootstrap
        app_loader = AppLoader(app_root)
        app = await app_loader.bootstrap_application(update_mode=update_mode)

        if app:
            bootstrap_log.complete("Application graph ready")
        else:
            bootstrap_log.error(
                "Declarative bootstrap failed - falling back to manual bootstrap"
            )
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

    # Step 2: Create App node if it doesn't exist (graph-based lookup)
    app_nodes = [n for n in await root.nodes(direction="out") if isinstance(n, App)]
    app = app_nodes[0] if app_nodes else None

    if app:
        logger.info(f"App node already exists: {app.id}")
        App._cached_app = app
    else:
        # Create App node with file storage configuration
        app = await App.create(
            app_id="jvagent_app",
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

    First-user bootstrap is delegated to jvspatial via AuthConfig.bootstrap_admin_*;
    the server runs _bootstrap_admin_startup on start. This function handles only the
    recovery path: when users exist but the configured admin email is not found.

    Returns:
        True if admin user exists (or will be created by jvspatial on server start),
        False if admin user could not be created (missing password or no admin).
    """
    logger.debug("Checking for admin user...")

    admin_username = os.getenv("JVAGENT_ADMIN_USERNAME", "admin")
    admin_password = os.getenv("JVAGENT_ADMIN_PASSWORD")
    admin_email = os.getenv("JVAGENT_ADMIN_EMAIL", f"{admin_username}@jvagent.example")

    if not admin_password:
        logger.warning(
            "JVAGENT_ADMIN_PASSWORD not set in .env. " "Admin user will not be created."
        )
        return False

    auth_service = get_auth_service()
    user_count = await auth_service._user_count()

    if user_count == 0:
        return True

    existing_user = await auth_service._find_user_by_email(admin_email)
    if existing_user:
        roles = getattr(existing_user, "roles", None) or []
        if "admin" in roles:
            logger.debug(f"Admin user already exists: {admin_email}")
            return True
        logger.warning(
            f"User {admin_email} exists but lacks admin role. "
            "Use admin endpoint to assign admin role."
        )
        return False

    # Recovery: users exist but configured admin email not found - create admin via
    # create_user_with_roles (trusted bootstrap context; no HTTP caller check)
    try:
        user_response = await auth_service.create_user_with_roles(
            UserCreateAdmin(
                email=admin_email,
                password=admin_password,
                name=admin_username,
                roles=["admin"],
                permissions=[],
            )
        )
        logger.info(
            f"Created admin user (recovery): {admin_email} (ID: {user_response.id})"
        )
        return True
    except ValueError as e:
        if "already exists" in str(e).lower():
            existing_user = await auth_service._find_user_by_email(admin_email)
            if existing_user:
                roles = getattr(existing_user, "roles", None) or []
                if "admin" in roles:
                    return True
        logger.error(f"Failed to create admin user: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to create admin user: {e}", exc_info=True)
        return False


def _get_config_value(
    config: dict, path: str, env_var: str = None, default: Any = None
) -> Any:
    """Get configuration value from nested dict path with environment variable fallback.

    Args:
        config: Configuration dictionary (from app.yaml)
        path: Dot-separated path to config value (e.g., "server.host")
        env_var: Environment variable name to check (takes precedence)
        default: Default value if not found

    Returns:
        Configuration value (env var > config > default)
    """
    # Environment variables take highest priority
    if env_var and os.getenv(env_var) is not None:
        value = os.getenv(env_var)
        # Convert string "true"/"false" to boolean
        if value.lower() in ("true", "false"):
            return value.lower() == "true"
        # Try to convert to int if default is int
        if isinstance(default, int) and value.isdigit():
            return int(value)
        return value

    # Try to get from config dict using dot notation
    if config:
        keys = path.split(".")
        current = config
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                current = None
                break
        if current is not None:
            return current

    # Return default
    return default


def _import_core_endpoint_modules() -> None:
    """Import core endpoint modules so @endpoint decorators register with the server."""
    from jvagent.action import endpoints as _  # noqa: F401
    from jvagent.core import endpoints as _  # noqa: F401
    from jvagent.logging import endpoints as _  # noqa: F401


def create_server_from_config(debug: bool = False, app_root: str = None) -> Server:
    """Create and configure Server instance from app.yaml and environment variables.

    Configuration priority (highest to lowest):
    1. Environment variables
    2. app.yaml config section
    3. Hardcoded defaults

    Args:
        debug: Enable debug mode
        app_root: Path to app root directory (for loading app.yaml)

    Returns:
        Configured Server instance with authentication enabled by default.
    """
    # Try to load app.yaml config
    app_config = {}
    if app_root is None:
        app_root = os.getcwd()

    try:
        from pathlib import Path

        app_yaml_path = Path(app_root) / "app.yaml"
        if app_yaml_path.exists():
            import yaml

            from jvagent.core.env_resolver import resolve_env_placeholders

            with open(app_yaml_path, "r", encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f)
                if yaml_data and "config" in yaml_data:
                    # Resolve environment variable placeholders
                    app_config = resolve_env_placeholders(yaml_data.get("config", {}))
    except Exception as e:
        logger.debug(f"Could not load app.yaml config: {e}")
        app_config = {}

    # Get configuration with priority: env var > app.yaml > default
    # Server configuration
    title = _get_config_value(
        app_config, "server.title", "JVAGENT_TITLE", "jvagent API"
    )
    description = _get_config_value(
        app_config,
        "server.description",
        "JVAGENT_DESCRIPTION",
        "jvagent Agentive Platform API",
    )
    version = _get_config_value(
        app_config, "server.version", "JVAGENT_VERSION", __version__
    )
    host = _get_config_value(app_config, "server.host", "JVAGENT_HOST", "127.0.0.1")
    port = int(_get_config_value(app_config, "server.port", "JVAGENT_PORT", 8000))

    # Database configuration
    db_type = _get_config_value(
        app_config, "database.type", "JVSPATIAL_DB_TYPE", "json"
    )
    db_path = _get_config_value(
        app_config, "database.path", "JVSPATIAL_DB_PATH", "./jvagent_db"
    )
    # Ensure db_path is never None or empty (jvspatial falls back to "./jvdb" if None)
    if not db_path or db_path.strip() == "":
        db_path = "./jvagent_db"

    # Resolve relative database path against app_root
    from pathlib import Path

    app_root_path = Path(app_root).resolve()
    db_path_obj = Path(db_path)
    if not db_path_obj.is_absolute():
        db_path = str(app_root_path / db_path)
        logger.debug(f"Resolved database path to: {db_path}")

    # MongoDB configuration
    mongodb_uri = _get_config_value(
        app_config, "database.uri", "JVSPATIAL_MONGODB_URI", "mongodb://localhost:27017"
    )
    mongodb_db_name = _get_config_value(
        app_config, "database.name", "JVSPATIAL_MONGODB_DB_NAME", None
    )

    # Handle empty string from unresolved placeholder (resolve_env_placeholders returns "" if env var not found)
    if not mongodb_uri or mongodb_uri.strip() == "":
        mongodb_uri = os.getenv("JVSPATIAL_MONGODB_URI", "mongodb://localhost:27017")

    # Set MongoDB environment variables if using MongoDB
    if db_type == "mongodb":
        os.environ["JVSPATIAL_MONGODB_URI"] = mongodb_uri
        if mongodb_db_name:
            os.environ["JVSPATIAL_MONGODB_DB_NAME"] = mongodb_db_name

    # DynamoDB configuration
    dynamodb_table_name = _get_config_value(
        app_config, "database.table_name", "JVSPATIAL_DYNAMODB_TABLE_NAME", None
    )
    dynamodb_region = _get_config_value(
        app_config, "database.region", "JVSPATIAL_DYNAMODB_REGION", None
    )
    dynamodb_endpoint_url = _get_config_value(
        app_config, "database.endpoint_url", "JVSPATIAL_DYNAMODB_ENDPOINT_URL", None
    )
    dynamodb_access_key_id = _get_config_value(
        app_config, "database.access_key_id", "AWS_ACCESS_KEY_ID", None
    )
    dynamodb_secret_access_key = _get_config_value(
        app_config, "database.secret_access_key", "AWS_SECRET_ACCESS_KEY", None
    )

    # Handle empty strings from unresolved placeholders
    if dynamodb_table_name and dynamodb_table_name.strip() == "":
        dynamodb_table_name = None
    if dynamodb_region and dynamodb_region.strip() == "":
        dynamodb_region = None
    if dynamodb_endpoint_url and dynamodb_endpoint_url.strip() == "":
        dynamodb_endpoint_url = None
    if dynamodb_access_key_id and dynamodb_access_key_id.strip() == "":
        dynamodb_access_key_id = None
    if dynamodb_secret_access_key and dynamodb_secret_access_key.strip() == "":
        dynamodb_secret_access_key = None

    # Set DynamoDB environment variables if using DynamoDB (for backward compatibility)
    if db_type == "dynamodb":
        if dynamodb_table_name:
            os.environ["JVSPATIAL_DYNAMODB_TABLE_NAME"] = dynamodb_table_name
        if dynamodb_region:
            os.environ["JVSPATIAL_DYNAMODB_REGION"] = dynamodb_region
        if dynamodb_endpoint_url:
            os.environ["JVSPATIAL_DYNAMODB_ENDPOINT_URL"] = dynamodb_endpoint_url
        if dynamodb_access_key_id:
            os.environ["AWS_ACCESS_KEY_ID"] = dynamodb_access_key_id
        if dynamodb_secret_access_key:
            os.environ["AWS_SECRET_ACCESS_KEY"] = dynamodb_secret_access_key

    # Set JVSPATIAL_JSONDB_PATH unconditionally to ensure DatabaseManager uses the correct path
    # (DatabaseManager uses JVSPATIAL_JSONDB_PATH, not JVSPATIAL_DB_PATH)
    # This must be set before any database initialization occurs
    if db_type == "json":
        os.environ["JVSPATIAL_JSONDB_PATH"] = db_path

    # Graph endpoint configuration
    graph_endpoint_enabled = _get_config_value(
        app_config,
        "api.graph_endpoint_enabled",
        "JVSPATIAL_GRAPH_ENDPOINT_ENABLED",
        False,
    )

    # Authentication configuration (enabled by default for jvagent)
    auth_enabled = _get_config_value(
        app_config, "auth.enabled", "JVAGENT_AUTH_ENABLED", True
    )
    jwt_secret = os.getenv(
        "JVSPATIAL_JWT_SECRET", "jvagent-secret-key-change-in-production"
    )
    jwt_expire_minutes = int(
        _get_config_value(
            app_config, "auth.jwt_expire_minutes", "JVSPATIAL_JWT_EXPIRE_MINUTES", 60
        )
    )

    # API key management endpoints (/auth/api-keys) - enabled by default when auth is enabled
    # Supports auth.api_key_management_enabled and legacy auth.api_key_enabled
    api_key_management_enabled = _get_config_value(
        app_config,
        "auth.api_key_management_enabled",
        "JVAGENT_API_KEY_MANAGEMENT_ENABLED",
        None,
    )
    if api_key_management_enabled is None:
        api_key_management_enabled = _get_config_value(
            app_config,
            "auth.api_key_enabled",
            "JVAGENT_API_KEY_AUTH_ENABLED",
            auth_enabled,
        )
    api_key_prefix = _get_config_value(
        app_config, "auth.api_key_prefix", "JVAGENT_API_KEY_PREFIX", "jv_"
    )
    api_key_header = _get_config_value(
        app_config, "auth.api_key_header", "JVAGENT_API_KEY_HEADER", "x-api-key"
    )

    # Bootstrap admin (passed to AuthConfig; jvspatial runs _bootstrap_admin_startup on server start)
    admin_username = os.getenv("JVAGENT_ADMIN_USERNAME", "admin")
    admin_password = os.getenv("JVAGENT_ADMIN_PASSWORD")
    admin_email = os.getenv("JVAGENT_ADMIN_EMAIL", f"{admin_username}@jvagent.example")

    # Log server creation details only in debug mode
    if debug:
        logger.debug(f"Creating server: {title} v{version}")
        if db_type == "dynamodb":
            db_info = f"Database: {db_type}"
            if dynamodb_table_name:
                db_info += f" (table: {dynamodb_table_name})"
            if dynamodb_region:
                db_info += f" (region: {dynamodb_region})"
            logger.debug(db_info)
        else:
            logger.debug(f"Database: {db_type} at {db_path}")
        logger.debug(f"Authentication: {'enabled' if auth_enabled else 'disabled'}")
        if auth_enabled:
            logger.debug(
                f"  API Key Management: {'enabled' if api_key_management_enabled else 'disabled'}"
            )
            if api_key_management_enabled:
                logger.debug(f"    API Key Prefix: {api_key_prefix}")
                logger.debug(f"    API Key Header: {api_key_header}")

    # Determine log level based on debug flag or environment variable
    log_level = os.getenv("JVAGENT_LOG_LEVEL", "debug" if debug else "info")

    # Override with app.yaml development.debug if available
    debug_mode = _get_config_value(
        app_config, "development.debug", "JVSPATIAL_DEBUG", False
    )
    if debug_mode:
        log_level = "debug"

    # CORS configuration
    cors_enabled = _get_config_value(
        app_config, "cors.enabled", "JVSPATIAL_CORS_ENABLED", True
    )
    cors_origins_str = _get_config_value(
        app_config, "cors.origins", "JVSPATIAL_CORS_ORIGINS", None
    )
    if cors_origins_str and isinstance(cors_origins_str, str):
        cors_origins = [
            origin.strip() for origin in cors_origins_str.split(",") if origin.strip()
        ]
    else:
        # Default CORS origins
        cors_origins = [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]

    # Build grouped configuration objects
    # Database configuration
    database_config = DatabaseConfig(
        db_type=db_type,
        db_path=db_path if db_type == "json" else None,
        db_connection_string=mongodb_uri if db_type == "mongodb" else None,
        db_database_name=mongodb_db_name if db_type == "mongodb" else None,
        dynamodb_table_name=dynamodb_table_name if db_type == "dynamodb" else None,
        dynamodb_region=dynamodb_region if db_type == "dynamodb" else None,
        dynamodb_endpoint_url=dynamodb_endpoint_url if db_type == "dynamodb" else None,
        dynamodb_access_key_id=(
            dynamodb_access_key_id if db_type == "dynamodb" else None
        ),
        dynamodb_secret_access_key=(
            dynamodb_secret_access_key if db_type == "dynamodb" else None
        ),
    )

    # Auth configuration - merge default exempt paths with app-specific (auth.exempt_paths)
    default_exempt_paths = [
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/favicon.ico",
        "/api/auth/register",
        "/api/auth/login",
        "/api/auth/refresh",
        "/api/auth/logout",
        "/api/storage/*",  # Public access for images/media
        "/api/agents/*/interact",  # Anonymous interact endpoint
    ]
    app_exempt_paths = _get_config_value(app_config, "auth.exempt_paths", None, None)
    if isinstance(app_exempt_paths, list):
        auth_exempt_paths = list(dict.fromkeys(default_exempt_paths + app_exempt_paths))
    else:
        auth_exempt_paths = default_exempt_paths

    auth_config = AuthConfig(
        auth_enabled=auth_enabled,
        jwt_secret=jwt_secret,
        jwt_expire_minutes=jwt_expire_minutes,
        api_key_management_enabled=api_key_management_enabled,
        api_key_prefix=api_key_prefix,
        api_key_header=api_key_header,
        bootstrap_admin_email=admin_email if admin_password else None,
        bootstrap_admin_password=admin_password or None,
        bootstrap_admin_name=admin_username if admin_password else None,
        auth_exempt_paths=auth_exempt_paths,
        role_permission_mapping={
            "admin": ["*"],
            "user": [],
            "system": [],
        },
    )

    # CORS configuration
    cors_config = CORSConfig(
        cors_enabled=cors_enabled,
        cors_origins=cors_origins,
    )

    # File storage configuration (env > app.yaml > default)
    file_storage_enabled = _get_config_value(
        app_config, "file_storage.enabled", "JVSPATIAL_FILE_STORAGE_ENABLED", False
    )
    file_storage_provider = (
        _get_config_value(
            app_config, "file_storage.provider", "JVSPATIAL_FILE_INTERFACE", None
        )
        or _get_config_value(
            app_config,
            "file_storage.provider",
            "JVSPATIAL_FILE_STORAGE_PROVIDER",
            "local",
        )
        or "local"
    )
    file_storage_root = (
        _get_config_value(
            app_config, "file_storage.root_dir", "JVSPATIAL_FILES_ROOT_PATH", None
        )
        or _get_config_value(
            app_config,
            "file_storage.root_dir",
            "JVSPATIAL_FILE_STORAGE_ROOT",
            ".files",
        )
        or ".files"
    )
    file_storage_base_url = _get_config_value(
        app_config,
        "file_storage.base_url",
        "JVSPATIAL_FILE_STORAGE_BASE_URL",
        "http://localhost:8000",
    )
    file_storage_max_size = _get_config_value(
        app_config,
        "file_storage.max_size",
        "JVSPATIAL_FILE_STORAGE_MAX_SIZE",
        100 * 1024 * 1024,
    )
    file_storage_config = FileStorageConfig(
        file_storage_enabled=file_storage_enabled,
        file_storage_provider=file_storage_provider,
        file_storage_root=file_storage_root,
        file_storage_base_url=file_storage_base_url,
        file_storage_max_size=file_storage_max_size,
    )

    # Create server with grouped configuration
    server_kwargs = {
        "title": title,
        "description": description,
        "version": version,
        "host": host,
        "port": port,
        "database": database_config,
        "auth": auth_config,
        "cors": cors_config,
        "file_storage": file_storage_config,
        "graph_endpoint_enabled": graph_endpoint_enabled,
        "log_level": log_level,
        "debug": debug_mode,
    }

    server = Server(**server_kwargs)

    # Initialize logging database (automatically installs DBLogHandler)
    # Import INTERACTION level to ensure it's registered before initialization
    import logging

    from jvspatial.logging.config import initialize_logging_database

    from jvagent.logging.service import INTERACTION_LEVEL_NUMBER

    # Get logging configuration from app.yaml if available
    logging_enabled = _get_config_value(
        app_config, "logging.enabled", "JVAGENT_LOGGING_ENABLED", True
    )
    if logging_enabled:
        # Get log levels from app.yaml or environment
        log_levels_str = _get_config_value(
            app_config, "logging.levels", "JVAGENT_DB_LOGGING_LEVELS", "ERROR,CRITICAL"
        )
        if isinstance(log_levels_str, str):
            log_level_names = [
                level.strip().upper() for level in log_levels_str.split(",")
            ]
        else:
            log_level_names = ["ERROR", "CRITICAL"]

        # Convert level names to logging constants
        log_levels = set()
        for level_name in log_level_names:
            try:
                level = getattr(logging, level_name)
                log_levels.add(level)
            except AttributeError:
                logger.warning(f"Invalid log level: {level_name}, skipping")

        # Default to ERROR and CRITICAL if no valid levels
        if not log_levels:
            log_levels = {logging.ERROR, logging.CRITICAL}

        # Add INTERACTION level to capture interaction logs
        log_levels.add(INTERACTION_LEVEL_NUMBER)

        # Get logging database config from app.yaml
        log_db_type = _get_config_value(
            app_config, "logging.database.type", "JVAGENT_LOG_DB_TYPE", None
        )
        log_db_uri = _get_config_value(
            app_config, "logging.database.uri", "JVAGENT_LOG_DB_URI", None
        )
        log_db_name = _get_config_value(
            app_config, "logging.database.name", "JVAGENT_LOG_DB_NAME", "jvagent_logs"
        )
        log_db_path = _get_config_value(
            app_config, "logging.database.path", "JVAGENT_LOG_DB_PATH", None
        )

        # DynamoDB logging database configuration
        log_dynamodb_table_name = _get_config_value(
            app_config,
            "logging.database.table_name",
            "JVSPATIAL_LOG_DB_TABLE_NAME",
            None,
        )
        log_dynamodb_region = _get_config_value(
            app_config, "logging.database.region", "JVSPATIAL_LOG_DB_REGION", None
        )
        log_dynamodb_endpoint_url = _get_config_value(
            app_config,
            "logging.database.endpoint_url",
            "JVSPATIAL_LOG_DB_ENDPOINT_URL",
            None,
        )
        log_dynamodb_access_key_id = _get_config_value(
            app_config, "logging.database.access_key_id", "AWS_ACCESS_KEY_ID", None
        )
        log_dynamodb_secret_access_key = _get_config_value(
            app_config,
            "logging.database.secret_access_key",
            "AWS_SECRET_ACCESS_KEY",
            None,
        )

        # Handle empty strings from unresolved placeholders
        if log_db_uri and log_db_uri.strip() == "":
            log_db_uri = os.getenv("JVAGENT_LOG_DB_URI") or mongodb_uri
        if log_db_path and log_db_path.strip() == "":
            log_db_path = None

        # Resolve relative log database path against app_root
        if log_db_path:
            log_db_path_obj = Path(log_db_path)
            if not log_db_path_obj.is_absolute():
                log_db_path = str(app_root_path / log_db_path)
                logger.debug(f"Resolved log database path to: {log_db_path}")
        if log_dynamodb_table_name and log_dynamodb_table_name.strip() == "":
            log_dynamodb_table_name = None
        if log_dynamodb_region and log_dynamodb_region.strip() == "":
            log_dynamodb_region = None
        if log_dynamodb_endpoint_url and log_dynamodb_endpoint_url.strip() == "":
            log_dynamodb_endpoint_url = None
        if log_dynamodb_access_key_id and log_dynamodb_access_key_id.strip() == "":
            log_dynamodb_access_key_id = None
        if (
            log_dynamodb_secret_access_key
            and log_dynamodb_secret_access_key.strip() == ""
        ):
            log_dynamodb_secret_access_key = None

        # Set logging database environment variables if specified
        if log_db_type:
            os.environ["JVSPATIAL_LOG_DB_TYPE"] = log_db_type
        if log_db_uri:
            os.environ["JVSPATIAL_LOG_DB_URI"] = log_db_uri
        if log_db_name:
            os.environ["JVSPATIAL_LOG_DB_NAME"] = log_db_name
        if log_db_path:
            os.environ["JVSPATIAL_LOG_DB_PATH"] = log_db_path

        # Set DynamoDB logging database environment variables if using DynamoDB
        if log_db_type == "dynamodb":
            if log_dynamodb_table_name:
                os.environ["JVSPATIAL_LOG_DB_TABLE_NAME"] = log_dynamodb_table_name
            if log_dynamodb_region:
                os.environ["JVSPATIAL_LOG_DB_REGION"] = log_dynamodb_region
            if log_dynamodb_endpoint_url:
                os.environ["JVSPATIAL_LOG_DB_ENDPOINT_URL"] = log_dynamodb_endpoint_url
            if log_dynamodb_access_key_id:
                os.environ["AWS_ACCESS_KEY_ID"] = log_dynamodb_access_key_id
            if log_dynamodb_secret_access_key:
                os.environ["AWS_SECRET_ACCESS_KEY"] = log_dynamodb_secret_access_key

        # Initialize with updated log_levels
        initialize_logging_database(
            log_levels=log_levels,
        )
    else:
        logger.info("Logging is disabled in configuration")

    # Import core endpoint modules so @endpoint decorators run and register.
    # jvspatial auto-registers: decorators register immediately when server exists;
    # sync_endpoint_modules handles uvicorn --reload double-load. Action-specific
    # endpoints (interact, pageindex, whatsapp, etc.) load via pre_import_action_modules_for_agents.
    _import_core_endpoint_modules()

    return server


async def pre_startup_bootstrap(
    server: Server, update_mode: Optional[str] = None, app_root: str = None
) -> bool:
    """Perform bootstrap tasks before server starts.

    This runs after the server is created (so context is initialized)
    but before the server starts running.

    Args:
        server: Server instance with initialized context
        update_mode: Update strategy - "merge" for non-destructive merge, "source" for
                     destructive overwrite from YAML, or None to skip existing.
        app_root: Path to the app root directory. If None, uses current working directory.

    Returns:
        True if admin user exists, False otherwise
    """
    try:
        # Bootstrap application graph
        await bootstrap_application_graph(update_mode=update_mode, app_root=app_root)

        # Initialize all actions by calling their on_startup() hooks
        # This ensures runtime components like channel adapters are initialized
        from jvagent.core.startup import run_app_startup

        await run_app_startup()

        # Ensure admin user exists
        admin_exists = await ensure_admin_user()

        return admin_exists
    except Exception as e:
        logger.error(f"❌ Bootstrap failed: {e}", exc_info=True)
        raise


def purge_app_data(app_root: str) -> None:
    """Purge application data (database and logs).

    Reads database configuration from app.yaml and environment variables to determine
    the actual paths to purge. Resolves relative paths relative to app_root.

    Args:
        app_root: Path to the app root directory.
    """
    from pathlib import Path

    if app_root is None:
        app_root = os.getcwd()

    app_root_path = Path(app_root).resolve()

    # Load app.yaml to get configured database paths
    app_config = {}
    try:
        app_yaml_path = app_root_path / "app.yaml"
        if app_yaml_path.exists():
            import yaml

            from jvagent.core.env_resolver import resolve_env_placeholders

            with open(app_yaml_path, "r", encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f)
                if yaml_data and "config" in yaml_data:
                    app_config = resolve_env_placeholders(yaml_data.get("config", {}))
    except Exception as e:
        logger.debug(f"Could not load app.yaml for purge: {e}")

    # Get database path from config (env var > app.yaml > default)
    db_path = os.getenv("JVSPATIAL_DB_PATH")
    if not db_path:
        db_path = _get_config_value(app_config, "database.path", None, "./jvagent_db")

    # Get log database path from config
    log_db_path = os.getenv("JVAGENT_LOG_DB_PATH")
    if not log_db_path:
        log_db_path = _get_config_value(
            app_config, "logging.database.path", None, "./jvagent_logs"
        )

    # Resolve paths relative to app_root if they are relative
    paths_to_purge = []
    for path_str in [db_path, log_db_path]:
        if path_str:
            path = Path(path_str)
            if not path.is_absolute():
                # Resolve relative path against app_root
                path = app_root_path / path_str
            paths_to_purge.append(path.resolve())

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


def main() -> None:
    """Main entry point for jvagent application."""
    import asyncio
    import sys
    from pathlib import Path

    # Parse command-line arguments
    args = sys.argv[1:]

    # Extract app root path (first positional argument that's not a flag or command)
    # This handles both: "jvagent /path/to/app bundle" and "jvagent bundle /path/to/app"
    app_root = None
    commands = ["run", "status", "agent", "action", "bootstrap", "bundle"]
    flags = ["--debug", "--update", "--migrate", "--purge", "--source", "--merge"]

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

    # Set the global app root for config loading in other modules
    from jvagent.core.app_context import set_app_root

    set_app_root(app_root)

    # Reload performance configs now that app root is set
    from jvagent.core.cache import reload_performance_config
    from jvagent.core.profiling import reload_profiling_config

    reload_performance_config()
    reload_profiling_config()

    # Load .env file from app root directory
    load_app_env(app_root=app_root)

    # Set database path environment variables BEFORE any database initialization
    # This must happen before any database operations to prevent jvspatial from using defaults
    from pathlib import Path

    app_root_path = Path(app_root).resolve()

    db_type = os.getenv("JVSPATIAL_DB_TYPE", "json")
    db_path = os.getenv("JVSPATIAL_DB_PATH", "./jvagent_db")
    if not db_path or db_path.strip() == "":
        db_path = "./jvagent_db"

    # Resolve relative database path against app_root
    db_path_obj = Path(db_path)
    if not db_path_obj.is_absolute():
        db_path = str(app_root_path / db_path)
        logger.debug(f"Resolved database path to: {db_path}")

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

    # Check for --update flag and sub-flags (--source / --merge)
    has_update = "--update" in args or "--migrate" in args
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

    args = [
        arg
        for arg in args
        if arg not in ["--update", "--migrate", "--source", "--merge"]
    ]

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
    import sys
    from pathlib import Path

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
    --update, --migrate        Update existing agents and actions from YAML files (non-destructive merge).
                                Applies source changes while preserving database state.
    --update --source          Destructive update: fully overwrite database state from source YAML files.
                                Deletes and recreates action nodes (child nodes are lost).
    --update --merge           Explicit non-destructive merge (same as --update alone).
    --purge                    Delete existing database and logs before starting (development mode only)
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
    jvagent /path/to/my_app --update           # Run with merge update (non-destructive)
    jvagent /path/to/my_app --update --source  # Run with source update (destructive)
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
    import asyncio

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
            import asyncio

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
        server.run()
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
    from pathlib import Path

    from jvagent.core.app_loader import AppLoader

    if app_root is None:
        app_root = os.getcwd()

    app_root_path = Path(app_root).resolve()

    # Initialize database context with paths resolved against app_root
    db_type = os.getenv("JVSPATIAL_DB_TYPE", "json")
    db_path = os.getenv("JVSPATIAL_DB_PATH", "./jvagent_db")
    if not db_path or db_path.strip() == "":
        db_path = "./jvagent_db"

    # Resolve relative database path against app_root
    db_path_obj = Path(db_path)
    if not db_path_obj.is_absolute():
        db_path = str(app_root_path / db_path)

    os.environ["JVSPATIAL_DB_TYPE"] = db_type
    if db_type == "json":
        os.environ["JVSPATIAL_JSONDB_PATH"] = db_path
    elif db_type == "sqlite":
        os.environ["JVSPATIAL_SQLITE_PATH"] = db_path

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
    from pathlib import Path

    if app_root is None:
        app_root = os.getcwd()

    app_root_path = Path(app_root).resolve()

    # Load .env file from app root directory
    load_app_env(app_root=app_root)

    # Initialize database context with paths resolved against app_root
    db_type = os.getenv("JVSPATIAL_DB_TYPE", "json")
    db_path = os.getenv("JVSPATIAL_DB_PATH", "./jvagent_db")
    if not db_path or db_path.strip() == "":
        db_path = "./jvagent_db"

    # Resolve relative database path against app_root
    db_path_obj = Path(db_path)
    if not db_path_obj.is_absolute():
        db_path = str(app_root_path / db_path)

    os.environ["JVSPATIAL_DB_TYPE"] = db_type
    if db_type == "json":
        os.environ["JVSPATIAL_JSONDB_PATH"] = db_path
    elif db_type == "sqlite":
        os.environ["JVSPATIAL_SQLITE_PATH"] = db_path

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


def handle_agent_command(args: List[str], app_root: str = None) -> None:
    """Handle agent management commands.

    Note: Agents are installed automatically from app.yaml when running jvagent or bootstrap.
    This command is for listing and uninstalling existing agents only.

    Args:
        args: Command arguments
        app_root: Path to the app root directory. If None, uses current working directory.
    """
    import asyncio
    from pathlib import Path

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

    # Initialize database context - resolve path against app_root if relative
    db_type = os.getenv("JVSPATIAL_DB_TYPE", "json")
    db_path = os.getenv("JVSPATIAL_JSONDB_PATH") or os.getenv(
        "JVSPATIAL_DB_PATH", "./jvagent_db"
    )
    db_path_obj = Path(db_path)
    if not db_path_obj.is_absolute():
        db_path = str(Path(app_root).resolve() / db_path)

    os.environ["JVSPATIAL_DB_TYPE"] = db_type
    if db_type == "json":
        os.environ["JVSPATIAL_JSONDB_PATH"] = db_path
    elif db_type == "sqlite":
        os.environ["JVSPATIAL_SQLITE_PATH"] = db_path

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
    from pathlib import Path

    if app_root is None:
        app_root = os.getcwd()

    if not args:
        print("Usage: jvagent action <command>")
        print("Commands: list, enable, disable")
        return

    command = args[0]

    # Initialize database context - resolve path against app_root if relative
    db_type = os.getenv("JVSPATIAL_DB_TYPE", "json")
    db_path = os.getenv("JVSPATIAL_JSONDB_PATH") or os.getenv(
        "JVSPATIAL_DB_PATH", "./jvagent_db"
    )
    db_path_obj = Path(db_path)
    if not db_path_obj.is_absolute():
        db_path = str(Path(app_root).resolve() / db_path)

    os.environ["JVSPATIAL_DB_TYPE"] = db_type
    if db_type == "json":
        os.environ["JVSPATIAL_JSONDB_PATH"] = db_path
    elif db_type == "sqlite":
        os.environ["JVSPATIAL_SQLITE_PATH"] = db_path

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


if __name__ == "__main__":
    main()
