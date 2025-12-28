"""Configuration for logging system."""

import os
from typing import Any, Dict, Optional

from jvspatial.db import create_database, get_database_manager


def get_logging_config() -> Dict[str, Any]:
    """Get logging configuration from environment variables and defaults.

    Returns:
        Dictionary with logging configuration
    """
    # Check if logging is enabled globally
    logging_enabled = os.getenv("JVAGENT_LOGGING_ENABLED", "true").lower() == "true"

    # Get database type (defaults to same as prime DB)
    db_type = os.getenv("JVAGENT_LOG_DB_TYPE") or os.getenv("JVSPATIAL_DB_TYPE", "json")

    # Get database path/connection based on type
    if db_type == "json":
        db_path = os.getenv("JVAGENT_LOG_DB_PATH", "./jvagent_logs")
        config = {
            "enabled": logging_enabled,
            "db_type": db_type,
            "db_path": db_path,
        }
    elif db_type == "sqlite":
        db_path = os.getenv("JVAGENT_LOG_DB_PATH", "jvagent_logs/sqlite/jvspatial_logs.db")
        config = {
            "enabled": logging_enabled,
            "db_type": db_type,
            "db_path": db_path,
        }
    elif db_type == "mongodb":
        db_uri = os.getenv("JVAGENT_LOG_DB_URI") or os.getenv(
            "JVSPATIAL_MONGODB_URI", "mongodb://localhost:27017"
        )
        db_name = os.getenv("JVAGENT_LOG_DB_NAME", "jvagent_logs")
        config = {
            "enabled": logging_enabled,
            "db_type": db_type,
            "db_uri": db_uri,
            "db_name": db_name,
        }
    elif db_type == "dynamodb":
        table_name = os.getenv("JVAGENT_LOG_DB_TABLE_NAME", "jvspatial_logs")
        region_name = os.getenv("JVAGENT_LOG_DB_REGION") or os.getenv(
            "JVSPATIAL_DYNAMODB_REGION", "us-east-1"
        )
        endpoint_url = os.getenv("JVAGENT_LOG_DB_ENDPOINT_URL") or os.getenv(
            "JVSPATIAL_DYNAMODB_ENDPOINT_URL"
        )
        aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID")
        aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        config = {
            "enabled": logging_enabled,
            "db_type": db_type,
            "table_name": table_name,
            "region_name": region_name,
            "endpoint_url": endpoint_url,
            "aws_access_key_id": aws_access_key_id,
            "aws_secret_access_key": aws_secret_access_key,
        }
    else:
        # Fallback to JSON
        db_path = os.getenv("JVAGENT_LOG_DB_PATH", "./jvagent_logs")
        config = {
            "enabled": logging_enabled,
            "db_type": "json",
            "db_path": db_path,
        }

    # Retention default
    config["retention_default_days"] = int(
        os.getenv("JVAGENT_LOG_RETENTION_DEFAULT_DAYS", "60")
    )

    # Text normalization setting (from jvspatial, applies to all database persistence)
    # This is included in config for reference, but the actual setting is controlled
    # by JVSPATIAL_TEXT_NORMALIZATION_ENABLED environment variable
    try:
        from jvspatial.utils.normalization import is_text_normalization_enabled
        config["text_normalization_enabled"] = is_text_normalization_enabled()
    except ImportError:
        # Fallback if jvspatial is not available
        config["text_normalization_enabled"] = (
            os.getenv("JVSPATIAL_TEXT_NORMALIZATION_ENABLED", "true").lower() == "true"
        )

    # Log level for database logging (minimum level to persist to database)
    # ERROR/CRITICAL are always logged regardless of this setting
    import logging
    log_db_level_str = os.getenv("JVAGENT_LOG_DB_LEVEL", "ERROR").upper()
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if log_db_level_str not in valid_levels:
        log_db_level_str = "ERROR"
    config["log_db_level"] = getattr(logging, log_db_level_str, logging.ERROR)
    config["log_db_level_str"] = log_db_level_str  # Keep string version for reference

    return config


def initialize_logging_database(config: Optional[Dict[str, Any]] = None) -> bool:
    """Initialize and register the logging database.

    Args:
        config: Optional configuration dictionary. If not provided, reads from environment.

    Returns:
        True if logging database was initialized, False if logging is disabled
    """
    if config is None:
        config = get_logging_config()

    # Check if logging is enabled
    if not config.get("enabled", True):
        return False

    try:
        manager = get_database_manager()
        db_type = config["db_type"]

        # Create logging database based on type
        if db_type == "json":
            log_db = create_database(
                db_type="json",
                base_path=config["db_path"],
            )
        elif db_type == "sqlite":
            log_db = create_database(
                db_type="sqlite",
                db_path=config["db_path"],
            )
        elif db_type == "mongodb":
            log_db = create_database(
                db_type="mongodb",
                uri=config["db_uri"],
                db_name=config["db_name"],
            )
        elif db_type == "dynamodb":
            log_db = create_database(
                db_type="dynamodb",
                table_name=config["table_name"],
                region_name=config["region_name"],
                endpoint_url=config.get("endpoint_url"),
                aws_access_key_id=config.get("aws_access_key_id"),
                aws_secret_access_key=config.get("aws_secret_access_key"),
            )
        else:
            # Fallback to JSON
            log_db = create_database(
                db_type="json",
                base_path=config.get("db_path", "./jvagent_logs"),
            )

        # Register as "logs" database
        manager.register_database("logs", log_db)
        
        # Install DBLogHandler if not already installed
        import logging
        root_logger = logging.getLogger()
        
        # Check if handler already exists
        from jvagent.logging.handler import DBLogHandler as DBLogHandlerClass
        handler_exists = any(
            isinstance(h, DBLogHandlerClass)
            for h in root_logger.handlers
        )
        
        if not handler_exists:
            from jvagent.logging.handler import DBLogHandler
            
            # Get log_db_level from config (default: ERROR)
            log_db_level = config.get("log_db_level", logging.ERROR)
            
            # Create and install handler
            db_handler = DBLogHandler(log_db_level=log_db_level)
            root_logger.addHandler(db_handler)
            
            logger = logging.getLogger(__name__)
            log_level_str = config.get("log_db_level_str", "ERROR")
            logger.info(
                f"DBLogHandler installed: log_db_level={log_level_str} "
                f"(ERROR/CRITICAL always logged, {log_level_str} and above logged to database)"
            )
        
        # Log success
        logger = logging.getLogger(__name__)
        logger.info(f"Logging database initialized: type={db_type}, path={config.get('db_path', 'N/A')}")
        
        return True

    except Exception as e:
        # Log error but don't fail startup
        import logging

        logger = logging.getLogger(__name__)
        logger.error(f"Failed to initialize logging database: {e}", exc_info=True)
        return False

