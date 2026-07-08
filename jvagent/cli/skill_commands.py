"""Skill bundle CLI command handlers."""

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


def _build_skill_stub(skill_name: str, description: str) -> str:
    return f"""---
name: {skill_name}
description: {description}
allowed-tools: []
---

## Workflow

1. Clarify the objective and constraints.
2. Use available tools to gather evidence.
3. Return a concise, actionable result.
"""


def _handle_skill_add_command(args: List[str], app_root: str = None) -> None:
    """Create a Claude-style SKILL.md bundle for an agent."""
    if app_root is None:
        app_root = os.getcwd()

    parser = argparse.ArgumentParser(prog="jvagent skill add")
    parser.add_argument("agent_ref", help="namespace/agent_id")
    parser.add_argument("skill_name", help="Skill bundle folder/name")
    parser.add_argument(
        "--description",
        default="A skill bundle for the thinking interact action.",
        help="Frontmatter description for SKILL.md",
    )
    parser.add_argument("--force", action="store_true")
    ns = parser.parse_args(args)

    if "/" not in ns.agent_ref:
        parser.error("agent_ref must be in format namespace/agent_id")

    namespace, agent_name = ns.agent_ref.split("/", 1)
    agent_dir = Path(app_root).resolve() / "agents" / namespace / agent_name
    if not agent_dir.is_dir():
        parser.error(f"agent directory not found: {agent_dir}")

    skills_dir = agent_dir / "skills"
    skill_dir = skills_dir / ns.skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"

    if skill_path.exists() and not ns.force:
        parser.error(f"{skill_path} already exists (use --force to overwrite)")

    skill_path.write_text(
        _build_skill_stub(ns.skill_name, ns.description), encoding="utf-8"
    )
    print(f"Wrote {skill_path}")


def _handle_skill_create_leadgen_command(args: List[str], app_root: str = None) -> None:
    """Scaffold a LeadGenAction skill from the example_leadgen template."""
    if app_root is None:
        app_root = os.getcwd()

    parser = argparse.ArgumentParser(prog="jvagent skill create-leadgen")
    parser.add_argument("agent_ref", help="namespace/agent_id")
    parser.add_argument("skill_name", help="Skill folder/name under agents/.../skills/")
    parser.add_argument(
        "--title",
        default="",
        help="Leadgen title in frontmatter (defaults to skill_name title case)",
    )
    parser.add_argument("--force", action="store_true")
    ns = parser.parse_args(args)

    if "/" not in ns.agent_ref:
        parser.error("agent_ref must be in format namespace/agent_id")

    namespace, agent_name = ns.agent_ref.split("/", 1)
    agent_dir = Path(app_root).resolve() / "agents" / namespace / agent_name
    if not agent_dir.is_dir():
        parser.error(f"agent directory not found: {agent_dir}")

    template_dir = (
        Path(__file__).resolve().parent.parent
        / "action"
        / "leadgen"
        / "examples"
        / "example_leadgen"
    )
    if not template_dir.is_dir():
        parser.error(f"example_leadgen template not found: {template_dir}")

    dest = agent_dir / "skills" / ns.skill_name
    if dest.exists() and not ns.force:
        parser.error(f"{dest} already exists (use --force to overwrite)")

    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(template_dir, dest)

    title = ns.title or ns.skill_name.replace("_", " ").title()
    skill_md = dest / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    text = text.replace("name: example_leadgen", f"name: {ns.skill_name}", 1)
    text = text.replace("title: Product Inquiry Leads", f"title: {title}", 1)
    skill_md.write_text(text, encoding="utf-8")

    custom_tools = dest / "scripts" / "custom_tools.py"
    ct = custom_tools.read_text(encoding="utf-8")
    ct = ct.replace(
        '_SKILL_NAME = "example_leadgen"', f'_SKILL_NAME = "{ns.skill_name}"'
    )
    custom_tools.write_text(ct, encoding="utf-8")

    from jvagent.action.leadgen._validate_contract import validate_leadgen_skill_dir

    ok, issues = validate_leadgen_skill_dir(dest)
    print(f"Created leadgen skill at {dest}")
    if ok:
        print("Contract validation: PASSED")
    else:
        print("Contract validation: FAILED")
        for issue in issues:
            print(f"  - {issue}")
    print(
        "Next: register the skill in agent.yaml orchestrator skills: "
        "and enable jvagent/leadgen in actions."
    )


