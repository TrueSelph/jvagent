"""Agent and action CLI command handlers."""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import List

from jvagent.cli.server_config import _set_db_env_from_config

logger = logging.getLogger(__name__)


def _handle_agent_create_command(args: List[str], app_root: str = None) -> None:
    """Scaffold a new agent directory and register it in app.yaml."""
    if app_root is None:
        app_root = os.getcwd()

    parser = argparse.ArgumentParser(prog="jvagent agent create")
    parser.add_argument(
        "spec",
        nargs="?",
        help="namespace/agent_id or namespace/agent_id@profile",
    )
    parser.add_argument(
        "--profile",
        default="orchestrator",
        help=(
            "Profile when spec has no @profile (default: orchestrator). "
            "Builtins: orchestrator, minimal, conversational, research, "
            "whatsapp_voice."
        ),
    )
    parser.add_argument(
        "--action",
        action="append",
        dest="actions",
        default=[],
        help="Extra action id (repeatable)",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--author", default=None)
    parser.add_argument("--jvagent-version", default=None, dest="jvagent_spec")

    ns = parser.parse_args(args)
    spec = ns.spec
    if not spec:
        if sys.stdin.isatty():
            spec = input("Agent (namespace/agent or namespace/agent@profile): ").strip()
        if not spec:
            parser.error("agent spec is required")

    from jvagent import __version__ as jvagent_version
    from jvagent.scaffold.operations import CreateAgentContext, create_agent_in_app

    jv_spec = ns.jvagent_spec or f"~{jvagent_version}"

    try:
        create_agent_in_app(
            CreateAgentContext(
                app_root=Path(app_root),
                agent_spec=spec,
                default_profile=ns.profile,
                extra_action_flags=list(ns.actions or []),
                force=ns.force,
                author=ns.author,
                version=ns.version,
                jvagent_spec=jv_spec,
            )
        )
    except (FileExistsError, FileNotFoundError, ValueError) as e:
        logger.error("%s", e)
        sys.exit(1)

    print(f"\nAgent scaffolded under {app_root}/agents/")
    print(
        "Run: jvagent bootstrap --update   (or jvagent --update) to load the new agent."
    )


def handle_bundle_command(args: List[str], app_root: str = None) -> None:
    """Handle bundle command - generates Dockerfile in app directory.

    Supports both:
    - jvagent /path/to/app bundle
    - jvagent bundle /path/to/app
    - jvagent bundle (uses current working directory)

    Args:
        args: Command arguments (may contain app root path)
        app_root: Path to the app root directory. If None, checks args or uses current working directory.
    """
    # If app_root not provided, check if first arg is a path
    if app_root is None:
        if args and args[0]:
            potential_path = Path(args[0]).expanduser().resolve()
            if potential_path.exists() and potential_path.is_dir():
                app_root = str(potential_path)
                logger.debug(f"Using app root from command argument: {app_root}")
            else:
                app_root = os.getcwd()
                logger.debug(
                    f"Argument '{args[0]}' is not a valid path, using current working directory"
                )
        else:
            app_root = os.getcwd()
            logger.debug(f"Using current working directory as app root: {app_root}")

    # Create bundler
    from jvagent.bundle import Bundler

    bundler = Bundler(app_root=app_root)

    # Generate Dockerfile
    success = bundler.generate_dockerfile()

    if not success:
        logger.error("Dockerfile generation failed")
        sys.exit(1)

    print(f"\n✓ Dockerfile generated successfully in {app_root}")


def handle_agent_command(args: List[str], app_root: str = None) -> None:
    """Handle agent management commands (create, list, uninstall).

    Agents are loaded from ``app.yaml`` on bootstrap/run. Use ``agent create`` to scaffold
    YAML under ``agents/`` and register the agent, then ``bootstrap --update``.

    Args:
        args: Command arguments
        app_root: Path to the app root directory. If None, uses current working directory.
    """
    if app_root is None:
        app_root = os.getcwd()

    if not args:
        print("Usage: jvagent agent <command>")
        print("Commands: create, list, uninstall")
        print("\nNote: Agents are installed automatically from app.yaml.")
        print(
            "      To install agents, add them to app.yaml and run 'jvagent' or 'jvagent bootstrap'."
        )
        return

    command = args[0]

    if command == "create":
        _handle_agent_create_command(args[1:], app_root=app_root)
        return

    _set_db_env_from_config(app_root)

    if command == "list":
        asyncio.run(list_agents())
    elif command == "uninstall":
        if len(args) < 2:
            print("Usage: jvagent agent uninstall <namespace/agent_name> [--yes]")
            return
        agent_ref = args[1]
        assume_yes = "--yes" in args[2:] or "-y" in args[2:]

        # Parse namespace/agent_name format
        if "/" not in agent_ref:
            print("Error: Agent reference must be in format 'namespace/agent_name'")
            return

        namespace, agent_name = agent_ref.split("/", 1)
        asyncio.run(
            uninstall_agent(
                namespace,
                agent_name,
                app_root=app_root,
                assume_yes=assume_yes,
            )
        )
    else:
        print(f"Unknown agent command: {command}")
        print("Available commands: create, list, uninstall")
        print("\nNote: Agents are installed automatically from app.yaml.")
        print(
            "      To install agents, add them to app.yaml and run 'jvagent' or 'jvagent bootstrap'."
        )


def handle_action_command(args: List[str], app_root: str = None) -> None:
    """Handle action management commands.

    Args:
        args: Command arguments
        app_root: Path to the app root directory. If None, uses current working directory.
    """
    if app_root is None:
        app_root = os.getcwd()

    if not args:
        print("Usage: jvagent action <command>")
        print("Commands: list, enable, disable")
        return

    command = args[0]

    _set_db_env_from_config(app_root)

    if command == "list":
        if len(args) < 2:
            print("Usage: jvagent action list <agent_name>")
            return
        agent_name = args[1]
        asyncio.run(list_actions(agent_name))
    elif command == "enable":
        if len(args) < 3:
            print("Usage: jvagent action enable <agent_name> <action_id>")
            return
        agent_name = args[1]
        action_id = args[2]
        asyncio.run(enable_action(agent_name, action_id))
    elif command == "disable":
        if len(args) < 3:
            print("Usage: jvagent action disable <agent_name> <action_id>")
            return
        agent_name = args[1]
        action_id = args[2]
        asyncio.run(disable_action(agent_name, action_id))
    else:
        print(f"Unknown action command: {command}")


async def list_agents() -> None:
    """List all agents."""
    from jvagent.core.agent import Agent

    agents = await Agent.find({})

    if not agents:
        print("No agents found")
        return

    print(f"\n=== Agents ({len(agents)}) ===\n")
    for agent in agents:
        namespace = getattr(agent, "namespace", "")
        alias = getattr(agent, "alias", "")

        # Display alias if available, otherwise use name
        display_name = alias if alias else agent.name

        if namespace:
            print(f"  - {display_name} ({namespace}/{agent.name})")
        else:
            print(f"  - {display_name} ({agent.name})")
        print(f"    ID: {agent.id}")
        print(f"    Enabled: {agent.enabled}")
        print(f"    Description: {agent.description}")
        print()


async def uninstall_agent(
    namespace: str,
    agent_name: str,
    app_root: str = None,
    assume_yes: bool = False,
) -> None:
    """Uninstall an agent (cascade-deletes user memory under that agent).

    Requires ``--yes`` or an interactive ``yes`` confirmation — same safety
    bar as ``--purge``.
    """
    from jvagent.core.agent_loader import AgentLoader

    if app_root is None:
        app_root = os.getcwd()

    ref = f"{namespace}/{agent_name}"
    if not assume_yes:
        print(
            f"This will permanently delete agent {ref} and cascade-delete "
            "its users/conversations/interactions."
        )
        try:
            answer = input("Type 'yes' to confirm: ").strip()
        except EOFError:
            answer = ""
        if answer != "yes":
            print("Aborted.")
            return

    loader = AgentLoader(app_root)
    success = await loader.uninstall_agent(namespace, agent_name)

    if success:
        print(f"Uninstalled agent: {ref}")
    else:
        print(f"Failed to uninstall agent: {ref}")


async def list_actions(agent_name: str) -> None:
    """List actions for an agent."""
    from jvagent.core.agent import Agent

    # Find the agent
    agent = await Agent.find_one({"context.name": agent_name})
    if not agent:
        print(f"Agent not found: {agent_name}")
        return

    # Get Actions manager
    actions_manager = await agent.node(node="Actions")

    if not actions_manager:
        print(f"No Actions manager found for agent: {agent_name}")
        return

    # Get actions
    actions_list = await actions_manager.list_actions()

    if not actions_list:
        print(f"No actions found for agent: {agent_name}")
        return

    print(f"\n=== Actions for {agent_name} ({len(actions_list)}) ===\n")
    for action in actions_list:
        print(f"  - {action.get('label')} ({action.get('package_name')})")
        print(f"    ID: {action.get('id')}")
        print(f"    Enabled: {action.get('enabled')}")
        print(f"    Description: {action.get('description')}")
        print(f"    Version: {action.get('version')}")
        print()


async def enable_action(agent_name: str, action_label: str) -> None:
    """Enable an action for an agent."""
    from jvagent.core.agent import Agent

    # Find the agent
    agent = await Agent.find_one({"context.name": agent_name})
    if not agent:
        print(f"Agent not found: {agent_name}")
        return

    # Get Actions manager
    actions_manager = await agent.node(node="Actions")

    if not actions_manager:
        print(f"No Actions manager found for agent: {agent_name}")
        return

    # Get the action
    action = await actions_manager.get_action_by_label(action_label)
    if not action:
        print(f"Action not found: {action_label}")
        return

    # Enable using Action method directly
    await action.enable()
    print(f"Enabled action: {action_label}")


async def disable_action(agent_name: str, action_label: str) -> None:
    """Disable an action for an agent."""
    from jvagent.core.agent import Agent

    # Find the agent
    agent = await Agent.find_one({"context.name": agent_name})
    if not agent:
        print(f"Agent not found: {agent_name}")
        return

    # Get Actions manager
    actions_manager = await agent.node(node="Actions")

    if not actions_manager:
        print(f"No Actions manager found for agent: {agent_name}")
        return

    # Get the action
    action = await actions_manager.get_action_by_label(action_label)
    if not action:
        print(f"Action not found: {action_label}")
        return

    # Disable using Action method directly
    await action.disable()
    print(f"Disabled action: {action_label}")
