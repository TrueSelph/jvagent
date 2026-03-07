"""Webhook authentication utilities for WhatsApp action.

This module provides utilities for managing system service users and API keys
for WhatsApp webhook authentication.
"""

import logging
import secrets
from typing import Optional

from jvspatial.api.auth.models import UserCreateAdmin
from jvspatial.api.auth.service import AuthenticationService

logger = logging.getLogger(__name__)

# System service user email - not used for login, only for API key ownership
SYSTEM_USER_EMAIL = "whatsapp-service@system.internal"


async def get_or_create_system_user() -> str:
    """Get or create system service user for webhook API keys.

    Uses create_user_with_roles (admin-only path) since public registration
    is disabled when users exist. Admin must exist before webhook setup.

    Returns:
        User ID of the system service user

    Raises:
        Exception: If user creation fails
    """
    auth_service = AuthenticationService()

    existing_user = await auth_service._find_user_by_email(SYSTEM_USER_EMAIL)
    if existing_user:
        return existing_user.id

    random_password = secrets.token_urlsafe(32)

    try:
        user_data = UserCreateAdmin(
            email=SYSTEM_USER_EMAIL,
            password=random_password,
            roles=["system"],
            permissions=["webhook:whatsapp"],
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