def _handle_skill_create_interview_command(
    args: List[str], app_root: str = None
) -> None:
    """Scaffold an InterviewAction skill from the example_interview template."""
    if app_root is None:
        app_root = os.getcwd()

    parser = argparse.ArgumentParser(prog="jvagent skill create-interview")
    parser.add_argument("agent_ref", help="namespace/agent_id")
    parser.add_argument("skill_name", help="Skill folder/name under agents/.../skills/")
    parser.add_argument(
        "--title",
        default="",
        help="Interview title in frontmatter (defaults to skill_name title case)",
    )
    parser.add_argument("--force", action="store_true")
    ns = parser.parse_args(args)

    if "/" not in ns.agent_ref:
        parser.error("agent_ref must be in format namespace/agent_id")

    namespace, agent_name = ns.agent_ref.split("/", 1)
    agent_dir = Path(app_root).resolve() / "agents" / namespace / agent_name
    if not agent_dir.is_dir():
        parser.error(f"agent directory not found: {agent_dir}")

    template_dir = (
        Path(__file__).resolve().parent.parent
        / "action"
        / "interview"
        / "examples"
        / "example_interview"
    )
    if not template_dir.is_dir():
        parser.error(f"example_interview template not found: {template_dir}")

    dest = agent_dir / "skills" / ns.skill_name
    if dest.exists() and not ns.force:
        parser.error(f"{dest} already exists (use --force to overwrite)")

    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(template_dir, dest)

    title = ns.title or ns.skill_name.replace("_", " ").title()
    skill_md = dest / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    text = text.replace("name: example_interview", f"name: {ns.skill_name}", 1)
    text = text.replace("example_interview__", f"{ns.skill_name}__")
    text = text.replace("title: Product Feedback", f"title: {title}", 1)
    skill_md.write_text(text, encoding="utf-8")

    custom_tools = dest / "scripts" / "custom_tools.py"
    ct = custom_tools.read_text(encoding="utf-8")
    ct = ct.replace(
        '_SKILL_NAME = "example_interview"', f'_SKILL_NAME = "{ns.skill_name}"'
    )
    custom_tools.write_text(ct, encoding="utf-8")

    from jvagent.action.interview._validate_contract import (
        validate_interview_skill_dir,
    )

    ok, issues = validate_interview_skill_dir(dest)
    print(f"Created interview skill at {dest}")
    if ok:
        print("Contract validation: PASSED")
    else:
        print("Contract validation: FAILED")
        for issue in issues:
            print(f"  - {issue}")
    print(
        "Next: register the skill in agent.yaml orchestrator skills: "
        "and enable jvagent/interview in actions."
    )


def _resolve_skills_for_cli(
    *,
    app_root: str,
    agent_ref: Optional[str],
    include_builtin: bool,
) -> Dict[str, Dict[str, Any]]:
    from jvagent.scaffold.skill_resolve import (
        resolve_agent_skills,
        resolve_builtin_skills,
        resolve_merged_skill_bundles,
    )

    if agent_ref:
        if "/" not in agent_ref:
            raise ValueError("agent_ref must be in format namespace/agent_id")
        namespace, agent_name = agent_ref.split("/", 1)
        if include_builtin:
            return resolve_merged_skill_bundles(
                app_root=app_root,
                namespace=namespace,
                agent_name=agent_name,
                include_builtin=True,
            )
        return resolve_agent_skills(app_root, namespace, agent_name)

    # No agent specified: default to builtin catalog for discoverability.
    return resolve_builtin_skills()


