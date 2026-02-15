"""Configuration for PageIndex graph database.

This module provides database initialization and configuration for the PageIndex
document index, using a separate database (like logs) for document structure persistence.
"""

import logging
import os
from typing import Any, Dict, Optional

from jvspatial.db import create_database, get_database_manager

logger = logging.getLogger(__name__)

PAGEINDEX_DB_NAME = "pageindex_db"

_pageindex_node_summary: Optional[bool] = None
_pageindex_node_text: Optional[bool] = None
_pageindex_doc_description: Optional[bool] = None
_pageindex_max_token_num_each_node: Optional[int] = None
_pageindex_summary_token_threshold: Optional[int] = None


def set_pageindex_node_summary(value: Optional[bool]) -> None:
    """Set whether to generate node summaries during ingestion (from action config)."""
    global _pageindex_node_summary
    _pageindex_node_summary = value


def get_pageindex_node_summary() -> bool:
    """Get node_summary config. Defaults to False when not set (off by default)."""
    if _pageindex_node_summary is None:
        return False
    return _pageindex_node_summary


def set_pageindex_node_text(value: Optional[bool]) -> None:
    """Set whether to add node text during ingestion (from action config)."""
    global _pageindex_node_text
    _pageindex_node_text = value


def get_pageindex_node_text() -> bool:
    """Get node_text config. Defaults to True when not set."""
    if _pageindex_node_text is None:
        return True
    return _pageindex_node_text


def set_pageindex_doc_description(value: Optional[bool]) -> None:
    """Set whether to add doc description during ingestion (from action config)."""
    global _pageindex_doc_description
    _pageindex_doc_description = value


def get_pageindex_doc_description() -> bool:
    """Get doc_description config. Defaults to False when not set."""
    if _pageindex_doc_description is None:
        return False
    return _pageindex_doc_description


def set_pageindex_max_token_num_each_node(value: Optional[int]) -> None:
    """Set max tokens per node for PDF ingestion (from action config)."""
    global _pageindex_max_token_num_each_node
    _pageindex_max_token_num_each_node = value


def get_pageindex_max_token_num_each_node() -> Optional[int]:
    """Get max_token_num_each_node config. Returns None when not set."""
    return _pageindex_max_token_num_each_node


def set_pageindex_summary_token_threshold(value: Optional[int]) -> None:
    """Set token threshold for node summaries in markdown (from action config)."""
    global _pageindex_summary_token_threshold
    _pageindex_summary_token_threshold = value


def get_pageindex_summary_token_threshold() -> Optional[int]:
    """Get summary_token_threshold config. Returns None when not set (documents.py uses 200)."""
    return _pageindex_summary_token_threshold


def _get_prime_db_root() -> str:
    """Get the prime database root path (shared with prime db).

    Uses JVSPATIAL_JSONDB_PATH when set (json type). For sqlite, derives from
    JVSPATIAL_SQLITE_PATH (e.g. jvdb/sqlite/jvspatial.db -> jvdb).
    """
    json_path = os.getenv("JVSPATIAL_JSONDB_PATH")
    if json_path:
        return json_path
    sqlite_path = os.getenv("JVSPATIAL_SQLITE_PATH", "jvdb/sqlite/jvspatial.db")
    # Derive root: jvdb/sqlite/jvspatial.db -> jvdb
    return os.path.dirname(os.path.dirname(sqlite_path)) or "jvdb"


def _get_shared_db_root() -> str:
    """Get shared root for prime db and PageIndex (parent of prime db folder)."""
    prime_root = _get_prime_db_root()
    shared = os.path.dirname(prime_root)
    return shared if shared else "."


def _get_pageindex_db_path() -> str:
    """Get PageIndex db path, defaulting to shared root + /pageindex_db when unset."""
    explicit = os.getenv("JVSPATIAL_PAGEINDEX_DB_PATH")
    if explicit:
        return explicit
    shared_root = _get_shared_db_root()
    return os.path.join(shared_root, PAGEINDEX_DB_NAME)


