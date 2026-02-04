"""Webhook authentication utilities for WhatsApp action.

This module provides utilities for managing system service users and API keys
for WhatsApp webhook authentication.
"""

import logging
import secrets
from typing import Optional

from jvspatial.api.auth.models import User, UserCreate
from jvspatial.api.auth.service import AuthenticationService
from jvspatial.core.context import GraphContext
from jvspatial.db import get_prime_database

logger = logging.getLogger(__name__)

# System service user email - not used for login, only for API key ownership
SYSTEM_USER_EMAIL = "whatsapp-service@system.internal"


async def get_or_create_system_user() -> str:
    """Get or create system service user for webhook API keys.

    This user is not used for login authentication, but serves as the owner
    of API keys generated for WhatsApp webhook endpoints.

    Returns:
        User ID of the system service user

    Raises:
        Exception: If user creation fails
    """
    # Use prime database for authentication operations
    prime_db = get_prime_database()
    context = GraphContext(database=prime_db)

    # Initialize authentication service
    auth_service = AuthenticationService(context=context)

    # Try to find existing system user
    existing_user = await auth_service._find_user_by_email(SYSTEM_USER_EMAIL)

    if existing_user:
        return existing_user.id

    # Create new system user
    # Generate a secure random password (not used for login, but required by User model)
    random_password = secrets.token_urlsafe(32)

    try:
        user_data = UserCreate(
            email=SYSTEM_USER_EMAIL,
            password=random_password,
        )
        user_response = await auth_service.register_user(user_data)
        # logger.info(f"Created system service user: {user_response.id}")
        return user_response.id
    except ValueError as e:
        # User might have been created between check and creation
        if "already exists" in str(e).lower():
            # Try to find again
            existing_user = await auth_service._find_user_by_email(SYSTEM_USER_EMAIL)
            if existing_user:
                return existing_user.id
        raise
    except Exception as e:
        logger.error(f"Failed to create system service user: {e}")
        raise


__all__ = ["get_or_create_system_user", "SYSTEM_USER_EMAIL"]