def _load_agent_thinking_skill_config(
    app_root: str,
    agent_ref: str,
) -> Dict[str, Any]:
    """Load thinking skill selector settings from an agent's agent.yaml."""
    import yaml

    if "/" not in agent_ref:
        raise ValueError("agent_ref must be in format namespace/agent_id")

    namespace, agent_name = agent_ref.split("/", 1)
    agent_yaml = (
        Path(app_root).resolve() / "agents" / namespace / agent_name / "agent.yaml"
    )
    if not agent_yaml.is_file():
        return {"skills": None, "denied_skills": [], "skills_source": "both"}

    try:
        data = yaml.safe_load(agent_yaml.read_text(encoding="utf-8")) or {}
    except Exception:
        return {"skills": None, "denied_skills": [], "skills_source": "both"}

    actions = data.get("actions", []) if isinstance(data, dict) else []
    if not isinstance(actions, list):
        return {"skills": None, "denied_skills": [], "skills_source": "both"}

    for action in actions:
        if not isinstance(action, dict):
            continue
        if action.get("action") != "jvagent/skill_interact_action":
            continue
        context = action.get("context", {})
        if not isinstance(context, dict):
            context = {}
        return {
            "skills": context.get("skills"),
            "denied_skills": context.get("denied_skills", []),
            "skills_source": context.get("skills_source", "both"),
        }

    return {"skills": None, "denied_skills": [], "skills_source": "both"}


def _handle_skill_list_command(args: List[str], app_root: str = None) -> None:
    """List available skill bundles."""
    if app_root is None:
        app_root = os.getcwd()

    parser = argparse.ArgumentParser(prog="jvagent skill list")
    parser.add_argument("--agent", default=None, help="namespace/agent_id")
    parser.add_argument(
        "--builtin",
        action="store_true",
        help="Include built-in skills when --agent is used",
    )
    ns = parser.parse_args(args)

    try:
        bundles = _resolve_skills_for_cli(
            app_root=app_root,
            agent_ref=ns.agent,
            include_builtin=ns.builtin,
        )
    except ValueError as exc:
        parser.error(str(exc))

    if not bundles:
        print("No skills found.")
        return

    active_skill_names: Set[str] = set()
    if ns.agent:
        from jvagent.scaffold.skill_resolve import apply_skill_selector

        config = _load_agent_thinking_skill_config(
            app_root=app_root, agent_ref=ns.agent
        )
        selected = apply_skill_selector(
            bundles=bundles,
            selector=config.get("skills"),
            denied=config.get("denied_skills"),
        )
        active_skill_names = set(selected.keys())

        selector = config.get("skills")
        source = config.get("skills_source", "both")
        denied = config.get("denied_skills", [])
        print("\n=== Skill selector ===\n")
        print(f"agent: {ns.agent}")
        print(f"skills_source: {source}")
        print(f"skills: {selector!r}")
        print(f"denied_skills: {denied!r}")

    print("\n=== Skills ===\n")
    for skill_name in sorted(bundles.keys()):
        bundle = bundles[skill_name]
        source = bundle.get("source", "unknown")
        description = bundle.get("description", "")
        if ns.agent:
            status = "active" if skill_name in active_skill_names else "excluded"
            print(f"- {skill_name} [{source}] [{status}]")
        else:
            print(f"- {skill_name} [{source}]")
        if description:
            print(f"  {description}")
    print()


def _handle_skill_show_command(args: List[str], app_root: str = None) -> None:
    """Show one skill bundle in detail."""
    if app_root is None:
        app_root = os.getcwd()

    parser = argparse.ArgumentParser(prog="jvagent skill show")
    parser.add_argument("skill_name")
    parser.add_argument("--agent", default=None, help="namespace/agent_id")
    parser.add_argument(
        "--builtin",
        action="store_true",
        help="Include built-in skills when --agent is used",
    )
    ns = parser.parse_args(args)

    try:
        bundles = _resolve_skills_for_cli(
            app_root=app_root,
            agent_ref=ns.agent,
            include_builtin=ns.builtin,
        )
    except ValueError as exc:
        parser.error(str(exc))

    bundle = bundles.get(ns.skill_name)
    if not bundle:
        print(f"Skill not found: {ns.skill_name}")
        return

    print(f"\n=== Skill: {ns.skill_name} ===")
    print(f"Source: {bundle.get('source', 'unknown')}")
    print(f"Directory: {bundle.get('dir', '')}")
    print(f"Description: {bundle.get('description', '')}")
    allowed_tools = bundle.get("allowed_tools", [])
    print(f"Allowed tools: {', '.join(allowed_tools) if allowed_tools else '(all)'}")
    tool_files = bundle.get("tool_files", [])
    print(f"Tool files: {len(tool_files)}")
    if tool_files:
        for path in tool_files:
            print(f"  - {path}")
    content = bundle.get("content", "")
    if content:
        print("\n--- SKILL.md content ---\n")
        print(content)
    print()


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


