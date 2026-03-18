"""Centralized environment variable loading for jvagent.

All JVAGENT_* and JVSPATIAL_* env vars used by jvagent core are documented here.
Modules should use load_env() instead of os.getenv.
"""

import os
from dataclasses import dataclass
from typing import Optional


def _parse_bool(val: str) -> bool:
    return str(val).strip().lower() in ("true", "1", "yes")


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
    """

    # JVAGENT
    log_level: Optional[str]
    admin_username: str
    admin_password: Optional[str]
    admin_email: str
    log_db_path: Optional[str]
    log_db_uri: Optional[str]
    log_retention_default_days: Optional[int]

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


def load_env() -> EnvConfig:
    """Load all environment variables used by jvagent core into EnvConfig."""
    admin_username = os.getenv("JVAGENT_ADMIN_USERNAME", "admin")
    admin_email = (
        os.getenv("JVAGENT_ADMIN_EMAIL") or f"{admin_username}@jvagent.example"
    )
    jwt_secret = os.getenv("JVSPATIAL_JWT_SECRET_KEY") or os.getenv(
        "JVSPATIAL_JWT_SECRET", "jvagent-secret-key-change-in-production"
    )
    return EnvConfig(
        # JVAGENT
        log_level=os.getenv("JVAGENT_LOG_LEVEL"),
        admin_username=admin_username,
        admin_password=os.getenv("JVAGENT_ADMIN_PASSWORD"),
        admin_email=admin_email,
        log_db_path=os.getenv("JVAGENT_LOG_DB_PATH"),
        log_db_uri=os.getenv("JVAGENT_LOG_DB_URI"),
        log_retention_default_days=_parse_int(
            os.getenv("JVAGENT_LOG_RETENTION_DEFAULT_DAYS")
        ),
        # JVSPATIAL
        file_interface=os.getenv("JVSPATIAL_FILE_INTERFACE", "local"),
        files_root_path=os.getenv("JVSPATIAL_FILES_ROOT_PATH", ".files"),
        mongodb_uri=os.getenv("JVSPATIAL_MONGODB_URI", "mongodb://localhost:27017"),
        jwt_secret_key=os.getenv("JVSPATIAL_JWT_SECRET_KEY"),
        jwt_secret=jwt_secret,
        # S3
        s3_bucket_name=os.getenv("JVSPATIAL_S3_BUCKET_NAME"),
        s3_region_name=os.getenv("JVSPATIAL_S3_REGION_NAME", "us-east-1"),
        s3_access_key_id=os.getenv("JVSPATIAL_S3_ACCESS_KEY_ID"),
        s3_secret_access_key=os.getenv("JVSPATIAL_S3_SECRET_ACCESS_KEY"),
        s3_endpoint_url=os.getenv("JVSPATIAL_S3_ENDPOINT_URL"),
    )
