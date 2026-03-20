"""Webhook authentication utilities for PageIndexGoogleDriveSync action.

This module provides utilities for managing system service users and API keys
for PageIndexGoogleDriveSync webhook authentication.
"""

import logging
import os
import secrets
from typing import Optional

from jvspatial.api.auth.models import UserCreateAdmin
from jvspatial.api.auth.service import AuthenticationService

logger = logging.getLogger(__name__)

# System service user email - not used for login, only for API key ownership
SYSTEM_USER_EMAIL = "pageindex-google-drive-sync-service@system.internal"


def _get_jwt_secret() -> Optional[str]:
    """Get JWT secret from environment. Supports JVSPATIAL_JWT_SECRET_KEY and JVSPATIAL_JWT_SECRET."""
    return os.environ.get("JVSPATIAL_JWT_SECRET_KEY") or os.environ.get(
        "JVSPATIAL_JWT_SECRET"
    )


async def get_or_create_system_user() -> str:
    """Get or create system service user for webhook API keys.

    Uses create_user_with_roles (admin-only path) since public registration
    is disabled when users exist. Admin must exist before webhook setup.

    Returns:
        User ID of the system service user

    Raises:
        Exception: If user creation fails
    """
    jwt_secret = _get_jwt_secret()
    if not jwt_secret:
        raise ValueError(
            "JWT secret must be set for webhook authentication. "
            "Set JVSPATIAL_JWT_SECRET_KEY in your environment."
        )
    auth_service = AuthenticationService(jwt_secret=jwt_secret)

    existing_user = await auth_service._find_user_by_email(SYSTEM_USER_EMAIL)
    if existing_user:
        return existing_user.id

    random_password = secrets.token_urlsafe(32)

    try:
        user_data = UserCreateAdmin(
            email=SYSTEM_USER_EMAIL,
            password=random_password,
            roles=["system"],
            permissions=["webhook:pageindex_google_drive_sync"],
        )
        user_response = await auth_service.create_user_with_roles(user_data)
        logger.info(f"Created system service user: {user_response.id}")
        return user_response.id
    except ValueError as e:
        if "already exists" in str(e).lower():
            existing_user = await auth_service._find_user_by_email(SYSTEM_USER_EMAIL)
            if existing_user:
                return existing_user.id
        raise
    except Exception as e:
        logger.error(f"Failed to create system service user: {e}")
        raise


__all__ = ["get_or_create_system_user", "SYSTEM_USER_EMAIL"]
