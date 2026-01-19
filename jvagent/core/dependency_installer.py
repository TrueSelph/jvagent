"""Dependency installer for action pip dependencies.

This module provides functionality to install pip dependencies specified in
action info.yaml files before actions are loaded.

Pip dependencies are specified in the info.yaml file under:
  package:
    dependencies:
      pip:
        - package1>=1.0.0
        - package2==2.3.0
        - package3

Dependencies are automatically installed during action discovery, ensuring
they are available before the action module is imported.
"""

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def install_pip_dependencies(
    dependencies: List[str],
    action_name: str,
    action_path: Optional[Path] = None,
    upgrade: bool = False,
    skip_if_installed: bool = True,
) -> bool:
    """Install pip dependencies for an action.

    Args:
        dependencies: List of pip package specifications (e.g., ["requests>=2.25.0", "numpy"])
        action_name: Name of the action (for logging)
        action_path: Optional path to the action directory (for logging)
        upgrade: If True, upgrade packages if already installed
        skip_if_installed: If True, check if packages are already installed before attempting install

    Returns:
        True if installation succeeded, False otherwise
    """
    if not dependencies:
        return True

    if not isinstance(dependencies, list):
        logger.warning(
            f"Action {action_name}: dependencies.pip must be a list, got {type(dependencies)}"
        )
        return False

    # Filter out empty strings
    dependencies = [dep.strip() for dep in dependencies if dep and dep.strip()]

    if not dependencies:
        return True

    # If skip_if_installed is True and not upgrading, check which packages need installation
    if skip_if_installed and not upgrade:
        packages_to_install = []
        for dep in dependencies:
            if not check_pip_dependency_installed(dep):
                packages_to_install.append(dep)
        
        if not packages_to_install:
            return True
        
        dependencies = packages_to_install

    logger.info(
        f"Installing pip dependencies for action {action_name}: {', '.join(dependencies)}"
    )

    try:
        # Build pip install command
        cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--no-warn-script-location"]

        if upgrade:
            cmd.append("--upgrade")

        # Add dependencies
        cmd.extend(dependencies)

        # Run pip install
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,  # Don't raise exception, handle manually
        )

        if result.returncode == 0:
            logger.debug(f"Successfully installed dependencies for action {action_name}")
            return True
        else:
            logger.error(
                f"Failed to install dependencies for action {action_name}: "
                f"{result.stderr or result.stdout}"
            )
            return False

    except Exception as e:
        logger.error(f"Error installing dependencies for action {action_name}: {e}", exc_info=True)
        return False


def install_action_dependencies(metadata: Dict[str, Any], action_name: str, action_path: Path) -> bool:
    """Install dependencies for an action from its metadata.

    Extracts pip dependencies from the dependencies.pip field in the action's
    info.yaml metadata and installs them.

    Args:
        metadata: Action metadata dictionary (from info.yaml)
        action_name: Name of the action (for logging)
        action_path: Path to the action directory (for logging)

    Returns:
        True if installation succeeded or no dependencies, False otherwise
    """
    # Extract dependencies from package.dependencies
    package = metadata.get("package", {})
    if not isinstance(package, dict):
        return True

    dependencies = package.get("dependencies", {})
    if not isinstance(dependencies, dict):
        return True

    # Extract pip dependencies
    pip_deps = dependencies.get("pip", [])
    if not pip_deps:
        return True

    # Install pip dependencies
    return install_pip_dependencies(pip_deps, action_name, action_path)


def check_pip_dependency_installed(package_spec: str) -> bool:
    """Check if a pip package is installed.

    Args:
        package_spec: Package specification (e.g., "requests>=2.25.0" or "numpy")

    Returns:
        True if package is installed and meets requirements, False otherwise
    """
    # Extract package name (remove version specifiers)
    package_name = package_spec.split(">=")[0].split("==")[0].split("!=")[0].split("<=")[0].split(">")[0].split("<")[0].strip()

    try:
        # Try importing the package to check if it's installed
        # This is a simple check - for more robust checking, use pkg_resources
        __import__(package_name.replace("-", "_"))
        return True
    except ImportError:
        # Package not installed or import name differs
        # Use pip list to check more accurately
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "list", "--format=json"],
                capture_output=True,
                text=True,
                check=True,
            )
            import json

            installed_packages = json.loads(result.stdout)
            for pkg in installed_packages:
                if pkg["name"].lower() == package_name.lower():
                    return True
            return False
        except Exception:
            return False

