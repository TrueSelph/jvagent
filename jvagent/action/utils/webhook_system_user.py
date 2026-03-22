"""Create or resolve system users for webhook API key ownership."""

import logging
import os
import secrets
from typing import Optional

from jvspatial.api.auth.models import UserCreateAdmin
from jvspatial.api.auth.service import AuthenticationService

logger = logging.getLogger(__name__)


def _get_jwt_secret() -> Optional[str]:
    return os.environ.get("JVSPATIAL_JWT_SECRET_KEY") or os.environ.get(
        "JVSPATIAL_JWT_SECRET"
    )


async def get_or_create_system_user_for_webhook(
    system_user_email: str,
    webhook_permission: str,
) -> str:
    """Get or create a system service user for webhook API keys.

    Uses create_user_with_roles (admin-only path) since public registration
    is disabled when users exist. Admin must exist before webhook setup.
    """
    jwt_secret = _get_jwt_secret()
    if not jwt_secret:
        raise ValueError(
            "JWT secret must be set for webhook authentication. "
            "Set JVSPATIAL_JWT_SECRET_KEY in your environment."
        )
    auth_service = AuthenticationService(jwt_secret=jwt_secret)

    existing_user = await auth_service._find_user_by_email(system_user_email)
    if existing_user:
        return existing_user.id

    random_password = secrets.token_urlsafe(32)

    try:
        user_data = UserCreateAdmin(
            email=system_user_email,
            password=random_password,
            roles=["system"],
            permissions=[webhook_permission],
        )
        user_response = await auth_service.create_user_with_roles(user_data)
        logger.info("Created system service user: %s", user_response.id)
        return user_response.id
    except ValueError as e:
        if "already exists" in str(e).lower():
            existing_user = await auth_service._find_user_by_email(system_user_email)
            if existing_user:
                return existing_user.id
        raise
    except Exception as e:
        logger.error("Failed to create system service user: %s", e)
        raise
