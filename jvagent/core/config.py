"""Centralized configuration loading and path resolution for jvagent.

Single source for app.yaml loading, config value resolution (env > config > default),
empty string normalization, and database path resolution.

Declarative ``ConfigSchema`` / ``ConfigKey`` pattern for subsystem configuration
replaces ad-hoc resolver functions with a consistent, self-documenting interface.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, Generic, Literal, Optional, TypeVar

from jvagent.core.app_yaml_validator import warn_app_yaml_config
from jvagent.core.env_resolver import resolve_env_placeholders

logger = logging.getLogger(__name__)

EnvironmentMode = Literal["development", "production"]
T = TypeVar("T")


def get_environment_mode() -> EnvironmentMode:
    """Get the current environment mode.

    Configuration priority:
    1. JVSPATIAL_ENVIRONMENT env var
    2. Default: development

    Returns:
        'production' if configured as production (case-insensitive),
        'development' otherwise
    """
    raw = os.getenv("JVSPATIAL_ENVIRONMENT")
    if raw is not None and str(raw).strip():
        mode = str(raw).strip().lower()
        return "production" if mode == "production" else "development"

    return "development"


def is_production_mode() -> bool:
    """Check if running in production mode."""
    return get_environment_mode() == "production"


# =============================================================================
# Declarative Config Schema
# =============================================================================


@dataclass
class ConfigKey(Generic[T]):
    """A single configuration key with env > config > default precedence.

    Type coercion is applied based on *type_hint* or inferred from *default*.
    An optional *coerce* callable overrides automatic coercion.

    Example::

        ConfigKey("pageindex.db_type", env="JVAGENT_PAGEINDEX_DB_TYPE", default="json")
        ConfigKey("server.port", env="JVAGENT_PORT", default=8080)
    """

    path: str
    env: Optional[str] = None
    default: T = None  # type: ignore[assignment]
    doc: str = ""
    coerce: Optional[Callable[[Any], T]] = None

    def resolve(self, app_config: dict, *, _app_root: Optional[str] = None) -> T:
        """Resolve this key against *app_config*."""
        # 1. Environment variable
        if self.env:
            raw = os.getenv(self.env)
            if raw is not None and str(raw).strip() != "":
                return self._coerce_env(str(raw).strip())
        # 2. Config path
        if app_config:
            current: Any = app_config
            for key in self.path.split("."):
                if isinstance(current, dict) and key in current:
                    current = current[key]
                else:
                    current = None
                    break
            if current is not None:
                if (
                    isinstance(current, str)
                    and not current.strip()
                    and self.default is None
                ):
                    return None  # type: ignore[return-value]
                return self._coerce_value(current)
        # 3. Default
        return self.default

    def _coerce_env(self, raw: str) -> T:
        """Coerce an env var string to the target type."""
        if self.coerce:
            return self.coerce(raw)
        if isinstance(self.default, bool):
            pb = parse_env_bool(raw)
            return self.default if pb is None else pb  # type: ignore[return-value]
        if isinstance(self.default, int):
            try:
                return int(raw)  # type: ignore[return-value]
            except ValueError:
                return self.default  # type: ignore[return-value]
        if isinstance(self.default, float):
            try:
                return float(raw)  # type: ignore[return-value]
            except ValueError:
                return self.default  # type: ignore[return-value]
        pb = parse_env_bool(raw)
        if pb is not None:
            return pb  # type: ignore[return-value]
        return raw  # type: ignore[return-value]

    def _coerce_value(self, value: Any) -> T:
        """Coerce a YAML value to the target type."""
        if self.coerce:
            return self.coerce(value)
        if isinstance(self.default, bool):
            if isinstance(value, bool):
                return value  # type: ignore[return-value]
            if isinstance(value, str):
                pb = parse_env_bool(value)
                return self.default if pb is None else pb  # type: ignore[return-value]
            return bool(value)  # type: ignore[return-value]
        if isinstance(self.default, (int, float)):
            try:
                return type(self.default)(value)  # type: ignore[return-value]
            except (TypeError, ValueError):
                return self.default  # type: ignore[return-value]
        return value  # type: ignore[return-value]


class ConfigSchema:
    """Declarative configuration schema for a subsystem.

    Define keys as class-level ``ConfigKey`` instances and call ``resolve()``
    to produce a namespace with all values resolved.

    Example::

        class PageIndexConfig(ConfigSchema):
            db_type = ConfigKey("pageindex.db_type", env="JVAGENT_PAGEINDEX_DB_TYPE", default="json")
            db_name = ConfigKey("pageindex.db_name")

        cfg = PageIndexConfig.resolve(app_config)
        print(cfg.db_type)  # "json"
    """

    _keys: ClassVar[Dict[str, "ConfigKey[Any]"]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls._keys = {k: v for k, v in vars(cls).items() if isinstance(v, ConfigKey)}

    @classmethod
    def resolve(cls, app_config: dict, *, app_root: Optional[str] = None) -> Any:
        """Resolve all keys against *app_config* and return a namespace."""
        ns: dict[str, Any] = {}
        for name, key in cls._keys.items():
            ns[name] = key.resolve(app_config, _app_root=app_root)
        return type(f"{cls.__name__}Resolved", (), ns)()


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


def parse_env_bool(raw: Optional[str]) -> Optional[bool]:
    """Parse common truthy/falsey env tokens. None if empty or unrecognized."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    return None