def _handle_skill_validate_command(args: List[str], app_root: str = None) -> None:
    """Validate a SKILL.md file without requiring agent context."""
    from pathlib import Path as _Path

    parser = argparse.ArgumentParser(prog="jvagent skill validate")
    parser.add_argument(
        "path",
        help="Path to a SKILL.md file or a skill bundle directory containing one.",
    )
    ns = parser.parse_args(args)

    target = _Path(ns.path).expanduser().resolve()
    if target.is_dir():
        target = target / "SKILL.md"
    if not target.is_file():
        parser.error(f"SKILL.md not found at {target}")

    from jvagent.scaffold.skill_resolve import parse_skill_bundle

    print(f"Validating: {target}")
    bundle = parse_skill_bundle(target.parent, source="builtin")
    if bundle is None:
        print("FAILED: Could not parse SKILL.md")
        return

    print("PASSED (bundle parse)")
    print(f"  name:            {bundle['name']}")
    print(f"  description:     {bundle.get('description', '')[:80]}")
    print(f"  tools:           {len(bundle.get('tool_files', []))}")
    print(f"  requires_actions: {bundle.get('requires_actions', [])}")
    print(f"  exports:         {bundle.get('exports', [])}")
    print(f"  imports:         {bundle.get('imports', [])}")
    print(f"  allowed_tools:   {bundle.get('allowed_tools', [])}")

    from jvagent.action.interview._validate_contract import (
        validate_interview_skill_dir,
    )
    from jvagent.action.interview.spec import (
        load_interview_spec_from_skill,
    )

    if load_interview_spec_from_skill(target.parent) is not None:
        ok, issues = validate_interview_skill_dir(target.parent)
        if ok:
            print("PASSED (interview contract)")
        else:
            print("FAILED (interview contract)")
            for issue in issues:
                print(f"  - {issue}")

    from jvagent.action.leadgen._validate_contract import validate_leadgen_skill_dir
    from jvagent.action.leadgen.spec import load_leadgen_spec_from_skill

    if load_leadgen_spec_from_skill(target.parent) is not None:
        ok, issues = validate_leadgen_skill_dir(target.parent)
        if ok:
            print("PASSED (leadgen contract)")
        else:
            print("FAILED (leadgen contract)")
            for issue in issues:
                print(f"  - {issue}")


def handle_skill_command(args: List[str], app_root: str = None) -> None:
    """Handle skill bundle commands."""
    if app_root is None:
        app_root = os.getcwd()

    if not args:
        print(
            "Usage: jvagent skill <add|create-interview|create-leadgen|list|show|validate> ..."
        )
        return

    command = args[0]
    if command == "add":
        _handle_skill_add_command(args[1:], app_root=app_root)
        return
    if command == "create-interview":
        _handle_skill_create_interview_command(args[1:], app_root=app_root)
        return
    if command == "create-leadgen":
        _handle_skill_create_leadgen_command(args[1:], app_root=app_root)
        return
    if command == "list":
        _handle_skill_list_command(args[1:], app_root=app_root)
        return
    if command == "show":
        _handle_skill_show_command(args[1:], app_root=app_root)
        return
    if command == "validate":
        _handle_skill_validate_command(args[1:], app_root=app_root)
        return

    print(f"Unknown skill command: {command}")
    print(
        "Available commands: add, create-interview, create-leadgen, list, show, validate"
    )
