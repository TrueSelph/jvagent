"""Centralized configuration loading and path resolution for jvagent.

Single source for app.yaml loading, config value resolution (env > config > default),
empty string normalization, and database path resolution.
"""

import logging
import os
from pathlib import Path
from typing import Any, Literal, Optional

from jvagent.core.env_resolver import resolve_env_placeholders

logger = logging.getLogger(__name__)

EnvironmentMode = Literal["development", "production"]


def _get_environment_from_app_config() -> Optional[str]:
    """Read environment mode from app.yaml.

    Checks config.environment first, then config.development.environment (legacy).

    Returns:
        'production' or 'development' if found in config, None otherwise
    """
    try:
        from jvagent.core.app_context import get_app_root
        from jvagent.core.app_loader import AppLoader

        loader = AppLoader(get_app_root())
        descriptor = loader.load_app_descriptor()
        if descriptor and descriptor.config:
            config = descriptor.config
            val = config.get("environment")
            if isinstance(val, str):
                return val.lower()
            dev_config = config.get("development", {})
            if isinstance(dev_config, dict) and "environment" in dev_config:
                val = dev_config["environment"]
                if isinstance(val, str):
                    return val.lower()
    except Exception:
        pass
    return None


def get_environment_mode() -> EnvironmentMode:
    """Get the current environment mode.

    Configuration priority:
    1. JVAGENT_ENVIRONMENT env var (highest)
    2. app.yaml config.environment (or config.development.environment legacy)
    3. Default: development

    Returns:
        'production' if configured as production (case-insensitive),
        'development' otherwise
    """
    from jvspatial.env import get_environment_mode as _get_mode

    return _get_mode(_get_environment_from_app_config)


def is_development_mode() -> bool:
    """Check if running in development mode."""
    return get_environment_mode() == "development"


def is_production_mode() -> bool:
    """Check if running in production mode."""
    return get_environment_mode() == "production"


def normalize_empty(value: Optional[str]) -> Optional[str]:
    """Normalize empty or whitespace-only strings to None.

    Args:
        value: String value to normalize

    Returns:
        None if value is None, empty, or whitespace-only; otherwise the stripped value
    """
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def load_app_config(app_root: Optional[str] = None) -> dict:
    """Load app.yaml config section with environment variable resolution.

    Args:
        app_root: Path to app root directory. If None, uses current working directory.

    Returns:
        Resolved config dict from app.yaml, or empty dict if not found/invalid
    """
    if app_root is None:
        app_root = os.getcwd()

    app_config: dict = {}
    try:
        app_yaml_path = Path(app_root) / "app.yaml"
        if app_yaml_path.exists():
            import yaml

            with open(app_yaml_path, "r", encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f)
                if yaml_data and "config" in yaml_data:
                    app_config = resolve_env_placeholders(yaml_data.get("config", {}))
    except Exception as e:
        logger.debug("Could not load app.yaml config: %s", e)

    return app_config


def get_config_value(
    config: dict,
    path: str,
    env_var: Optional[str] = None,
    default: Any = None,
) -> Any:
    """Get configuration value from nested dict path with environment variable fallback.

    Priority: env var > config path > default.
    For string values, empty strings from unresolved placeholders are normalized to None
    when the default is not a string (e.g. None). Bool/int coercion applied for env vars.

    Args:
        config: Configuration dictionary (from app.yaml)
        path: Dot-separated path to config value (e.g., "server.host")
        env_var: Environment variable name (takes precedence)
        default: Default value if not found

    Returns:
        Configuration value
    """
    if env_var and os.getenv(env_var) is not None:
        value = os.getenv(env_var)
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        if isinstance(default, int) and isinstance(value, str) and value.isdigit():
            return int(value)
        if isinstance(default, float) and isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                pass
        return value

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
            if isinstance(current, str) and not current.strip() and default is None:
                return None
            return current

    return default


