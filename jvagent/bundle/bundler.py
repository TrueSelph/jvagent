"""Dockerfile generator for jvagent applications.

This module generates Dockerfiles directly in the jvagent app directory
by extending a base template and including pip dependencies from action info.yaml files.
"""

import logging
from pathlib import Path

from jvagent.bundle.dockerfile_generator import generate_dockerfile

logger = logging.getLogger(__name__)


class Bundler:
    """Generates Dockerfile for jvagent applications."""

    def __init__(self, app_root: str):
        """Initialize the bundler.

        Args:
            app_root: Path to the jvagent app root directory
        """
        self.app_root = Path(app_root).resolve()

    def generate_dockerfile(self) -> bool:
        """Generate Dockerfile in the app directory.

        Returns:
            True if generation succeeded, False otherwise
        """
        try:
            logger.info(f"Generating Dockerfile for app: {self.app_root}")

            # Validate app.yaml exists
            if not self._validate_app():
                return False

            # Get path to base Dockerfile template
            bundle_dir = Path(__file__).parent
            base_template_path = bundle_dir / "Dockerfile.base"

            if not base_template_path.exists():
                logger.error(
                    f"Base Dockerfile template not found: {base_template_path}"
                )
                return False

            # Generate Dockerfile
            dockerfile_content = generate_dockerfile(self.app_root, base_template_path)

            # Write Dockerfile to app directory
            dockerfile_path = self.app_root / "Dockerfile"
            dockerfile_path.write_text(dockerfile_content)

            logger.info(f"Dockerfile generated successfully: {dockerfile_path}")
            return True

        except Exception as e:
            logger.error(f"Dockerfile generation failed: {e}", exc_info=True)
            return False

    def _validate_app(self) -> bool:
        """Validate that app.yaml exists in app root.

        Returns:
            True if valid, False otherwise
        """
        app_yaml = self.app_root / "app.yaml"
        if not app_yaml.exists():
            logger.error(f"app.yaml not found in {self.app_root}")
            return False
        logger.debug(f"Found app.yaml: {app_yaml}")
        return True
