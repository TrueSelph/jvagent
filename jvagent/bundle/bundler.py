"""Dockerfile generator for jvagent applications.

This module generates Dockerfiles directly in the jvagent app directory
by extending a base template and including pip dependencies from action info.yaml files.
"""

import logging
import shutil
from pathlib import Path

from jvagent.bundle.dockerfile_generator import generate_dockerfile

logger = logging.getLogger(__name__)

_DOCKERIGNORE_LINES = (
    ".env",
    ".env.*",
    "*.db",
    "*.sqlite",
    "*.sqlite3",
    "jvdb/",
    ".git/",
    "__pycache__/",
    "*.pyc",
    ".pytest_cache/",
    ".mypy_cache/",
    "node_modules/",
)


class Bundler:
    """Generates Dockerfile for jvagent applications."""

    def __init__(self, app_root: str):
        """Initialize the bundler.

        Args:
            app_root: Path to the jvagent app root directory
        """
        self.app_root = Path(app_root).resolve()

    def generate_dockerfile(self, *, force: bool = False) -> bool:
        """Generate Dockerfile in the app directory.

        Args:
            force: When True, overwrite an existing Dockerfile (backup to .bak).

        Returns:
            True if generation succeeded, False otherwise
        """
        try:
            logger.info(f"Generating Dockerfile for app: {self.app_root}")

            if not self._validate_app():
                return False

            dockerfile_path = self.app_root / "Dockerfile"
            if dockerfile_path.exists() and not force:
                logger.error(
                    "Dockerfile already exists at %s; pass force=True to overwrite",
                    dockerfile_path,
                )
                return False

            bundle_dir = Path(__file__).parent
            base_template_path = bundle_dir / "Dockerfile.base"

            if not base_template_path.exists():
                logger.error(
                    f"Base Dockerfile template not found: {base_template_path}"
                )
                return False

            dockerfile_content = generate_dockerfile(self.app_root, base_template_path)

            if dockerfile_path.exists() and force:
                backup = self.app_root / "Dockerfile.bak"
                shutil.copy2(dockerfile_path, backup)
                logger.info("Backed up existing Dockerfile to %s", backup)

            dockerfile_path.write_text(dockerfile_content)
            self._write_dockerignore()

            logger.info(f"Dockerfile generated successfully: {dockerfile_path}")
            return True

        except Exception as e:
            logger.error(f"Dockerfile generation failed: {e}", exc_info=True)
            return False

    def _write_dockerignore(self) -> None:
        path = self.app_root / ".dockerignore"
        existing = (
            path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        )
        merged = list(dict.fromkeys([*existing, *_DOCKERIGNORE_LINES]))
        path.write_text("\n".join(merged) + "\n", encoding="utf-8")

    def _validate_app(self) -> bool:
        """Validate that app.yaml exists in app root."""
        app_yaml = self.app_root / "app.yaml"
        if not app_yaml.exists():
            logger.error(f"app.yaml not found in {self.app_root}")
            return False
        logger.debug(f"Found app.yaml: {app_yaml}")
        return True
