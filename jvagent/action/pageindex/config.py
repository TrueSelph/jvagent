"""Configuration for PageIndex graph database.

This module provides database initialization and configuration for the PageIndex
document index, using a separate database (like logs) for document structure persistence.

All mutable config uses contextvars.ContextVar for async-safety so concurrent
coroutines (e.g. multiple agent requests) cannot stomp on each other's settings.
"""

import contextvars
import logging
import os
import threading
from typing import Any, Dict, Optional

from jvspatial.db import create_database, get_database_manager

logger = logging.getLogger(__name__)

PAGEINDEX_DB_NAME = "pageindex_db"

_pageindex_node_summary: contextvars.ContextVar[Optional[bool]] = (
    contextvars.ContextVar("_pageindex_node_summary", default=None)
)
_pageindex_node_text: contextvars.ContextVar[Optional[bool]] = contextvars.ContextVar(
    "_pageindex_node_text", default=None
)
_pageindex_doc_description: contextvars.ContextVar[Optional[bool]] = (
    contextvars.ContextVar("_pageindex_doc_description", default=None)
)
_pageindex_max_token_num_each_node: contextvars.ContextVar[Optional[int]] = (
    contextvars.ContextVar("_pageindex_max_token_num_each_node", default=None)
)
_pageindex_summary_token_threshold: contextvars.ContextVar[Optional[int]] = (
    contextvars.ContextVar("_pageindex_summary_token_threshold", default=None)
)
_pageindex_max_summary_chars: contextvars.ContextVar[Optional[int]] = (
    contextvars.ContextVar("_pageindex_max_summary_chars", default=None)
)
_pageindex_max_tree_prompt_tokens: contextvars.ContextVar[Optional[int]] = (
    contextvars.ContextVar("_pageindex_max_tree_prompt_tokens", default=None)
)
_pageindex_enable_lexical_index: contextvars.ContextVar[Optional[bool]] = (
    contextvars.ContextVar("_pageindex_enable_lexical_index", default=None)
)
_pageindex_candidate_k: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "_pageindex_candidate_k", default=None
)
_pageindex_max_docs_for_tree_search: contextvars.ContextVar[Optional[int]] = (
    contextvars.ContextVar("_pageindex_max_docs_for_tree_search", default=None)
)


def set_pageindex_node_summary(value: Optional[bool]) -> None:
    """Set whether to generate node summaries during ingestion (from action config)."""
    _pageindex_node_summary.set(value)


def get_pageindex_node_summary() -> bool:
    """Get node_summary config. Defaults to False when not set (off by default)."""
    v = _pageindex_node_summary.get()
    return v if v is not None else False


def set_pageindex_node_text(value: Optional[bool]) -> None:
    """Set whether to add node text during ingestion (from action config)."""
    _pageindex_node_text.set(value)


def get_pageindex_node_text() -> bool:
    """Get node_text config. Defaults to True when not set."""
    v = _pageindex_node_text.get()
    return v if v is not None else True


def set_pageindex_doc_description(value: Optional[bool]) -> None:
    """Set whether to add doc description during ingestion (from action config)."""
    _pageindex_doc_description.set(value)


def get_pageindex_doc_description() -> bool:
    """Get doc_description config. Defaults to False when not set."""
    v = _pageindex_doc_description.get()
    return v if v is not None else False


def set_pageindex_max_token_num_each_node(value: Optional[int]) -> None:
    """Set max tokens per node for PDF ingestion (from action config)."""
    _pageindex_max_token_num_each_node.set(value)


def get_pageindex_max_token_num_each_node() -> Optional[int]:
    """Get max_token_num_each_node config. Returns None when not set."""
    return _pageindex_max_token_num_each_node.get()


def set_pageindex_summary_token_threshold(value: Optional[int]) -> None:
    """Set token threshold for node summaries in markdown (from action config)."""
    _pageindex_summary_token_threshold.set(value)


def get_pageindex_summary_token_threshold() -> Optional[int]:
    """Get summary_token_threshold config. Returns None when not set (documents.py uses 200)."""
    return _pageindex_summary_token_threshold.get()


def set_pageindex_max_summary_chars(value: Optional[int]) -> None:
    """Set max chars per node summary in tree prompt (retrieval display only)."""
    _pageindex_max_summary_chars.set(value)


def get_pageindex_max_summary_chars() -> int:
    """Get max_summary_chars. Defaults to 300 when not set."""
    v = _pageindex_max_summary_chars.get()
    return v if v is not None else 300


def set_pageindex_max_tree_prompt_tokens(value: Optional[int]) -> None:
    """Set max tokens for tree in tree-search prompt; over budget triggers fallback to direct."""
    _pageindex_max_tree_prompt_tokens.set(value)


