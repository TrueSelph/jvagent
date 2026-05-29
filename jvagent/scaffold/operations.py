"""Create app / add agent filesystem operations."""

from __future__ import annotations

import datetime
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from jvagent import __version__ as jvagent_package_version
from jvagent.scaffold.profile_resolve import (
    parse_agent_spec,
    parse_extra_action_flags,
    resolve_profile_actions,
)
from jvagent.scaffold.resource_io import list_package_names, read_package_text
from jvagent.scaffold.yaml_io import (
    append_agent_to_app_yaml,
    apply_agent_placeholders,
    write_agent_yaml,
)

logger = logging.getLogger(__name__)

_BUILTIN_PKG = "jvagent.scaffold.builtin_profiles"


def _default_agent_alias(agent_ref: str) -> str:
    part = agent_ref.split("/")[-1]
    return part.replace("_", " ").strip().title() or "Agent"


def _copy_builtin_profiles_to(dest_builtin_dir: Path) -> None:
    dest_builtin_dir.mkdir(parents=True, exist_ok=True)
    for name in list_package_names(_BUILTIN_PKG, suffix=".yaml"):
        text = read_package_text(_BUILTIN_PKG, name)
        (dest_builtin_dir / name).write_text(text, encoding="utf-8")


def _write_profiles_readme(profiles_dir: Path) -> None:
    readme = profiles_dir / "README.md"
    readme.write_text(
        """# Action profiles (authoring)

Profiles are YAML files used by `jvagent app create` and `jvagent agent create` to build
`agents/*/agent.yaml` **action lists**. At runtime jvagent reads **agent.yaml** only; edit
profiles to regenerate or hand-edit `agent.yaml` afterward.

## Resolution order

For a profile key `my_profile`, the scaffold looks for (first hit wins):

1. `profiles/my_profile.yaml`
2. `profiles/builtin/my_profile.yaml`
3. Built-in profiles shipped with the `jvagent` package (`minimal`, `conversational`, …)

## Schema

- **`extends`**: string name of another profile to merge (parent first, then this file).
- **`include`**: list of paths under `profiles/` to merge in order.
- **`actions`**: list of maps with `action` and optional `context` / `config` (same shape as in `agent.yaml`).

Later definitions of the same `action` id override earlier ones.

## Examples

See `examples/custom.yaml`. Copy it to `profiles/<name>.yaml` and reference it as
`namespace/agent_id@name` when creating agents.

After changing profiles, update an existing agent by editing `agent.yaml` or re-running scaffold
with care (overwrites agent yaml when using `--force`).
""",
        encoding="utf-8",
    )


def _write_profiles_example(profiles_dir: Path) -> None:
    examples = profiles_dir / "examples"
    examples.mkdir(parents=True, exist_ok=True)
    (examples / "custom.yaml").write_text(
        """# Example custom profile — copy to ../my_team.yaml and use @my_team on agent create
extends: minimal

actions:
  - action: jvagent/intro_interact_action
    context:
      enabled: true
      description: Optional intro flow on top of minimal stack
""",
        encoding="utf-8",
    )


def _write_gitignore(app_root: Path) -> None:
    (app_root / ".gitignore").write_text(
        """# Local secrets — never commit
.env

# Runtime data
jvagent_db/
*_pageindex_db/
jvagent_logs/
.files/
jvspatial_logs/

# Optional local deploy overrides (keep deploy.example.yaml in git)
deploy.yaml
""",
        encoding="utf-8",
    )


def _write_license_mit(app_root: Path, author: str, year: str) -> None:
    (app_root / "LICENSE").write_text(
        f"""MIT License

Copyright (c) {year} {author}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
""",
        encoding="utf-8",
    )


def _write_docs_architecture(app_root: Path, app_title: str) -> None:
    docs = app_root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "architecture.md").write_text(
        f"""# Architecture — {app_title}

This application is a [jvagent](https://github.com/trueselph/jvagent) deployment on
[jvspatial](https://github.com/trueselph/jvspatial): a graph of **App**, **Agent**, and **Action**
nodes loaded from `app.yaml` and `agents/*/agent.yaml`.

## Layout

- `app.yaml` — application id, defaults, and the list of agent references.
- `agents/<namespace>/<agent_id>/agent.yaml` — per-agent actions and context.
- `profiles/` — optional YAML profiles used when scaffolding agents (authoring only).

## Serverless

See [jvspatial serverless-mode documentation](https://github.com/trueselph/jvspatial/blob/main/docs/md/serverless-mode.md)
for `SERVERLESS_MODE`, deferred tasks, and AWS Lambda / LWA notes. Azure Functions and other
providers may detect as serverless but deferred scheduling may require a custom task scheduler.
""",
        encoding="utf-8",
    )