def _strict_app_config_load() -> bool:
    """When True, ``load_app_config`` re-raises after logging (fail-fast)."""
    return parse_env_bool(os.getenv("JVAGENT_STRICT_CONFIG")) is True


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
    app_yaml_path = Path(app_root) / "app.yaml"
    try:
        if app_yaml_path.exists():
            import yaml

            with open(app_yaml_path, "r", encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f)
                if yaml_data and "config" in yaml_data:
                    app_config = resolve_env_placeholders(yaml_data.get("config", {}))
                    if isinstance(app_config, dict):
                        warn_app_yaml_config(
                            app_config, source=f"{app_yaml_path}:config"
                        )
    except Exception as e:
        logger.warning(
            "Could not load app.yaml config from %s: %s",
            app_yaml_path,
            e,
            exc_info=True,
        )
        if _strict_app_config_load():
            raise

    return app_config


def get_config_value(
    config: dict,
    path: str,
    env_var: Optional[str] = None,
    default: Any = None,
) -> Any:
    """Get configuration value from nested dict path with environment variable fallback.

    Priority: env var > config path > default.
    Empty env values are treated as unset (fall through to yaml/default).
    Bool coercion: ``true``/``1``/``yes``/``on`` and ``false``/``0``/``no``/``off``.

    Args:
        config: Configuration dictionary (from app.yaml)
        path: Dot-separated path to config value (e.g., "server.host")
        env_var: Environment variable name (takes precedence)
        default: Default value if not found

    Returns:
        Configuration value
    """
    if env_var:
        raw_env = os.getenv(env_var)
        if raw_env is not None:
            s = str(raw_env).strip()
            if s != "":
                if isinstance(default, bool):
                    pb = parse_env_bool(s)
                    return default if pb is None else pb
                if isinstance(default, int):
                    try:
                        return int(s)
                    except ValueError:
                        return default
                if isinstance(default, float):
                    try:
                        return float(s)
                    except ValueError:
                        return default
                pb = parse_env_bool(s)
                if pb is not None:
                    return pb
                return s

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

    Priority: env ``JVSPATIAL_DB_PATH`` > ``config.database.path`` > default.
    Relative paths are resolved against app_root.

    Args:
        app_root: Path to app root directory
        config: App config dict from load_app_config
        db_type: Database type (json, mongodb, dynamodb)

    Returns:
        Resolved absolute database path for json; for other types returns the path/uri as-is
    """
    db_path = normalize_empty(os.getenv("JVSPATIAL_DB_PATH"))
    if not db_path:
        db_path = normalize_empty(
            get_config_value(
                config, "database.path", "JVSPATIAL_DB_PATH", "./jvagent_db"
            )
        )
    db_path = db_path or "./jvagent_db"

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
    log_db_path = normalize_empty(
        get_config_value(
            config, "logging.database.path", "JVSPATIAL_LOG_DB_PATH", "./jvagent_logs"
        )
    )
    if not log_db_path:
        return None

    app_root_path = Path(app_root).resolve()
    log_path_obj = Path(log_db_path)
    if not log_path_obj.is_absolute():
        return str(app_root_path / log_db_path)
    return log_db_path


def load_app_yaml_app_id(app_root: str) -> Optional[str]:
    """Read top-level ``app:`` identifier from app.yaml (same as AppDescriptor.app_id)."""
    try:
        app_yaml_path = Path(app_root) / "app.yaml"
        if not app_yaml_path.exists():
            return None
        import yaml

        with open(app_yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not data:
            return None
        data = resolve_env_placeholders(data)
        app = data.get("app")
        if isinstance(app, str) and app.strip():
            return app.strip()
        return None
    except Exception as e:
        logger.debug("Could not read app id from app.yaml: %s", e)
        return None


def _resolve_path_under_app_root(app_root: str, path_str: str) -> str:
    """Resolve path_str relative to app_root when not absolute."""
    app_root_path = Path(app_root).resolve()
    p = Path(path_str)
    if p.is_absolute():
        return str(p)
    return str(app_root_path / path_str)


class PageIndexConfig(ConfigSchema):
    """PageIndex subsystem configuration."""

    db_type = ConfigKey[str](
        "pageindex.db_type",
        env="JVAGENT_PAGEINDEX_DB_TYPE",
        default="json",
        doc="PageIndex database type (json, sqlite, mongodb, dynamodb)",
    )
    db_name = ConfigKey[str](
        "pageindex.db_name",
        env="JVAGENT_PAGEINDEX_DB_NAME",
        default="pageindex_db",
        doc="PageIndex database name",
    )
    db_root = ConfigKey[str](
        "pageindex.db_root",
        env="JVAGENT_PAGEINDEX_DB_ROOT",
        default=".",
        doc="PageIndex database root directory",
    )


# YAML-only fallback for db_name (explicit env and app-id naming handled in resolver).
_PAGEINDEX_DB_NAME_YAML = ConfigKey[str](
    "pageindex.db_name",
    env=None,
    default="pageindex_db",
    doc="pageindex.db_name from yaml only",
)


class LoggingDatabaseConfig(ConfigSchema):
    """logging.database.* keys."""

    database_type = ConfigKey[str](
        "logging.database.type",
        env="JVSPATIAL_LOG_DB_TYPE",
        default=None,
        doc="Optional override for log DB type",
    )


class AppDatabaseConfig(ConfigSchema):
    """Main application database.type."""

    database_type = ConfigKey[str](
        "database.type",
        env="JVSPATIAL_DB_TYPE",
        default="json",
        doc="Primary graph database type",
    )


def resolve_pageindex_db_name(app_root: str, app_config: dict) -> str:
    """Resolve PageIndex database name.

    Explicit env var takes precedence. Falls back to app-id-based naming,
    then yaml config, then default.
    """
    explicit = normalize_empty(os.getenv("JVAGENT_PAGEINDEX_DB_NAME"))
    if explicit:
        return explicit

    app_id = normalize_empty(os.getenv("JVAGENT_APP_ID")) or load_app_yaml_app_id(
        app_root
    )
    if app_id:
        sanitized = "".join(c for c in app_id if c.isalnum() or c == "_") or "app"
        return f"{sanitized}_pageindex_db"

    return _PAGEINDEX_DB_NAME_YAML.resolve(app_config)


def resolve_pageindex_purge_path(app_root: str, app_config: dict) -> Optional[str]:
    """Absolute filesystem path to purge for PageIndex when using json or sqlite.

    Returns None for mongodb, dynamodb, or unknown types (no local tree to remove).
    """
    cfg = PageIndexConfig.resolve(app_config)
    if cfg.db_type not in ("json", "sqlite"):
        return None

    explicit = normalize_empty(os.getenv("JVAGENT_PAGEINDEX_DB_PATH"))
    if explicit:
        return _resolve_path_under_app_root(app_root, explicit)

    db_name = resolve_pageindex_db_name(app_root, app_config)
    root_resolved = _resolve_path_under_app_root(app_root, cfg.db_root)

    if cfg.db_type == "json":
        return str(Path(root_resolved) / db_name)
    return str(Path(root_resolved) / db_name / "sqlite" / "pageindex.db")


def effective_log_db_type(app_config: dict) -> str:
    """Effective logging DB type: explicit log type, else main app database.type."""
    log_cfg = LoggingDatabaseConfig.resolve(app_config)
    log_t = normalize_empty(log_cfg.database_type)
    if log_t:
        return log_t
    main_cfg = AppDatabaseConfig.resolve(app_config)
    return normalize_empty(main_cfg.database_type) or "json"


class FileStorageConfig(ConfigSchema):
    """File storage subsystem configuration."""

    provider = ConfigKey[str](
        "file_storage.provider",
        env="JVSPATIAL_FILE_STORAGE_PROVIDER",
        default="local",
        doc="Storage backend provider (local, s3, gcs)",
    )
    root_dir = ConfigKey[str](
        "file_storage.root_dir",
        env="JVSPATIAL_FILES_ROOT_PATH",
        default="./.files",
        doc="Root directory for local file storage",
    )
    enabled = ConfigKey[bool](
        "file_storage.enabled",
        env="JVSPATIAL_FILE_STORAGE_ENABLED",
        default=False,
        doc="Whether file storage is enabled",
    )
    base_url = ConfigKey[str](
        "file_storage.base_url",
        env="JVSPATIAL_FILE_STORAGE_BASE_URL",
        default="http://localhost:8000",
        doc="Base URL for file access",
    )
    max_size = ConfigKey[int](
        "file_storage.max_size",
        env="JVSPATIAL_FILE_STORAGE_MAX_SIZE",
        default=100 * 1024 * 1024,
        doc="Maximum file size in bytes",
    )


def get_file_storage_config(app_root: str, config: dict) -> dict:
    """Get file storage configuration with unified fallback precedence.

    Delegates to :class:`FileStorageConfig` schema.

    Args:
        app_root: Path to app root directory (unused but kept for API consistency)
        config: App config dict from load_app_config

    Returns:
        Dict with provider, root_dir, enabled, base_url, max_size
    """
    cfg = FileStorageConfig.resolve(config)
    return {
        "provider": cfg.provider or "local",
        "root_dir": cfg.root_dir or "./.files",
        "enabled": cfg.enabled,
        "base_url": cfg.base_url,
        "max_size": cfg.max_size,
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
    if env_value is not None and str(env_value).strip() != "":
        if config_type == bool:
            pb = parse_env_bool(env_value)
            return default if pb is None else pb
        if config_type == int:
            try:
                return int(str(env_value).strip())
            except ValueError:
                return default
        if config_type == float:
            try:
                return float(str(env_value).strip())
            except ValueError:
                return default
        return str(env_value).strip()

    if raw is not None:
        if config_type == bool:
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, str):
                pb = parse_env_bool(raw)
                return default if pb is None else pb
            return bool(raw)
        if config_type == int:
            try:
                return int(raw)
            except (TypeError, ValueError):
                return default
        if config_type == float:
            try:
                return float(raw)
            except (TypeError, ValueError):
                return default
        try:
            return config_type(raw)
        except Exception:
            return default

    return default