def get_pageindex_max_tree_prompt_tokens() -> int:
    """Get max_tree_prompt_tokens. Defaults to 16000 when not set."""
    v = _pageindex_max_tree_prompt_tokens.get()
    return v if v is not None else 16000


def set_pageindex_enable_lexical_index(value: Optional[bool]) -> None:
    """Enable/disable the lexical candidate index for two-stage retrieval."""
    _pageindex_enable_lexical_index.set(value)


def get_pageindex_enable_lexical_index() -> bool:
    """Get enable_lexical_index. Defaults to True when not set."""
    v = _pageindex_enable_lexical_index.get()
    return v if v is not None else True


def set_pageindex_candidate_k(value: Optional[int]) -> None:
    """Set max candidates returned by lexical index per query."""
    _pageindex_candidate_k.set(value)


def get_pageindex_candidate_k() -> int:
    """Get candidate_k. Defaults to 200 when not set."""
    v = _pageindex_candidate_k.get()
    return v if v is not None else 200


def set_pageindex_max_docs_for_tree_search(value: Optional[int]) -> None:
    """Set max documents to include in tree search (replaces fixed constant)."""
    _pageindex_max_docs_for_tree_search.set(value)


def get_pageindex_max_docs_for_tree_search() -> int:
    """Get max_docs_for_tree_search. Defaults to 10 when not set."""
    v = _pageindex_max_docs_for_tree_search.get()
    return v if v is not None else 10


def _resolve_app_id(app_id: Optional[str]) -> Optional[str]:
    """Resolve app_id: JVAGENT_APP_ID overrides app.yaml/app node, else app node's app_id.

    Reads JVAGENT_APP_ID from .env via dotenv_values (not os.environ) so it works
    in child processes (e.g. uvicorn --reload) where load_dotenv may not have run.
    """
    env_val = None
    try:
        from dotenv import dotenv_values

        from jvagent.core.app_context import get_app_root

        root = get_app_root()
        for candidate in (os.path.join(root, ".env"), ".env"):
            if os.path.isfile(candidate):
                values = dotenv_values(candidate)
                env_val = values.get("JVAGENT_APP_ID") if values else None
                if env_val and str(env_val).strip():
                    return str(env_val).strip()
                break
    except Exception:
        pass
    env_val = os.getenv("JVAGENT_APP_ID")
    if env_val and str(env_val).strip():
        return str(env_val).strip()
    return app_id


def _get_pageindex_db_name(app_id: Optional[str] = None) -> str:
    """Resolve PageIndex db name: explicit env, or {app_id}_pageindex_db (one db per app), or default."""
    explicit = os.getenv("JVAGENT_PAGEINDEX_DB_NAME")
    if explicit and explicit.strip():
        return explicit.strip()

    resolved = _resolve_app_id(app_id)
    if resolved:
        sanitized = "".join(c for c in resolved if c.isalnum() or c == "_") or "app"
        return f"{sanitized}_pageindex_db"

    try:
        from jvagent.core.app_context import get_app_root
        from jvagent.core.config import load_app_config

        config = load_app_config(get_app_root())
        pageindex_cfg = config.get("pageindex") if isinstance(config, dict) else {}
        if isinstance(pageindex_cfg, dict):
            db_name = pageindex_cfg.get("db_name")
            if db_name and isinstance(db_name, str) and db_name.strip():
                return db_name.strip()
    except Exception:
        pass

    return "pageindex_db"


def _get_pageindex_db_path(app_id: Optional[str] = None) -> str:
    """Get PageIndex db path. Uses JVAGENT_PAGEINDEX_DB_PATH when set, else root + db_name."""
    explicit = os.getenv("JVAGENT_PAGEINDEX_DB_PATH")
    if explicit and explicit.strip():
        return explicit.strip()
    root = os.getenv("JVAGENT_PAGEINDEX_DB_ROOT", ".")
    root = root.strip() if root else "."
    return os.path.join(root, _get_pageindex_db_name(app_id))