def _build_app_dict(ctx: "CreateAppContext") -> Dict[str, Any]:
    agents_list = [parse_agent_spec(s)[0] for s in ctx.agent_specs]

    return {
        "app": ctx.app_id,
        "version": ctx.version,
        "author": ctx.author,
        "jvagent": ctx.jvagent_spec,
        "context": {
            "name": ctx.title,
            "description": ctx.description,
            "timezone": "America/New_York",
        },
        "license": ctx.license,
        "homepage": ctx.homepage,
        "tags": ["jvagent", "scaffold"],
        "config": {
            "server": {
                "title": f"{ctx.title} API",
                "description": f"API for {ctx.title}",
                "version": ctx.version,
            },
            "auth": {
                "enabled": True,
                "jwt_expire_minutes": 60,
                "exempt_paths": ["/health", "/docs", "/openapi.json"],
            },
            "interact": {
                "rate_limit_per_minute": 60,
                "max_utterance_length": 2000,
            },
            "cors": {
                "enabled": True,
                "origins": "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173",
            },
            "performance": {
                "enable_profiling": False,
                "enable_agent_cache": True,
                "agent_cache_ttl": 300,
                "enable_action_cache": True,
                "action_cache_ttl": 60,
                "enable_deferred_saves": True,
                "cache_cleanup_probability": 0.1,
                "enable_interact_router_cache": False,
                "interact_router_cache_ttl": 45,
            },
        },
        "agents": agents_list,
    }


def _write_packaged_env_example(
    app_root: Path, admin_email: Optional[str] = None
) -> None:
    text = read_package_text("jvagent.scaffold.static", "env.example.txt")
    if admin_email:
        text = text.replace(
            "JVAGENT_ADMIN_EMAIL=admin@jvagent.example",
            f"JVAGENT_ADMIN_EMAIL={admin_email}",
        )
    (app_root / ".env.example").write_text(text, encoding="utf-8")


def _patch_env_for_deployment(app_root: Path, deployment: str) -> None:
    if deployment != "aws-lambda":
        return
    p = app_root / ".env.example"
    extra = """

# --- AWS Lambda / serverless (jvspatial) ---
# SERVERLESS_MODE=true
# JVSPATIAL_DEFERRED_TASK_PROVIDER=aws
# AWS_LAMBDA_FUNCTION_NAME=your-function-name
# AWS_LWA_PASS_THROUGH_PATH=/api/_internal/deferred
# JVSPATIAL_FILES_ROOT_PATH=/tmp/.files
"""
    if p.is_file():
        p.write_text(p.read_text(encoding="utf-8") + extra, encoding="utf-8")


def _write_readme_app(app_root: Path, ctx: "CreateAppContext") -> None:
    agents_md = "\n".join(f"- `{s}`" for s in ctx.agent_specs) or "- _(none)_"
    (app_root / "README.md").write_text(
        f"""# {ctx.title}

{ctx.description}

Scaffolded with `jvagent app create`. Agents:

{agents_md}

## Quickstart

1. `cp .env.example .env` and set at least `JVAGENT_ADMIN_PASSWORD` and any API keys your actions need.
2. `jvagent bootstrap`
3. `jvagent run`

## Adding agents later

```text
jvagent agent create your_ns/new_agent@minimal
jvagent bootstrap --update
```

## Profiles

Authoring profiles live in `profiles/`. See `profiles/README.md`.

## jvspatial serverless

See [serverless-mode](https://github.com/trueselph/jvspatial/blob/main/docs/md/serverless-mode.md).
""",
        encoding="utf-8",
    )


def _write_deploy_example(app_root: Path, ctx: "CreateAppContext") -> None:
    if ctx.deployment != "aws-lambda":
        return
    (app_root / "deploy.example.yaml").write_text(
        f"""# Example jvdeploy / Lambda layout — replace account IDs, subnets, secrets.
app:
  name: {ctx.app_id}
  version: "{ctx.version}"
region: us-east-1
# account_id: "000000000000"
lambda:
  memory_mb: 1024
  timeout_seconds: 120
  # environment: {{ }}
# VPC / EFS optional
""",
        encoding="utf-8",
    )


