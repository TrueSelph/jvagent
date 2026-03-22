"""Bootstrap logic for jvagent application graph."""

import logging
import os
from typing import Optional

from jvspatial.api import get_auth_service
from jvspatial.api.auth.models import UserCreateAdmin
from jvspatial.core import Root

from jvagent import __version__
from jvagent.core import Agents
from jvagent.core.app import App
from jvagent.core.app_loader import AppLoader
from jvagent.core.bootstrap_logger import BootstrapLogger
from jvagent.core.config import get_file_storage_config, load_app_config
from jvagent.env import get_jvagent_app_id, load_env

logger = logging.getLogger(__name__)


async def bootstrap_application_graph(
    update_mode: Optional[str] = None, app_root: str = None
) -> None:
    """Bootstrap the application graph with App and Agents nodes.

    If an app.yaml file is found in the app root directory, uses AppLoader to
    bootstrap the application declaratively, including:
    - Creating/updating the App node from app.yaml
    - Installing all agents listed in app.yaml
    - Loading and registering all actions for each agent from agent.yaml files

    Otherwise, falls back to manual bootstrap with basic configuration.

    Args:
        update_mode: Update strategy - "merge" for non-destructive merge, "source" for
                     destructive overwrite from YAML, or None to skip existing.
        app_root: Path to the app root directory. If None, uses current working directory.

    All operations are idempotent - existing nodes and connections are preserved.
    """
    if app_root is None:
        app_root = os.getcwd()

    bootstrap_log = BootstrapLogger("Bootstrap")

    app_yaml_path = os.path.join(app_root, "app.yaml")

    if os.path.exists(app_yaml_path):
        mode = update_mode if update_mode else "sync"
        bootstrap_log.start(f"Application graph ({mode} mode)")

        app_loader = AppLoader(app_root)
        app = await app_loader.bootstrap_application(update_mode=update_mode)

        if app:
            bootstrap_log.complete("Application graph ready")
        else:
            bootstrap_log.error(
                "Declarative bootstrap failed - falling back to manual bootstrap"
            )
            await _manual_bootstrap(app_root)
    else:
        bootstrap_log.start("Application graph (manual mode, no app.yaml)")
        bootstrap_log.info("No app.yaml found - using manual bootstrap")
        await _manual_bootstrap(app_root)
        bootstrap_log.complete("Manual bootstrap complete")

    # Apply JVAGENT_APP_ID override to App node if set
    app = await App.get()
    if app:
        env_app_id = get_jvagent_app_id()
        if env_app_id:
            app.app_id = env_app_id
            await app.save()


async def _manual_bootstrap(app_root: Optional[str] = None) -> None:
    """Manual bootstrap when no app.yaml is available."""
    if app_root is None:
        app_root = os.getcwd()
    root = await Root.get()
    logger.info(f"Root node ready: {root.id}")

    app_nodes = [n for n in await root.nodes(direction="out") if isinstance(n, App)]
    app = app_nodes[0] if app_nodes else None

    if app:
        logger.info(f"App node already exists: {app.id}")
        App._cached_app = app
    else:
        _cfg = load_app_config(app_root)
        _fs = get_file_storage_config(app_root, _cfg)
        app = await App.create(
            app_id="jvagent_app",
            name="jvAgent",
            version=__version__,
            description="jvAgent Application",
            file_storage_provider=_fs["provider"],
            file_storage_root_dir=_fs["root_dir"],
            file_storage_enabled=True,
        )
        logger.info(f"Created App node: {app.id}")
        App._cached_app = app

    if not await root.is_connected_to(app):
        await root.connect(app)
        logger.info("Connected App node to Root node")
    else:
        logger.info("App node already connected to Root node")

    app_connected_nodes = await app.nodes()
    agents = None

    for node in app_connected_nodes:
        if isinstance(node, Agents):
            agents = node
            break

    if agents:
        logger.info(f"Agents node already exists: {agents.id}")
    else:
        agents = await Agents.create(total_agents=0, active_agents=0)
        logger.info(f"Created Agents node: {agents.id}")

    if not await app.is_connected_to(agents):
        await app.connect(agents)
        logger.info("Connected Agents node to App node")
    else:
        logger.info("Agents node already connected to App node")

    logger.info("Application graph bootstrap complete")


async def ensure_admin_user() -> bool:
    """Ensure a single admin user exists."""
    logger.debug("Checking for admin user...")

    env = load_env()
    admin_username = env.admin_username
    admin_password = env.admin_password
    admin_email = env.admin_email

    if not admin_password:
        logger.warning(
            "JVAGENT_ADMIN_PASSWORD not set in .env. " "Admin user will not be created."
        )
        return False

    auth_service = get_auth_service()
    user_count = await auth_service._user_count()

    if user_count == 0:
        return True

    existing_user = await auth_service._find_user_by_email(admin_email)
    if existing_user:
        roles = getattr(existing_user, "roles", None) or []
        if "admin" in roles:
            logger.debug(f"Admin user already exists: {admin_email}")
            return True
        logger.warning(
            f"User {admin_email} exists but lacks admin role. "
            "Use admin endpoint to assign admin role."
        )
        return False

    try:
        user_response = await auth_service.create_user_with_roles(
            UserCreateAdmin(
                email=admin_email,
                password=admin_password,
                name=admin_username,
                roles=["admin"],
                permissions=[],
            )
        )
        logger.info(
            f"Created admin user (recovery): {admin_email} (ID: {user_response.id})"
        )
        return True
    except ValueError as e:
        if "already exists" in str(e).lower():
            existing_user = await auth_service._find_user_by_email(admin_email)
            if existing_user:
                roles = getattr(existing_user, "roles", None) or []
                if "admin" in roles:
                    return True
        logger.error(f"Failed to create admin user: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to create admin user: {e}", exc_info=True)
        return False