def get_pageindex_config() -> Dict[str, Any]:
    """Get PageIndex database configuration from environment variables and defaults.

    Returns:
        Dictionary with PageIndex database configuration

    Environment Variables:
        JVSPATIAL_PAGEINDEX_DB_TYPE: Database type (json, sqlite, mongodb, dynamodb)
        JVSPATIAL_PAGEINDEX_DB_PATH: Path for file-based databases (json, sqlite).
            When unset, defaults to {parent_of_prime_db}/pageindex_db (sibling of prime db).
        JVSPATIAL_PAGEINDEX_DB_URI: Connection URI for MongoDB
        JVSPATIAL_PAGEINDEX_DB_NAME: Database name for MongoDB (default: pageindex_db)
        JVSPATIAL_PAGEINDEX_DB_TABLE_NAME: Table name for DynamoDB
        JVSPATIAL_PAGEINDEX_DB_REGION: AWS region for DynamoDB
    """
    db_type = os.getenv("JVSPATIAL_PAGEINDEX_DB_TYPE") or os.getenv(
        "JVSPATIAL_DB_TYPE", "json"
    )

    if db_type == "json":
        db_path = _get_pageindex_db_path()
        return {"db_type": db_type, "db_path": db_path}
    elif db_type == "sqlite":
        explicit = os.getenv("JVSPATIAL_PAGEINDEX_DB_PATH")
        if explicit:
            db_path = explicit
        else:
            shared_root = _get_shared_db_root()
            db_path = os.path.join(shared_root, PAGEINDEX_DB_NAME, "sqlite", "pageindex.db")
        return {"db_type": db_type, "db_path": db_path}
    elif db_type == "mongodb":
        db_uri = os.getenv("JVSPATIAL_PAGEINDEX_DB_URI") or os.getenv(
            "JVSPATIAL_MONGODB_URI", "mongodb://localhost:27017"
        )
        db_name = os.getenv("JVSPATIAL_PAGEINDEX_DB_NAME", "pageindex_db")
        return {"db_type": db_type, "db_uri": db_uri, "db_name": db_name}
    elif db_type == "dynamodb":
        table_name = os.getenv("JVSPATIAL_PAGEINDEX_DB_TABLE_NAME", "pageindex_db")
        region_name = os.getenv("JVSPATIAL_PAGEINDEX_DB_REGION") or os.getenv(
            "JVSPATIAL_DYNAMODB_REGION", "us-east-1"
        )
        endpoint_url = os.getenv("JVSPATIAL_PAGEINDEX_DB_ENDPOINT_URL") or os.getenv(
            "JVSPATIAL_DYNAMODB_ENDPOINT_URL"
        )
        return {
            "db_type": db_type,
            "table_name": table_name,
            "region_name": region_name,
            "endpoint_url": endpoint_url,
        }
    else:
        db_path = _get_pageindex_db_path()
        return {"db_type": "json", "db_path": db_path}


def initialize_pageindex_database(config: Optional[Dict[str, Any]] = None) -> bool:
    """Initialize and register the PageIndex graph database.

    Args:
        config: Optional configuration dictionary. If not provided, reads from environment.

    Returns:
        True if database was initialized, False otherwise
    """
    if config is None:
        config = get_pageindex_config()

    try:
        manager = get_database_manager()
        db_type = config["db_type"]
        db_name = PAGEINDEX_DB_NAME

        if db_type == "json":
            pageindex_db = create_database(
                db_type="json",
                base_path=config["db_path"],
            )
        elif db_type == "sqlite":
            pageindex_db = create_database(
                db_type="sqlite",
                db_path=config["db_path"],
            )
        elif db_type == "mongodb":
            pageindex_db = create_database(
                db_type="mongodb",
                uri=config["db_uri"],
                db_name=config["db_name"],
            )
        elif db_type == "dynamodb":
            pageindex_db = create_database(
                db_type="dynamodb",
                table_name=config["table_name"],
                region_name=config["region_name"],
                endpoint_url=config.get("endpoint_url"),
                aws_access_key_id=config.get("aws_access_key_id"),
                aws_secret_access_key=config.get("aws_secret_access_key"),
            )
        else:
            pageindex_db = create_database(
                db_type="json",
                base_path=config.get("db_path", "./pageindex_db"),
            )

        try:
            manager.get_database(db_name)
            logger.debug(f"PageIndex database '{db_name}' already registered")
        except (ValueError, KeyError):
            manager.register_database(db_name, pageindex_db)
            logger.info(
                f"PageIndex database initialized: type={db_type}, name={db_name}"
            )

        return True

    except Exception as e:
        logger.error(f"Failed to initialize PageIndex database: {e}", exc_info=True)
        return False


__all__ = [
    "get_pageindex_config",
    "get_pageindex_doc_description",
    "get_pageindex_max_token_num_each_node",
    "get_pageindex_node_summary",
    "get_pageindex_node_text",
    "get_pageindex_summary_token_threshold",
    "initialize_pageindex_database",
    "PAGEINDEX_DB_NAME",
    "set_pageindex_doc_description",
    "set_pageindex_max_token_num_each_node",
    "set_pageindex_node_summary",
    "set_pageindex_node_text",
    "set_pageindex_summary_token_threshold",
]