def get_pageindex_config(app_id: Optional[str] = None) -> Dict[str, Any]:
    """Get PageIndex database configuration from environment variables and defaults.

    Args:
        app_id: Optional app ID for db name derivation when JVAGENT_PAGEINDEX_DB_NAME unset.
                One db per app; agents share the db, documents scoped by collection (agent_id).

    Returns:
        Dictionary with PageIndex database configuration

    Environment Variables:
        JVAGENT_APP_ID: Universal app identifier (overrides app node's app_id when set)
        JVAGENT_PAGEINDEX_DB_TYPE: Database type (json, sqlite, mongodb, dynamodb)
        JVAGENT_PAGEINDEX_DB_PATH: Path for file-based databases (json, sqlite).
        JVAGENT_PAGEINDEX_DB_ROOT: Root for path when DB_PATH not set (default: .)
        JVAGENT_PAGEINDEX_DB_NAME: Explicit db name (overrides autogeneration)
        JVAGENT_PAGEINDEX_DB_URI: Connection URI for MongoDB
        JVAGENT_PAGEINDEX_DB_TABLE_NAME: Table name for DynamoDB
        JVAGENT_PAGEINDEX_DB_REGION: AWS region for DynamoDB
    """
    db_type = os.getenv("JVAGENT_PAGEINDEX_DB_TYPE", "json")
    db_name = _get_pageindex_db_name(app_id)

    if db_type == "json":
        db_path = _get_pageindex_db_path(app_id)
        return {"db_type": db_type, "db_path": db_path}
    elif db_type == "sqlite":
        explicit = os.getenv("JVAGENT_PAGEINDEX_DB_PATH")
        if explicit and explicit.strip():
            db_path = explicit.strip()
        else:
            root = os.getenv("JVAGENT_PAGEINDEX_DB_ROOT", ".")
            root = root.strip() if root else "."
            db_path = os.path.join(root, db_name, "sqlite", "pageindex.db")
        return {"db_type": db_type, "db_path": db_path}
    elif db_type == "mongodb":
        db_uri = os.getenv("JVAGENT_PAGEINDEX_DB_URI", "mongodb://localhost:27017")
        return {"db_type": db_type, "db_uri": db_uri, "db_name": db_name}
    elif db_type == "dynamodb":
        table_name = os.getenv("JVAGENT_PAGEINDEX_DB_TABLE_NAME", db_name)
        region_name = os.getenv("JVAGENT_PAGEINDEX_DB_REGION", "us-east-1")
        endpoint_url = os.getenv("JVAGENT_PAGEINDEX_DB_ENDPOINT_URL")
        return {
            "db_type": db_type,
            "table_name": table_name,
            "region_name": region_name,
            "endpoint_url": endpoint_url,
        }
    else:
        return {"db_type": "json", "db_path": _get_pageindex_db_path(app_id)}


_db_init_lock = threading.Lock()
_db_initialized = False


def initialize_pageindex_database(
    config: Optional[Dict[str, Any]] = None,
    app_id: Optional[str] = None,
) -> bool:
    """Initialize and register the PageIndex graph database.

    One db per app; multiple agents share it. Documents are scoped by collection (agent_id).

    Args:
        config: Optional configuration dictionary. If not provided, reads from environment.
        app_id: Optional app ID for db name derivation when config is None.

    Returns:
        True if database was initialized, False otherwise
    """
    global _db_initialized
    if _db_initialized and config is None:
        return True

    with _db_init_lock:
        if _db_initialized and config is None:
            return True

        if config is None:
            config = get_pageindex_config(app_id=app_id)

        try:
            manager = get_database_manager()
            db_name = PAGEINDEX_DB_NAME

            try:
                manager.get_database(db_name)
                logger.debug(f"PageIndex database '{db_name}' already registered")
                _db_initialized = True
                return True
            except (ValueError, KeyError):
                pass

            db_type = config["db_type"]
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

            manager.register_database(db_name, pageindex_db)
            logger.info(
                f"PageIndex database initialized: type={db_type}, name={db_name}"
            )
            _db_initialized = True
            return True

        except Exception as e:
            logger.error(f"Failed to initialize PageIndex database: {e}", exc_info=True)
            return False


__all__ = [
    "get_pageindex_candidate_k",
    "get_pageindex_config",
    "get_pageindex_doc_description",
    "get_pageindex_enable_lexical_index",
    "get_pageindex_max_docs_for_tree_search",
    "get_pageindex_max_summary_chars",
    "get_pageindex_max_token_num_each_node",
    "get_pageindex_max_tree_prompt_tokens",
    "get_pageindex_node_summary",
    "get_pageindex_node_text",
    "get_pageindex_summary_token_threshold",
    "initialize_pageindex_database",
    "PAGEINDEX_DB_NAME",
    "set_pageindex_candidate_k",
    "set_pageindex_doc_description",
    "set_pageindex_enable_lexical_index",
    "set_pageindex_max_docs_for_tree_search",
    "set_pageindex_max_summary_chars",
    "set_pageindex_max_token_num_each_node",
    "set_pageindex_max_tree_prompt_tokens",
    "set_pageindex_node_summary",
    "set_pageindex_node_text",
    "set_pageindex_summary_token_threshold",
]
