"""Server configuration and bootstrap for jvagent."""

import logging
import os
from typing import Optional

from jvspatial.api import Server
from jvspatial.api.config_groups import (
    AuthConfig,
    CORSConfig,
    DatabaseConfig,
    FileStorageConfig,
)
from jvspatial.env import clear_load_env_cache

from jvagent import __version__
from jvagent.cli.bootstrap import bootstrap_application_graph, ensure_admin_user
from jvagent.core.bootstrap_update_mode import (
    reset_app_update_mode_after_successful_bootstrap,
    resolve_bootstrap_update_mode,
)
from jvagent.core.config import (
    get_config_value,
    get_file_storage_config,
    load_app_config,
    normalize_empty,
    resolve_db_path,
    resolve_log_db_path,
)
from jvagent.env import load_env as load_jvagent_env

logger = logging.getLogger(__name__)


def _set_db_env_from_config(app_root: str) -> None:
    """Set database environment variables from app config.

    Must be called before any database initialization.
    Uses canonical ``JVSPATIAL_DB_PATH`` only (jvspatial forbids JSONDB/SQLITE env keys).
    """
    app_config = load_app_config(app_root)
    db_type = get_config_value(app_config, "database.type", "JVSPATIAL_DB_TYPE", "json")
    db_path = resolve_db_path(app_root, app_config, db_type)
    os.environ["JVSPATIAL_DB_TYPE"] = db_type
    if db_type in ("json", "sqlite"):
        os.environ["JVSPATIAL_DB_PATH"] = db_path
    for _removed in ("JVSPATIAL_JSONDB_PATH", "JVSPATIAL_SQLITE_PATH"):
        os.environ.pop(_removed, None)


