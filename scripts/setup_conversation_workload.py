#!/usr/bin/env python3
"""Create a fresh disposable workload app; never start models or modify a graph."""

from __future__ import annotations

import argparse
import json
import os
import secrets
from pathlib import Path

import yaml
from dotenv import dotenv_values


def create_fixture(
    directory: Path, implementation: str, key_env: Path, dsn_file: Path
) -> None:
    """Write the frozen declarations and private credentials into a new directory."""
    fixtures = Path(__file__).resolve().parents[1] / ".planning" / "fixtures"
    declarations = yaml.safe_load((fixtures / "conversation-apps.yaml").read_text())
    selected = declarations[implementation]
    key = dotenv_values(key_env).get("OPENAI_API_KEY")
    dsn = dsn_file.read_text().strip()
    if not key or not dsn.startswith(("postgres://", "postgresql://")):
        raise ValueError(
            "Supply an OpenAI key environment file and disposable PostgreSQL DSN file"
        )
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    agent_dir = directory / "agents" / "jvagent" / "bench"
    skill_dir = agent_dir / "skills" / "conversation"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        (fixtures / "conversation-SKILL.md").read_text()
    )
    (directory / "app.yaml").write_text(
        yaml.safe_dump(selected["app"], sort_keys=False)
    )
    agent_text = yaml.safe_dump(selected["agent"], sort_keys=False)
    (agent_dir / "agent.yaml").write_text(
        agent_text.replace("__APP_ROOT__", str(directory))
    )
    api, ui = (8132, 3132) if implementation == "legacy" else (8133, 3133)
    environment = {
        "OPENAI_API_KEY": key,
        "JVAGENT_ADMIN_USERNAME": "harness-bench",
        "JVAGENT_ADMIN_EMAIL": "bench@example.com",
        "JVAGENT_ADMIN_PASSWORD": secrets.token_urlsafe(24),
        "JVSPATIAL_JWT_SECRET_KEY": secrets.token_hex(32),
        "JVSPATIAL_DB_TYPE": "postgres",
        "JVSPATIAL_POSTGRES_DSN": dsn,
        "JVSPATIAL_POSTGRES_COMMAND_TIMEOUT": "10",
        "JVSPATIAL_POSTGRES_MIN_POOL_SIZE": "1",
        "JVSPATIAL_POSTGRES_MAX_POOL_SIZE": "5",
        "JVSPATIAL_LOG_DB_TYPE": "json",
        "JVAGENT_HOST": "127.0.0.1",
        "JVAGENT_PORT": str(api),
        "JVSPATIAL_CORS_ORIGINS": f"http://127.0.0.1:{ui}",
        "PYDANTIC_AI_NO_BANNER": "1",
    }
    path = directory / ".env"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        # JSON quoting preserves whitespace/newlines without shell expansion.
        for name, value in environment.items():
            stream.write(f"{name}={json.dumps(value, ensure_ascii=False)}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("implementation", choices=["legacy", "native"])
    parser.add_argument("directory", type=Path)
    parser.add_argument("--key-env", type=Path, required=True)
    parser.add_argument("--dsn-file", type=Path, required=True)
    args = parser.parse_args()
    create_fixture(args.directory, args.implementation, args.key_env, args.dsn_file)
    print(
        f"Created {args.implementation} fixture at {args.directory}; credentials are in its private .env"
    )


if __name__ == "__main__":
    main()
