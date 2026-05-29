"""`jvagent app` subcommands (create, profile new)."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional

from jvagent import __version__ as jvagent_version
from jvagent.scaffold.operations import CreateAppContext, create_app

logger = logging.getLogger(__name__)

DEPLOYMENT_CHOICES = ("local", "aws-lambda", "azure-functions")


def _prompt(msg: str, default: Optional[str] = None) -> str:
    if default:
        line = input(f"{msg} [{default}]: ").strip()
        return line or default
    return input(f"{msg}: ").strip()


def _run_profile_new(app_root: Path, rest: List[str]) -> None:
    from jvagent.scaffold.profile_stub import write_profile_stub

    p = argparse.ArgumentParser(prog="jvagent app profile new")
    p.add_argument("name", help="Profile file name without .yaml")
    p.add_argument(
        "--extends",
        dest="extends_profile",
        help="Built-in or existing profile key to extend",
    )
    ns, unknown = p.parse_known_args(rest)
    if unknown:
        p.error(f"unknown arguments: {unknown}")
    write_profile_stub(app_root, ns.name, extends=ns.extends_profile)
    print(f"Wrote {app_root / 'profiles' / (ns.name + '.yaml')}")


def handle_app_command(args: List[str], *, default_cwd: str) -> None:
    """Dispatch ``jvagent app ...`` (does not assume app.yaml exists for ``create``)."""
    if not args:
        _print_app_usage()
        return

    sub = args[0]
    rest = args[1:]

    if sub == "create":
        _handle_app_create(rest, default_cwd=default_cwd)
    elif sub == "profile":
        if not rest:
            print("Usage: jvagent app profile new <name> [--extends PROFILE]")
            return
        if rest[0] != "new":
            print("Unknown app profile command. Use: jvagent app profile new <name>")
            return
        _run_profile_new(Path(default_cwd).resolve(), rest[1:])
    else:
        print(f"Unknown app command: {sub}")
        _print_app_usage()


def _print_app_usage() -> None:
    print(
        """
jvagent app — scaffold applications

  jvagent app create [options]
      --dir PATH              Output directory (default: cwd)
      --app-id ID             Application id (app.yaml app:)
      --title TEXT
      --description TEXT
      --author TEXT
      --email TEXT            Admin email in generated .env.example
      --version VER           Default 1.0.0
      --license TEXT          Default MIT
      --homepage URL
      --jvagent-version SPEC  Default ~<installed version>
      --deployment NAME       local | aws-lambda | azure-functions
      --profile NAME          Default action profile when agent has no @profile
      --agent SPEC            Repeatable: namespace/agent or namespace/agent@profile
      --action ID             Repeatable: extra stock action id (e.g. jvagent/foo)
      --no-copy-builtin-profiles
      --no-git
      --force                 Overwrite non-empty --dir
      --yes                   Non-interactive (fail if required flags missing)

  jvagent app profile new <name> [--extends PROFILE]
      Create profiles/<name>.yaml under the current app root.
"""
    )


def _handle_app_create(rest: List[str], *, default_cwd: str) -> None:
    parser = argparse.ArgumentParser(prog="jvagent app create")
    parser.add_argument("--dir", default=default_cwd, help="Output directory")
    parser.add_argument("--app-id", default=None)
    parser.add_argument("--title", default=None)
    parser.add_argument("--description", default=None)
    parser.add_argument("--author", default=None)
    parser.add_argument(
        "--email",
        default=None,
        dest="admin_email",
        help="Admin email written to generated .env.example",
    )
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--license", default="MIT")
    parser.add_argument("--homepage", default="https://example.com")
    parser.add_argument("--jvagent-version", default=None, dest="jvagent_spec")
    parser.add_argument(
        "--deployment",
        default="local",
        choices=DEPLOYMENT_CHOICES,
    )
    parser.add_argument(
        "--profile",
        default="executive",
        help=(
            "Default profile for agents without @profile. "
            "Builtins: executive (default), minimal, conversational, research, "
            "whatsapp_voice."
        ),
    )
    parser.add_argument(
        "--agent",
        action="append",
        dest="agents",
        default=[],
        help="namespace/agent or namespace/agent@profile (repeatable)",
    )
    parser.add_argument(
        "--action",
        action="append",
        dest="actions",
        default=[],
        help="Extra action id, e.g. jvagent/foo (repeatable)",
    )
    parser.add_argument(
        "--no-copy-builtin-profiles",
        action="store_true",
        help="Do not copy built-in profiles to profiles/builtin/",
    )
    parser.add_argument("--no-git", action="store_true", dest="no_git")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Non-interactive; required options must be passed",
    )

    ns = parser.parse_args(rest)

    out = Path(ns.dir).expanduser().resolve()
    use_tty = sys.stdin.isatty() and not ns.yes

    app_id = ns.app_id
    title = ns.title
    description = ns.description
    author = ns.author

    if use_tty:
        if not app_id:
            app_id = _prompt("Application id (app.yaml `app:`)", "my_jvagent_app")
        if not title:
            title = _prompt("Human-readable title", app_id.replace("_", " ").title())
        if not description:
            description = _prompt("Short description", f"{title} (jvagent app)")
        if not author:
            author = _prompt("Author / organization", "Unknown")
        if not ns.agents:
            raw = _prompt(
                "Agents (comma-separated namespace/agent or ns/agent@profile)",
                "jvagent/main_agent",
            )
            ns.agents = [a.strip() for a in raw.split(",") if a.strip()]
    else:
        if not app_id or not title or not description or not author:
            parser.error(
                "Non-interactive mode requires --app-id, --title, --description, --author"
            )
        if not ns.agents:
            parser.error("Non-interactive mode requires at least one --agent")

    jv_spec = ns.jvagent_spec or f"~{jvagent_version}"

    try:
        create_app(
            CreateAppContext(
                output_dir=out,
                app_id=app_id,
                title=title,
                description=description,
                author=author,
                version=ns.version,
                license=ns.license,
                homepage=ns.homepage,
                jvagent_spec=jv_spec,
                deployment=ns.deployment,
                agent_specs=list(ns.agents),
                default_profile=ns.profile,
                extra_action_flags=list(ns.actions or []),
                copy_builtin_profiles=not ns.no_copy_builtin_profiles,
                force=ns.force,
                init_git=not ns.no_git,
                admin_email=ns.admin_email,
            )
        )
    except (FileExistsError, FileNotFoundError, ValueError) as e:
        logger.error("%s", e)
        sys.exit(1)

    print(f"\nCreated jvagent app at {out}")
    print(
        "Next: cp .env.example .env, set JVAGENT_ADMIN_PASSWORD, then jvagent bootstrap"
    )