def _import_core_endpoint_modules() -> None:
    """Import core endpoint modules so @endpoint decorators register with the server."""
    from jvagent.action import endpoints as _action_endpoints  # noqa: F401
    from jvagent.core import endpoints as _core_endpoints  # noqa: F401
    from jvagent.logging import endpoints as _logging_endpoints  # noqa: F401


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
    if app_root is None:
        app_root = os.getcwd()

    app_config = load_app_config(app_root)

    # Get configuration with priority: env var > app.yaml > default
    # Server configuration
    title = get_config_value(app_config, "server.title", "JVAGENT_TITLE", "jvagent API")
    description = get_config_value(
        app_config,
        "server.description",
        "JVAGENT_DESCRIPTION",
        "jvagent Agentive Platform API",
    )
    version = get_config_value(
        app_config, "server.version", "JVAGENT_VERSION", __version__
    )
    host = get_config_value(app_config, "server.host", "JVAGENT_HOST", "127.0.0.1")
    port = int(get_config_value(app_config, "server.port", "JVAGENT_PORT", 8000))

    # Database configuration
    db_type = get_config_value(app_config, "database.type", "JVSPATIAL_DB_TYPE", "json")
    db_path = resolve_db_path(app_root, app_config, db_type)

    # MongoDB configuration
    mongodb_uri = get_config_value(
        app_config, "database.uri", "JVSPATIAL_MONGODB_URI", "mongodb://localhost:27017"
    )
    mongodb_db_name = get_config_value(
        app_config, "database.name", "JVSPATIAL_MONGODB_DB_NAME", None
    )

    if normalize_empty(mongodb_uri) is None:
        mongodb_uri = load_jvagent_env().mongodb_uri

    # DynamoDB configuration
    dynamodb_table_name = get_config_value(
        app_config, "database.table_name", "JVSPATIAL_DYNAMODB_TABLE_NAME", None
    )
    dynamodb_region = get_config_value(
        app_config, "database.region", "JVSPATIAL_DYNAMODB_REGION", None
    )
    dynamodb_endpoint_url = get_config_value(
        app_config, "database.endpoint_url", "JVSPATIAL_DYNAMODB_ENDPOINT_URL", None
    )
    dynamodb_access_key_id = get_config_value(
        app_config, "database.access_key_id", "AWS_ACCESS_KEY_ID", None
    )
    dynamodb_secret_access_key = get_config_value(
        app_config, "database.secret_access_key", "AWS_SECRET_ACCESS_KEY", None
    )

    dynamodb_table_name = normalize_empty(dynamodb_table_name) or None
    dynamodb_region = normalize_empty(dynamodb_region) or None
    dynamodb_endpoint_url = normalize_empty(dynamodb_endpoint_url) or None
    dynamodb_access_key_id = normalize_empty(dynamodb_access_key_id) or None
    dynamodb_secret_access_key = normalize_empty(dynamodb_secret_access_key) or None

    # MongoDB / DynamoDB: pass via DatabaseConfig only (avoid mutating os.environ).

    if db_type in ("json", "sqlite"):
        os.environ["JVSPATIAL_DB_PATH"] = db_path
    for _removed in ("JVSPATIAL_JSONDB_PATH", "JVSPATIAL_SQLITE_PATH"):
        os.environ.pop(_removed, None)

    # Graph endpoint configuration
    graph_endpoint_enabled = get_config_value(
        app_config,
        "api.graph_endpoint_enabled",
        "JVSPATIAL_GRAPH_ENDPOINT_ENABLED",
        False,
    )

    # Authentication configuration (enabled by default for jvagent)
    auth_enabled = get_config_value(
        app_config, "auth.enabled", "JVAGENT_AUTH_ENABLED", True
    )
    jwt_secret_raw = normalize_empty(load_jvagent_env().jwt_secret_key)
    jwt_secret = jwt_secret_raw or ""
    if auth_enabled and not jwt_secret_raw:
        raise ValueError(
            "Authentication is enabled but JVSPATIAL_JWT_SECRET_KEY is not set (or is empty). "
            "Set a strong secret in the environment or disable auth with JVAGENT_AUTH_ENABLED=false."
        )
    jwt_expire_minutes = int(
        get_config_value(
            app_config, "auth.jwt_expire_minutes", "JVSPATIAL_JWT_EXPIRE_MINUTES", 60
        )
    )

    # API key management endpoints (/auth/api-keys) - enabled by default when auth is enabled
    # Supports auth.api_key_management_enabled and legacy auth.api_key_enabled
    api_key_management_enabled = get_config_value(
        app_config,
        "auth.api_key_management_enabled",
        "JVAGENT_API_KEY_MANAGEMENT_ENABLED",
        None,
    )
    if api_key_management_enabled is None:
        api_key_management_enabled = get_config_value(
            app_config,
            "auth.api_key_enabled",
            "JVAGENT_API_KEY_AUTH_ENABLED",
            auth_enabled,
        )
    api_key_prefix = get_config_value(
        app_config, "auth.api_key_prefix", "JVAGENT_API_KEY_PREFIX", "jv_"
    )
    api_key_header = get_config_value(
        app_config, "auth.api_key_header", "JVAGENT_API_KEY_HEADER", "x-api-key"
    )

    # Bootstrap admin: jvspatial runs AuthService.bootstrap_admin on server start
    # (lifecycle hook). jvagent's ensure_admin_user() also calls bootstrap_admin when
    # count_users()==0 so `jvagent bootstrap` without run still creates an admin.
    _env = load_jvagent_env()
    admin_username = _env.admin_username
    admin_password = _env.admin_password
    admin_email = _env.admin_email

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
    log_level = _env.log_level or ("debug" if debug else "info")

    # Override with app.yaml development.debug if available
    debug_mode = get_config_value(
        app_config, "development.debug", "JVSPATIAL_DEBUG", False
    )
    if debug_mode:
        log_level = "debug"

    # CORS configuration
    cors_enabled = get_config_value(
        app_config, "cors.enabled", "JVSPATIAL_CORS_ENABLED", True
    )
    cors_origins_str = get_config_value(
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
        "/api/agents/*/interact",  # Anonymous interact endpoint
    ]
    app_exempt_paths = get_config_value(app_config, "auth.exempt_paths", None, None)
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

    fs_cfg = get_file_storage_config(app_root, app_config)
    file_storage_config = FileStorageConfig(
        file_storage_enabled=fs_cfg["enabled"],
        file_storage_provider=fs_cfg["provider"],
        file_storage_root=fs_cfg["root_dir"],
        file_storage_base_url=fs_cfg["base_url"],
        file_storage_max_size=fs_cfg["max_size"],
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
    logging_enabled = get_config_value(
        app_config, "logging.enabled", "JVSPATIAL_DB_LOGGING_ENABLED", True
    )
    if logging_enabled:
        # Get log levels from app.yaml or environment
        log_levels_str = get_config_value(
            app_config,
            "logging.levels",
            "JVSPATIAL_DB_LOGGING_LEVELS",
            "ERROR,CRITICAL",
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

        # Get logging database config from app.yaml / JVSPATIAL_LOG_DB_*
        log_db_type = get_config_value(
            app_config, "logging.database.type", "JVSPATIAL_LOG_DB_TYPE", None
        )
        log_db_uri = get_config_value(
            app_config, "logging.database.uri", "JVSPATIAL_LOG_DB_URI", None
        )
        log_db_name = get_config_value(
            app_config, "logging.database.name", "JVSPATIAL_LOG_DB_NAME", "jvagent_logs"
        )

        # DynamoDB logging database configuration
        log_dynamodb_table_name = get_config_value(
            app_config,
            "logging.database.table_name",
            "JVSPATIAL_LOG_DB_TABLE_NAME",
            None,
        )
        log_dynamodb_region = get_config_value(
            app_config, "logging.database.region", "JVSPATIAL_LOG_DB_REGION", None
        )
        log_dynamodb_endpoint_url = get_config_value(
            app_config,
            "logging.database.endpoint_url",
            "JVSPATIAL_LOG_DB_ENDPOINT_URL",
            None,
        )
        log_dynamodb_access_key_id = get_config_value(
            app_config, "logging.database.access_key_id", "AWS_ACCESS_KEY_ID", None
        )
        log_dynamodb_secret_access_key = get_config_value(
            app_config,
            "logging.database.secret_access_key",
            "AWS_SECRET_ACCESS_KEY",
            None,
        )

        if normalize_empty(log_db_uri) is None:
            log_db_uri = load_jvagent_env().log_db_uri or mongodb_uri
        log_db_path = resolve_log_db_path(app_root, app_config)
        log_dynamodb_table_name = normalize_empty(log_dynamodb_table_name) or None
        log_dynamodb_region = normalize_empty(log_dynamodb_region) or None
        log_dynamodb_endpoint_url = normalize_empty(log_dynamodb_endpoint_url) or None
        log_dynamodb_access_key_id = normalize_empty(log_dynamodb_access_key_id) or None
        log_dynamodb_secret_access_key = (
            normalize_empty(log_dynamodb_secret_access_key) or None
        )

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

        # get_logging_config uses cached jvspatial load_env(); refresh after os.environ updates
        clear_load_env_cache()

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

    # jvspatial load_env() is LRU-cached; refresh after JVSPATIAL_* updates (DB path, logging DB).
    clear_load_env_cache()

    return server


async def pre_startup_bootstrap(
    server: Server, update_mode: Optional[str] = None, app_root: str = None
) -> bool:
    """Perform bootstrap tasks before server starts.

    This runs after the server is created (so context is initialized)
    but before the server starts running.

    Args:
        server: Server instance with initialized context
        update_mode: From CLI ``--update`` / ``--source`` (``merge``, ``source``, or None).
                     When None, effective mode may come from persisted ``App.update_mode``
                     (see ``resolve_bootstrap_update_mode``).
        app_root: Path to the app root directory. If None, uses current working directory.

    Returns:
        True if admin user exists, False otherwise
    """
    try:
        effective_update_mode = await resolve_bootstrap_update_mode(update_mode)
        await bootstrap_application_graph(
            update_mode=effective_update_mode, app_root=app_root
        )

        # Initialize all actions by calling their on_startup() hooks
        # This ensures runtime components like channel adapters are initialized
        from jvagent.core.startup import run_app_startup

        await run_app_startup()

        # Ensure admin user exists
        admin_exists = await ensure_admin_user()

        await reset_app_update_mode_after_successful_bootstrap()

        return admin_exists
    except Exception as e:
        logger.error(f"❌ Bootstrap failed: {e}", exc_info=True)
        raise
