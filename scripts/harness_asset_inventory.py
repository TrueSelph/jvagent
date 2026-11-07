#!/usr/bin/env python3
"""Read Git-tracked declarations without importing or executing their packages.

This checks source coverage, not native admission or runtime qualification.
Untracked deployments and generated/private assets are outside its scope.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

_KINDS = {
    "SKILL.md": "skills",
    "info.yaml": "action_manifests",
    "agent.yaml": "agent_declarations",
}
_OWNERS = {"jvagent/orchestrator", "jvagent/native_harness"}
_POLICY_FIELDS = (
    "allowed-tools",
    "disabled-tools",
    "requires-tools",
    "requires-actions",
    "allowed-channels",
    "denied-channels",
)


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _ordinary(repo: Path, name: str) -> Path:
    path = repo / name
    if not path.resolve(strict=True).is_relative_to(repo) or not stat.S_ISREG(
        path.lstat().st_mode
    ):
        raise ValueError(f"Tracked asset is not an ordinary contained file: {name}")
    return path


def _scope(name: str) -> str:
    if name.startswith("examples/"):
        return "app-example"
    if name.startswith("tests/"):
        return "test-fixture"
    return "package"


def _metadata(data: bytes, *, skill: bool) -> dict[str, Any]:
    text = data.decode("utf-8-sig")
    if skill:
        frontmatter = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
        if frontmatter is None:
            return {"metadata_error": "Missing explicit YAML frontmatter"}
        text = frontmatter[1]
    try:
        value = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError):
        return {"metadata_error": "Invalid YAML metadata"}
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        return {"metadata_error": "Metadata must contain named fields"}
    return value


def _literal(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _names(value: Any, *, single: bool = True) -> list[str] | None:
    if single and isinstance(value, str):
        return [value]
    if (
        isinstance(value, list)
        and len(value) <= 256
        and all(isinstance(v, str) for v in value)
    ):
        return value
    return None


def scan(repo: Path) -> dict[str, Any]:
    """Include ignored-but-tracked paths; never use an ignore-filtered walk."""
    repo = Path(
        subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "--show-toplevel"], text=True
        ).removesuffix("\n")
    ).resolve(strict=True)
    paths = sorted(
        subprocess.check_output(["git", "-C", str(repo), "ls-files", "--cached", "-z"])
        .decode("utf-8")
        .rstrip("\0")
        .split("\0")
    )
    paths = [name for name in paths if name]
    hashes: dict[str, str] = {}

    def file_hash(name: str) -> str:
        if name not in hashes:
            digest = hashlib.sha256()
            with _ordinary(repo, name).open("rb") as stream:
                for chunk in iter(lambda: stream.read(64 * 1024), b""):
                    digest.update(chunk)
            hashes[name] = "sha256:" + digest.hexdigest()
        return hashes[name]

    assets: dict[str, list[dict[str, Any]]] = {kind: [] for kind in _KINDS.values()}
    for name in paths:
        kind = _KINDS.get(Path(name).name)
        if kind is None:
            continue
        with _ordinary(repo, name).open("rb") as stream:
            data = stream.read(256_001)
        if len(data) > 256_000:
            raise ValueError(f"Tracked declaration exceeds metadata bounds: {name}")
        hashes[name] = _sha(data)
        metadata = _metadata(data, skill=kind == "skills")
        record: dict[str, Any] = {
            "path": name,
            "scope": _scope(name),
            "sha256": hashes[name],
        }
        if "metadata_error" in metadata:
            record["metadata_error"] = "Metadata requires explicit review"
        if kind == "skills":
            fields = [*_POLICY_FIELDS, *(k.replace("-", "_") for k in _POLICY_FIELDS)]
            record.update(
                name=_literal(metadata.get("name")),
                spec=_literal(metadata.get("spec")),
                extends=_literal(metadata.get("extends")),
                byte_order_mark=data.startswith(b"\xef\xbb\xbf"),
                policy_fields={
                    k: _names(metadata[k], single=False)
                    for k in fields
                    if k in metadata
                },
                metadata_keys=sorted(metadata),
            )
        elif kind == "action_manifests":
            package = metadata.get("package", {})
            if not isinstance(package, dict):
                package = {}
            record.update(
                name=_literal(package.get("name")),
                archetype=_literal(package.get("archetype")),
            )
        else:
            declared = metadata.get("actions", [])
            declarations = declared if isinstance(declared, list) else []
            record["agent"] = _literal(metadata.get("agent"))
            record["action_names"] = [
                _literal(item.get("action"))
                for item in declarations
                if isinstance(item, dict)
            ]
            record["execution_declarations"] = []
            for item in declarations:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("action"), str)
                    or item["action"] not in _OWNERS
                ):
                    continue
                context = item.get("context", {})
                if not isinstance(context, dict):
                    context = {}
                record["execution_declarations"].append(
                    {
                        "action": item["action"],
                        "setting_names": sorted(str(k) for k in context),
                        "skill_names": _names(context.get("skills")),
                        "skill_directory": _literal(context.get("skill_directory")),
                        "skill_directories": _names(context.get("skill_directories")),
                    }
                )
        # Bind all tracked companions, including resources and custom extensions.
        # A base Action SOP may cover its whole module; this is not an import graph.
        parent = Path(name).parent
        prefix = "" if parent == Path(".") else str(parent) + "/"
        companions = [
            (p, file_hash(p)) for p in paths if p.startswith(prefix) and p != name
        ]
        record["tracked_directory_files"] = len(companions)
        record["tracked_directory_digest"] = _sha(
            json.dumps(companions, ensure_ascii=False).encode("utf-8")
        )
        assets[kind].append(record)
    return {
        "schema": "jvagent.tracked-harness-assets.v1",
        "scope": "Git-tracked working-tree declarations and companions; untracked deployments are not covered",
        "qualification": "Source coverage only; no SDK preflight, execution or compatibility approval",
        "totals": {kind: len(values) for kind, values in assets.items()},
        "assets": assets,
    }


def main() -> int:
    """Print an inventory or refuse drift against a reviewed snapshot."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    try:
        result = scan(args.repo)
        if args.check is not None:
            if json.loads(args.check.read_text()) != result:
                print(
                    "Tracked asset inventory drift; regenerate and review the snapshot."
                )
                return 1
            print("Tracked asset inventory matches; runtime qualification is separate.")
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False))
    except (OSError, ValueError, subprocess.CalledProcessError):
        print(
            "Tracked asset inventory failed; check repository paths and declarations.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