def resolve_db_path(
    app_root: str,
    config: dict,
    db_type: str = "json",
) -> str:
    """Resolve database path from config and environment.

    Priority: env (JVSPATIAL_DB_PATH or JVSPATIAL_JSONDB_PATH) > config database.path > default.
    Relative paths are resolved against app_root.

    Args:
        app_root: Path to app root directory
        config: App config dict from load_app_config
        db_type: Database type (json, mongodb, dynamodb)

    Returns:
        Resolved absolute database path for json; for other types returns the path/uri as-is
    """
    db_path = os.getenv("JVSPATIAL_DB_PATH") or get_config_value(
        config, "database.path", "JVSPATIAL_DB_PATH", "./jvagent_db"
    )
    db_path = normalize_empty(db_path) or "./jvagent_db"

    app_root_path = Path(app_root).resolve()
    db_path_obj = Path(db_path)
    if not db_path_obj.is_absolute():
        db_path = str(app_root_path / db_path)

    return db_path


def resolve_log_db_path(app_root: str, config: dict) -> Optional[str]:
    """Resolve logging database path from config and environment.

    Args:
        app_root: Path to app root directory
        config: App config dict from load_app_config

    Returns:
        Resolved path or None if not configured
    """
    log_db_path = os.getenv("JVAGENT_LOG_DB_PATH") or get_config_value(
        config, "logging.database.path", None, "./jvagent_logs"
    )
    log_db_path = normalize_empty(log_db_path)
    if not log_db_path:
        return None

    app_root_path = Path(app_root).resolve()
    log_path_obj = Path(log_db_path)
    if not log_path_obj.is_absolute():
        return str(app_root_path / log_db_path)
    return log_db_path


def get_file_storage_config(app_root: str, config: dict) -> dict:
    """Get file storage configuration with unified fallback precedence.

    Precedence for provider: JVSPATIAL_FILE_INTERFACE > JVSPATIAL_FILE_STORAGE_PROVIDER >
    config file_storage.provider > default "local".
    Precedence for root_dir: JVSPATIAL_FILES_ROOT_PATH > JVSPATIAL_FILE_STORAGE_ROOT >
    config file_storage.root_dir > default ".files".

    Args:
        app_root: Path to app root directory (unused but kept for API consistency)
        config: App config dict from load_app_config

    Returns:
        Dict with provider, root_dir, enabled, base_url, max_size
    """
    provider = (
        normalize_empty(os.getenv("JVSPATIAL_FILE_INTERFACE"))
        or normalize_empty(os.getenv("JVSPATIAL_FILE_STORAGE_PROVIDER"))
        or get_config_value(
            config, "file_storage.provider", "JVSPATIAL_FILE_INTERFACE", "local"
        )
        or "local"
    )
    root_dir = (
        normalize_empty(os.getenv("JVSPATIAL_FILES_ROOT_PATH"))
        or normalize_empty(os.getenv("JVSPATIAL_FILE_STORAGE_ROOT"))
        or get_config_value(
            config, "file_storage.root_dir", "JVSPATIAL_FILES_ROOT_PATH", ".files"
        )
        or ".files"
    )
    return {
        "provider": provider if provider else "local",
        "root_dir": root_dir if root_dir else ".files",
        "enabled": get_config_value(
            config, "file_storage.enabled", "JVSPATIAL_FILE_STORAGE_ENABLED", False
        ),
        "base_url": get_config_value(
            config,
            "file_storage.base_url",
            "JVSPATIAL_FILE_STORAGE_BASE_URL",
            "http://localhost:8000",
        ),
        "max_size": get_config_value(
            config,
            "file_storage.max_size",
            "JVSPATIAL_FILE_STORAGE_MAX_SIZE",
            100 * 1024 * 1024,
        ),
    }


def get_performance_config_value(
    config: dict,
    key: str,
    env_var: str,
    default: Any,
    config_type: type = str,
) -> Any:
    """Get performance config value with type coercion.

    Used by cache.py for config.performance section. Keys are flat (e.g. enable_agent_cache).

    Args:
        config: Full app config (or config.performance subsection)
        key: Flat key in performance config
        env_var: Environment variable name
        default: Default value
        config_type: Type to coerce to (bool, int, float, str)

    Returns:
        Coerced value
    """
    perf = config.get("performance", {}) if isinstance(config, dict) else {}
    if isinstance(perf, dict) and key in perf:
        raw = perf[key]
    else:
        raw = None

    env_value = os.getenv(env_var)
    if env_value is not None:
        if config_type == bool:
            return env_value.lower() == "true"
        if config_type == int:
            return int(env_value)
        if config_type == float:
            return float(env_value)
        return env_value

    if raw is not None:
        return raw

    return default
