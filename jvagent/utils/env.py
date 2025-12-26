"""Environment configuration utilities."""

import os
from typing import Literal

EnvironmentMode = Literal["development", "production"]


def get_environment_mode() -> EnvironmentMode:
    """Get the current environment mode from JVAGENT_ENVIRONMENT.
    
    Returns:
        'production' if JVAGENT_ENVIRONMENT is set to 'production' (case-insensitive),
        'development' otherwise (default)
    """
    mode = os.getenv("JVAGENT_ENVIRONMENT", "development").lower()
    return "production" if mode == "production" else "development"


def is_development_mode() -> bool:
    """Check if running in development mode."""
    return get_environment_mode() == "development"


def is_production_mode() -> bool:
    """Check if running in production mode."""
    return get_environment_mode() == "production"