def _write_dockerfile_example(app_root: Path) -> None:
    (app_root / "Dockerfile").write_text(
        """# Generated scaffold — adjust base image and pip install for your registry.
FROM python:3.11-slim

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir jvagent jvspatial

# Install action extras from agents/*/actions/**/info.yaml in your CI or bundle step.
# RUN jvagent bundle  # generates Dockerfile layers when using jvagent bundle

ENV JVAGENT_APP_ID=""
EXPOSE 8000
CMD ["jvagent", "run", "--host", "0.0.0.0", "--port", "8000"]
""",
        encoding="utf-8",
    )


def _has_skill_interact_action(actions: List[Dict[str, Any]]) -> bool:
    for item in actions:
        if not isinstance(item, dict):
            continue
        if item.get("action") == "jvagent/skill_interact_action":
            return True
    return False


def _inject_skill_defaults(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ensure scaffolded skill actions opt into full skill exposure."""
    for item in actions:
        if not isinstance(item, dict):
            continue
        if item.get("action") != "jvagent/skill_interact_action":
            continue

        context = item.get("context")
        if not isinstance(context, dict):
            context = {}
            item["context"] = context

        context.setdefault("skills", "-all")
        context.setdefault("skills_source", "both")

    return actions


def _write_starter_skills_bundle(agent_dir: Path) -> None:
    skills_dir = agent_dir / "skills"
    example_dir = skills_dir / "example_skill"
    skills_dir.mkdir(parents=True, exist_ok=True)
    example_dir.mkdir(parents=True, exist_ok=True)

    (skills_dir / "README.md").write_text(
        """# Skills

Create one subfolder per Claude-style skill bundle:

```
skills/<skill_name>/SKILL.md
```

Optional Python tools can live beside `SKILL.md` and are activated when the
agent calls `read_skill` for that bundle.
""",
        encoding="utf-8",
    )

    (example_dir / "SKILL.md").write_text(
        """---
name: example_skill
description: A starter skill bundle for the thinking interact action.
allowed-tools: []
---

## Workflow

1. Understand the user request.
2. Call relevant tools to gather evidence.
3. Return a concise, actionable response.
""",
        encoding="utf-8",
    )


@dataclass
class CreateAppContext:
    output_dir: Path
    app_id: str
    title: str
    description: str
    author: str
    version: str = "1.0.0"
    license: str = "MIT"
    homepage: str = "https://example.com"
    jvagent_spec: str = field(default_factory=lambda: f"~{jvagent_package_version}")
    deployment: str = "local"  # local | aws-lambda | azure-functions
    agent_specs: List[str] = field(default_factory=list)
    default_profile: str = "executive"
    extra_action_flags: List[str] = field(default_factory=list)
    copy_builtin_profiles: bool = True
    force: bool = False
    init_git: bool = True
    admin_email: Optional[str] = None
    year: str = field(default_factory=lambda: str(datetime.datetime.now().year))


def create_app(ctx: CreateAppContext) -> None:
    root = ctx.output_dir.resolve()
    if root.exists() and any(root.iterdir()) and not ctx.force:
        raise FileExistsError(f"Directory not empty (use --force): {root}")
    root.mkdir(parents=True, exist_ok=True)

    profiles_dir = root / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    _write_profiles_readme(profiles_dir)
    _write_profiles_example(profiles_dir)
    if ctx.copy_builtin_profiles:
        _copy_builtin_profiles_to(profiles_dir / "builtin")

    _write_gitignore(root)
    _write_license_mit(root, ctx.author, ctx.year)
    _write_docs_architecture(root, ctx.title)
    _write_packaged_env_example(root, admin_email=ctx.admin_email)
    _patch_env_for_deployment(root, ctx.deployment)
    if ctx.deployment == "azure-functions":
        (root / ".env.example").write_text(
            (root / ".env.example").read_text(encoding="utf-8")
            + "\n# Azure: FUNCTIONS_WORKER_RUNTIME may set serverless mode; deferred tasks may need a custom scheduler.\n",
            encoding="utf-8",
        )

    app_dict = _build_app_dict(ctx)
    with open(root / "app.yaml", "w", encoding="utf-8") as f:
        f.write("# jvagent application — generated by scaffold\n\n")
        yaml.safe_dump(
            app_dict,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        )

    extras = parse_extra_action_flags(ctx.extra_action_flags)
    agents_root = root / "agents"
    agents_root.mkdir(parents=True, exist_ok=True)

    for spec in ctx.agent_specs:
        agent_ref, prof = parse_agent_spec(spec)
        profile_key = prof or ctx.default_profile
        ns, aid = agent_ref.split("/", 1)
        actions = resolve_profile_actions(str(root), profile_key, extras)
        actions = apply_agent_placeholders(
            actions,
            _default_agent_alias(agent_ref),
            f"{ctx.title} — {_default_agent_alias(agent_ref)}",
        )
        actions = _inject_skill_defaults(actions)
        agent_dir = agents_root / ns / aid
        agent_dir.mkdir(parents=True, exist_ok=True)
        write_agent_yaml(
            agent_dir / "agent.yaml",
            agent_ref=agent_ref,
            author=ctx.author,
            version=ctx.version,
            jvagent_version=ctx.jvagent_spec,
            alias=_default_agent_alias(agent_ref),
            description=f"Agent {_default_agent_alias(agent_ref)} for {ctx.title}",
            actions=actions,
        )
        (agent_dir / "README.md").write_text(
            f"# {_default_agent_alias(agent_ref)}\n\n"
            f"Defined as `{agent_ref}`. Edit `agent.yaml` to change actions.\n",
            encoding="utf-8",
        )
        if _has_skill_interact_action(actions):
            _write_starter_skills_bundle(agent_dir)

    _write_readme_app(root, ctx)
    if ctx.deployment == "aws-lambda":
        _write_deploy_example(root, ctx)
        _write_dockerfile_example(root)

    if ctx.init_git:
        try:
            subprocess.run(
                ["git", "init"],
                cwd=str(root),
                check=False,
                capture_output=True,
            )
        except OSError:
            logger.warning("git init skipped (git not available)")


@dataclass
class CreateAgentContext:
    app_root: Path
    agent_spec: str
    default_profile: str = "executive"
    extra_action_flags: List[str] = field(default_factory=list)
    force: bool = False
    author: Optional[str] = None
    version: str = "1.0.0"
    jvagent_spec: str = field(default_factory=lambda: f"~{jvagent_package_version}")


def create_agent_in_app(ctx: CreateAgentContext) -> None:
    app_root = ctx.app_root.resolve()
    app_yaml = app_root / "app.yaml"
    if not app_yaml.is_file():
        raise FileNotFoundError(f"app.yaml not found in {app_root}")

    with open(app_yaml, "r", encoding="utf-8") as f:
        app_data = yaml.safe_load(f)
    app_author = (
        ctx.author
        or (app_data.get("author") if isinstance(app_data, dict) else None)
        or "Unknown"
    )
    app_title = (
        (app_data.get("context") or {}).get("name")
        if isinstance(app_data, dict)
        else None
    ) or "Application"

    agent_ref, prof = parse_agent_spec(ctx.agent_spec)
    profile_key = prof or ctx.default_profile
    ns, aid = agent_ref.split("/", 1)

    agents_list = app_data.get("agents") if isinstance(app_data, dict) else None
    if not isinstance(agents_list, list):
        agents_list = []

    agent_dir = app_root / "agents" / ns / aid
    agent_yaml_path = agent_dir / "agent.yaml"
    if agent_ref in agents_list and agent_yaml_path.is_file() and not ctx.force:
        raise ValueError(
            f"Agent {agent_ref!r} is already listed in app.yaml and "
            f"{agent_yaml_path} exists (use --force to overwrite)"
        )

    if agent_dir.exists() and any(agent_dir.iterdir()) and not ctx.force:
        raise FileExistsError(f"Agent directory exists (use --force): {agent_dir}")

    extras = parse_extra_action_flags(ctx.extra_action_flags)
    actions = resolve_profile_actions(str(app_root), profile_key, extras)
    actions = apply_agent_placeholders(
        actions,
        _default_agent_alias(agent_ref),
        f"{app_title} — {_default_agent_alias(agent_ref)}",
    )
    actions = _inject_skill_defaults(actions)

    agent_dir.mkdir(parents=True, exist_ok=True)
    write_agent_yaml(
        agent_dir / "agent.yaml",
        agent_ref=agent_ref,
        author=app_author,
        version=ctx.version,
        jvagent_version=ctx.jvagent_spec,
        alias=_default_agent_alias(agent_ref),
        description=f"Agent {_default_agent_alias(agent_ref)} for {app_title}",
        actions=actions,
    )
    (agent_dir / "README.md").write_text(
        f"# {_default_agent_alias(agent_ref)}\n\n"
        f"Defined as `{agent_ref}`. Edit `agent.yaml` to change actions.\n",
        encoding="utf-8",
    )
    if _has_skill_interact_action(actions):
        _write_starter_skills_bundle(agent_dir)

    if agent_ref not in agents_list:
        append_agent_to_app_yaml(app_yaml, agent_ref, force=ctx.force)
    elif ctx.force:
        # Already registered; files were refreshed above
        pass
