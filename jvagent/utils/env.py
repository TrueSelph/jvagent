"""Environment configuration utilities."""

import os
from typing import Literal, Optional

EnvironmentMode = Literal["development", "production"]


def _get_environment_from_app_config() -> Optional[str]:
    """Read environment mode from app.yaml config.development.environment.
    
    Returns:
        'production' or 'development' if found in config, None otherwise
    """
    try:
        from jvagent.core.app_context import get_app_root
        from jvagent.core.app_loader import AppLoader

        loader = AppLoader(get_app_root())
        descriptor = loader.load_app_descriptor()
        if descriptor and descriptor.config:
            dev_config = descriptor.config.get("development", {})
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
    2. app.yaml config.development.environment
    3. Default: development
    
    Returns:
        'production' if configured as production (case-insensitive),
        'development' otherwise
    """
    # Environment variable takes priority
    env_value = os.getenv("JVAGENT_ENVIRONMENT")
    if env_value is not None:
        mode = env_value.lower()
        return "production" if mode == "production" else "development"

    # Fallback to app config
    config_value = _get_environment_from_app_config()
    if config_value is not None:
        return "production" if config_value == "production" else "development"

    return "development"


def is_development_mode() -> bool:
    """Check if running in development mode."""
    return get_environment_mode() == "development"


def is_production_mode() -> bool:
    """Check if running in production mode."""
    return get_environment_mode() == "production"

