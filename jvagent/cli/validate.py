"""CLI validation and usage helpers."""

import logging
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


def run_validate(app_root: str) -> int:
    """Validate ``app.yaml`` and discovered ``agent.yaml`` files.

    Runs the same structural checks as runtime (``validate_*`` helpers).
    Prints issues to the log and returns 1 if any warning-level issue is found
    (suitable for CI).

    Args:
        app_root: Application root directory containing ``app.yaml``.

    Returns:
        0 if no issues, 1 otherwise.
    """
    import yaml

    from jvagent.core.agent_loader import AgentLoader
    from jvagent.core.agent_yaml_validator import (
        _reset_warning_cache_for_tests as reset_agent_yaml_warnings,
    )
    from jvagent.core.agent_yaml_validator import (
        validate_agent_yaml,
    )
    from jvagent.core.app_yaml_validator import (
        _reset_warning_cache_for_tests as reset_app_yaml_warnings,
    )
    from jvagent.core.app_yaml_validator import (
        validate_app_yaml_descriptor,
    )
    from jvagent.core.env_resolver import resolve_env_placeholders

    root = Path(app_root).resolve()
    app_yaml = root / "app.yaml"

    reset_app_yaml_warnings()
    reset_agent_yaml_warnings()

    if not app_yaml.is_file():
        logger.error("app.yaml not found: %s", app_yaml)
        return 1

    try:
        with open(app_yaml, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except Exception as e:
        logger.error("Failed to read or parse app.yaml: %s", e, exc_info=True)
        return 1

    if not isinstance(raw, dict):
        logger.error("app.yaml must contain a mapping at the root")
        return 1

    data = resolve_env_placeholders(raw)
    issues: List[str] = []
    for w in validate_app_yaml_descriptor(data):
        suffix = f" Hint: {w.hint}" if w.hint else ""
        issues.append(f"app.yaml [{w.path}] {w.message}{suffix}")

    loader = AgentLoader(str(root))
    for namespace, agent_name in loader.discover_agents():
        agent_file = root / "agents" / namespace / agent_name / "agent.yaml"
        try:
            with open(agent_file, encoding="utf-8") as f:
                agent_raw = yaml.safe_load(f)
        except Exception as e:
            issues.append(f"{agent_file}: failed to load ({e})")
            continue
        if not isinstance(agent_raw, dict):
            issues.append(f"{agent_file}: expected mapping at root")
            continue
        agent_data = resolve_env_placeholders(agent_raw)
        for agent_issue in validate_agent_yaml(agent_data):
            suffix = f" Hint: {agent_issue.hint}" if agent_issue.hint else ""
            issues.append(
                f"{agent_file} [{agent_issue.path}] {agent_issue.message}{suffix}"
            )

    if issues:
        for line in issues:
            logger.error("validate: %s", line)
        logger.error("validate failed: %d issue(s) in %s", len(issues), root)
        return 1

    logger.info("validate OK: %s", root)
    return 0


def print_usage() -> None:
    """Print CLI usage information."""
    print(
        """
jvagent - Agentive Platform

    Usage:
        jvagent [<app_root>] [run] [--update] [--debug] [--serverless]   Start the jvagent server (default)
        jvagent <app_root> [run] [--update] [--debug] [--serverless]    Start server with app root path
                                --update: Update existing agents/actions from YAML files
                                --serverless: Simulate serverless runtime (single-threaded, no background tasks)
    jvagent [run] [--debug] --stress-seed --user-memory-nodes N --interactions-per-user-memory-node M ...
                                After bootstrap, populate the memory graph, then start the server (same DB)
    jvagent [<app_root>] status             Show application status
    jvagent [<app_root>] validate         Check app.yaml and agent.yaml structure (exit 1 if issues; for CI)
    jvagent [<app_root>] stress-seed --user-memory-nodes N --interactions-per-user-memory-node M
                                  Seed synthetic User + Interaction graph (stress testing)
    jvagent [<app_root>] bootstrap [--update]  Bootstrap application graph
                                  --update: Update existing agents/actions from YAML files
    jvagent [<app_root>] bundle [<app_root>]
                                  Generate Dockerfile in app directory
                                  Discovers action dependencies from info.yaml files
                                  App root can be specified before or after 'bundle' command
                                  Defaults to current working directory if not specified
    jvagent chat [--url URL] [--port N] [--host HOST] [--no-browser]
                                  Serve the bundled jvchat web UI on its own port (default 3000)
                                  --url injects the agent server URL the UI talks to (no rebuild)
    jvagent [<app_root>] agent create [SPEC] [--profile NAME] [--action ID]... [--force]
                                  Scaffold agents/<ns>/<id>/ and register in app.yaml
                                  SPEC: namespace/agent or namespace/agent@profile
    jvagent [<app_root>] skill add <agent_ref> <skill_name> [--description TEXT] [--force]
                                  Create agents/<ns>/<id>/skills/<skill_name>/SKILL.md starter
    jvagent [<app_root>] skill list [--agent <agent_ref>] [--builtin]
                                  List reusable and/or app-local skill bundles
    jvagent [<app_root>] skill show <skill_name> [--agent <agent_ref>] [--builtin]
                                  Show one skill bundle's metadata and SOP
    jvagent [<app_root>] agent list         List all installed agents
    jvagent [<app_root>] agent uninstall <name>    Uninstall an agent
    jvagent app create [--dir PATH] [--app-id ID] ...   Scaffold a new application tree
    jvagent app profile new <name> [--extends PROFILE]   Add profiles/<name>.yaml (from app root)
    jvagent [<app_root>] action list <agent_name>  List actions for an agent
    jvagent [<app_root>] action enable <agent_name> <action_id>   Enable an action
    jvagent [<app_root>] action disable <agent_name> <action_id>  Disable an action

Note: Agents are installed automatically from app.yaml when you run jvagent or bootstrap.
      Use `jvagent app create` or `jvagent agent create` to scaffold YAML, then bootstrap.
      Without `--update`, the next YAML sync mode can be set on the App node (`update_mode`: run | merge | source)
      via admin `PUT /api/app/update_mode` and applies on the next start; after a successful start it resets to run.
      CLI `--update` always overrides the stored value for that invocation.

Arguments:
    <app_root>                Path to the app root directory (default: current directory)
                              Must be a valid directory path. If not provided, uses current working directory.

Flags:
    --update                   Update existing agents and actions from YAML files (non-destructive merge).
                                Applies source changes while preserving database state.
    --update --source          Destructive update: fully overwrite database state from source YAML files.
                                Deletes and recreates action nodes (child nodes are lost).
    --update --merge           Explicit non-destructive merge (same as --update alone).
    --purge                    Delete local app, logging, and PageIndex stores (json/sqlite only; development mode)
    --debug                    Enable debug logging (verbose output for troubleshooting)
    --serverless              Simulate serverless execution environment (single-threaded, no background tasks)

Environment Variables:
    JVAGENT_ADMIN_PASSWORD     Admin user password (required)
    JVAGENT_HOST              Server host (default: 127.0.0.1)
    JVAGENT_PORT              Server port (default: 8000)
    JVSPATIAL_DB_PATH         Database path (default: ./jvagent_db)
    JVSPATIAL_FILES_ROOT_PATH File storage path (default: ./.files)

Examples:
    jvagent                                    # Run from current directory
    jvagent /path/to/my_app                    # Run from specified app directory
    jvagent /path/to/my_app --update           # Run with merge update (non-destructive)
    jvagent /path/to/my_app --update --source  # Run with source update (destructive)
    jvagent --serverless                      # Run with serverless runtime simulation
    jvagent /path/to/my_app bootstrap          # Bootstrap from specified directory
    jvagent /path/to/my_app bootstrap --update # Bootstrap with merge update
    jvagent --stress-seed --user-memory-nodes 50 --interactions-per-user-memory-node 20
    jvagent stress-seed --user-memory-nodes 50 --interactions-per-user-memory-node 20
    jvagent /path/to/my_app bundle             # Generate Dockerfile in app directory
    jvagent bundle /path/to/my_app             # Generate Dockerfile (path after command)
    jvagent bundle                             # Generate Dockerfile in current directory
    jvagent app create --yes --dir ./my_app --app-id my_app --title T --description D --author A --agent jvagent/bot@minimal
    jvagent agent create acme/bot@conversational
    jvagent app profile new my_profile --extends minimal
    """
    )
