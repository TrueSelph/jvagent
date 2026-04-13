"""Dockerfile generator for jvagent applications.

This module generates Dockerfiles by extending a base template and including
pip dependencies discovered from action info.yaml files.
"""

import logging
from pathlib import Path
from typing import Dict, List

import yaml

logger = logging.getLogger(__name__)


def discover_action_dependencies(app_root: Path) -> Dict[str, List[str]]:
    """Discover pip dependencies from all actions in the app.

    Scans the agents directory structure to find all actions and extract
    their pip dependencies from info.yaml files.

    Args:
        app_root: Path to the jvagent app root directory

    Returns:
        Dictionary mapping action names (namespace/action_name) to list of pip dependencies
    """
    dependencies: Dict[str, List[str]] = {}
    agents_path = app_root / "agents"

    if not agents_path.exists() or not agents_path.is_dir():
        logger.debug(f"No agents directory found at {agents_path}")
        return dependencies

    # Walk through agents/{namespace}/{agent_name}/actions/{namespace}/{action_name}/
    for namespace_dir in agents_path.iterdir():
        if not namespace_dir.is_dir():
            continue

        namespace = namespace_dir.name

        # Iterate through agent directories
        for agent_dir in namespace_dir.iterdir():
            if not agent_dir.is_dir():
                continue

            # Look for actions directory
            actions_path = agent_dir / "actions"
            if not actions_path.exists() or not actions_path.is_dir():
                continue

            # Iterate through action namespace directories
            for action_namespace_dir in actions_path.iterdir():
                if not action_namespace_dir.is_dir():
                    continue

                action_namespace = action_namespace_dir.name

                # Iterate through action directories
                for action_dir in action_namespace_dir.iterdir():
                    if not action_dir.is_dir():
                        continue

                    # Look for info.yaml
                    info_file = action_dir / "info.yaml"
                    if not info_file.exists():
                        continue

                    # Load and parse info.yaml
                    try:
                        with open(info_file, "r", encoding="utf-8") as f:
                            data = yaml.safe_load(f)

                        if not data or not isinstance(data, dict):
                            continue

                        # Extract pip dependencies
                        package = data.get("package", {})
                        if not isinstance(package, dict):
                            continue

                        deps = package.get("dependencies", {})
                        if not isinstance(deps, dict):
                            continue

                        pip_deps = deps.get("pip", [])
                        if not pip_deps or not isinstance(pip_deps, list):
                            continue

                        # Filter out empty strings and normalize
                        pip_deps = [
                            dep.strip() for dep in pip_deps if dep and dep.strip()
                        ]

                        if pip_deps:
                            # Use action name from package.name or construct from path
                            action_name = package.get("name")
                            if not action_name:
                                action_name = f"{action_namespace}/{action_dir.name}"

                            dependencies[action_name] = pip_deps
                            logger.debug(
                                f"Found {len(pip_deps)} dependencies for action {action_name}"
                            )

                    except Exception as e:
                        logger.warning(f"Error reading {info_file}: {e}")
                        continue

    return dependencies


def generate_dockerfile_run_commands(dependencies: Dict[str, List[str]]) -> str:
    """Generate RUN commands for pip dependencies.

    Creates separate RUN commands per action for better Docker layer caching.
    Handles duplicate dependencies by deduplicating within each action's command.

    Args:
        dependencies: Dictionary mapping action names to pip dependency lists

    Returns:
        String containing RUN commands for Dockerfile
    """
    if not dependencies:
        return ""

    commands = []
    commands.append("# Action-specific pip dependencies")

    for action_name, deps in sorted(dependencies.items()):
        # Deduplicate dependencies for this action (preserve order)
        seen = set()
        unique_deps = []
        for dep in deps:
            # Extract package name for comparison (handle version specifiers)
            pkg_name = (
                dep.split(">=")[0].split("==")[0].split("<")[0].split("~")[0].strip()
            )
            if pkg_name not in seen:
                seen.add(pkg_name)
                unique_deps.append(dep)

        if unique_deps:
            commands.append(f"# Dependencies for {action_name}")
            commands.append(
                f'RUN /opt/venv/bin/pip install --no-cache-dir {" ".join(unique_deps)}'
            )

    return "\n".join(commands)


def generate_dockerfile(app_root: Path, base_template_path: Path) -> str:
    """Generate Dockerfile for jvagent app.

    Loads the base Dockerfile template and extends it with action-specific
    pip dependencies.

    Args:
        app_root: Path to the jvagent app root directory
        base_template_path: Path to the base Dockerfile template

    Returns:
        Complete Dockerfile content as string
    """
    # Load base template
    if not base_template_path.exists():
        raise FileNotFoundError(
            f"Base Dockerfile template not found: {base_template_path}"
        )

    with open(base_template_path, "r", encoding="utf-8") as f:
        base_template = f.read()

    # Discover action dependencies
    logger.info("Discovering action dependencies...")
    dependencies = discover_action_dependencies(app_root)

    if dependencies:
        logger.info(f"Found dependencies for {len(dependencies)} actions")
        # Generate RUN commands
        run_commands = generate_dockerfile_run_commands(dependencies)
        # Replace placeholder in template
        # The placeholder is: "# {{ACTION_DEPENDENCIES}}" on its own line
        placeholder = "# {{ACTION_DEPENDENCIES}}"
        # Replace the placeholder line (with newline) with the actual commands
        dockerfile_content = base_template.replace(
            f"{placeholder}\n", f"{run_commands}\n"
        )
        # If replacement didn't work (no newline after placeholder), try without newline
        if placeholder in dockerfile_content:
            dockerfile_content = dockerfile_content.replace(placeholder, run_commands)
    else:
        logger.info("No action dependencies found")
        # Remove placeholder line if no dependencies
        placeholder = "# {{ACTION_DEPENDENCIES}}"
        dockerfile_content = base_template.replace(f"{placeholder}\n", "")
        # If still present (no newline), remove it
        if placeholder in dockerfile_content:
            dockerfile_content = dockerfile_content.replace(placeholder, "")

    return dockerfile_content
