"""Centralized environment variable loading for jvagent.

All ``JVAGENT_*`` and ``JVSPATIAL_*`` env vars used by jvagent core are documented here.
Log retention default uses ``JVSPATIAL_LOG_RETENTION_DEFAULT_DAYS`` (shared with jvspatial).
Database log levels use ``JVSPATIAL_DB_LOGGING_LEVELS`` (see ``jvagent.cli.server_config`` / jvspatial ``load_env``); do not use ``JVAGENT_DB_LOGGING_LEVELS``.
Process log verbosity uses ``JVSPATIAL_LOG_LEVEL`` (same as jvspatial Server / ``load_env().log_level``); do not use ``JVAGENT_LOG_LEVEL``.
JWT signing uses ``JVSPATIAL_JWT_SECRET_KEY`` only; do not use ``JVSPATIAL_JWT_SECRET``.
There is no implicit dev default for the JWT secret; set the env var when auth is enabled.
Modules should use load_env() instead of os.getenv.
"""

import os
from dataclasses import dataclass
from typing import Optional


def _normalize_empty(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _parse_env_bool(raw: Optional[str]) -> Optional[bool]:
    """Parse truthy/falsey tokens; None if empty or unrecognized (matches core.config.parse_env_bool)."""
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


def _parse_int(val: Optional[str]) -> Optional[int]:
    """Parse env string to int. Returns None if unset/invalid. Accepts 0 for indefinite retention."""
    if val is None or not str(val).strip():
        return None
    try:
        n = int(val)
        return n if n >= 0 else None
    except ValueError:
        return None


@dataclass
class EnvConfig:
    """Unified environment configuration for jvagent core.

    All JVAGENT_* and JVSPATIAL_* vars used by cli, core, and utils.
    log_retention_default_days is sourced from JVSPATIAL_LOG_RETENTION_DEFAULT_DAYS.
    log_level is sourced from JVSPATIAL_LOG_LEVEL.
    jwt_secret_key and jwt_secret both reflect ``JVSPATIAL_JWT_SECRET_KEY`` when set (else None).
    """

    # JVAGENT
    app_id: Optional[str]  # Overrides app node's app_id when set
    log_level: Optional[str]
    admin_username: str
    admin_password: Optional[str]
    admin_email: str
    log_db_path: Optional[str]
    log_db_uri: Optional[str]
    log_retention_default_days: Optional[int]
    # When True, skip ``pip install`` for action package.dependencies.pip (production / air-gapped)
    disable_runtime_pip_install: bool

    # JVSPATIAL (used by jvagent core)
    file_interface: str
    files_root_path: str
    mongodb_uri: str
    jwt_secret_key: Optional[str]
    jwt_secret: Optional[str]

    # S3 (for App file storage)
    s3_bucket_name: Optional[str]
    s3_region_name: str
    s3_access_key_id: Optional[str]
    s3_secret_access_key: Optional[str]
    s3_endpoint_url: Optional[str]


def get_jvagent_app_id() -> Optional[str]:
    """Return JVAGENT_APP_ID if set (from .env or os.environ), else None.

    Uses dotenv_values for .env so it works in child processes (e.g. uvicorn --reload)
    where load_dotenv may not have run.
    """
    try:
        from dotenv import dotenv_values

        from jvagent.core.app_context import get_app_root

        root = get_app_root()
        for candidate in (os.path.join(root, ".env"), ".env"):
            if os.path.isfile(candidate):
                values = dotenv_values(candidate)
                val = values.get("JVAGENT_APP_ID") if values else None
                if val and str(val).strip():
                    return str(val).strip()
                break
    except Exception:
        pass
    val = os.getenv("JVAGENT_APP_ID")
    return str(val).strip() if val and str(val).strip() else None


def load_env() -> EnvConfig:
    """Load all environment variables used by jvagent core into EnvConfig."""
    admin_username = os.getenv("JVAGENT_ADMIN_USERNAME", "admin")
    admin_email = (
        os.getenv("JVAGENT_ADMIN_EMAIL") or f"{admin_username}@jvagent.example"
    )
    _jwt = _normalize_empty(os.getenv("JVSPATIAL_JWT_SECRET_KEY"))
    _pip_raw = os.getenv("JVAGENT_DISABLE_RUNTIME_PIP_INSTALL")
    if _pip_raw is None or not str(_pip_raw).strip():
        disable_runtime_pip = False
    else:
        _pb = _parse_env_bool(_pip_raw)
        disable_runtime_pip = False if _pb is None else _pb
    _file_provider = _normalize_empty(os.getenv("JVSPATIAL_FILE_STORAGE_PROVIDER"))
    file_interface = _file_provider or "local"
    return EnvConfig(
        # JVAGENT
        app_id=os.getenv("JVAGENT_APP_ID") or None,
        log_level=os.getenv("JVSPATIAL_LOG_LEVEL"),
        admin_username=admin_username,
        admin_password=os.getenv("JVAGENT_ADMIN_PASSWORD"),
        admin_email=admin_email,
        log_db_path=os.getenv("JVSPATIAL_LOG_DB_PATH"),
        log_db_uri=os.getenv("JVSPATIAL_LOG_DB_URI"),
        log_retention_default_days=_parse_int(
            os.getenv("JVSPATIAL_LOG_RETENTION_DEFAULT_DAYS")
        ),
        disable_runtime_pip_install=disable_runtime_pip,
        # JVSPATIAL
        file_interface=file_interface,
        files_root_path=os.getenv("JVSPATIAL_FILES_ROOT_PATH", "./.files"),
        mongodb_uri=os.getenv("JVSPATIAL_MONGODB_URI", "mongodb://localhost:27017"),
        jwt_secret_key=_jwt,
        jwt_secret=_jwt,
        # S3 (canonical env names; see docs/configuration.md)
        s3_bucket_name=_normalize_empty(os.getenv("JVSPATIAL_S3_BUCKET_NAME")),
        s3_region_name=_normalize_empty(os.getenv("JVSPATIAL_S3_REGION"))
        or "us-east-1",
        s3_access_key_id=_normalize_empty(os.getenv("JVSPATIAL_S3_ACCESS_KEY")),
        s3_secret_access_key=_normalize_empty(os.getenv("JVSPATIAL_S3_SECRET_KEY")),
        s3_endpoint_url=_normalize_empty(os.getenv("JVSPATIAL_S3_ENDPOINT_URL")),
    )
