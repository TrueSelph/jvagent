"""Load and validate Conversation Use Case Specification (CUCS) YAML files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Union

import yaml

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "schemas" / "use-case-v1.schema.json"
)
_SCHEMA_URI = "jvagent.use-case/v1"


def schema_path() -> Path:
    return _SCHEMA_PATH


def load_schema() -> Dict[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def load_use_case(path: Union[str, Path]) -> Dict[str, Any]:
    """Load a CUCS YAML file and validate it against the v1 JSON schema."""
    p = Path(path)
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{p}: expected mapping at root")
    if data.get("schema") != _SCHEMA_URI:
        raise ValueError(f"{p}: schema must be {_SCHEMA_URI!r}")
    for key in ("id", "title", "given", "turns"):
        if key not in data:
            raise ValueError(f"{p}: missing required key {key!r}")
    if not data["turns"]:
        raise ValueError(f"{p}: turns must be non-empty")
    # Full structural validation — the checks above give friendlier messages
    # for the common misses, but the schema is the contract.
    try:
        import jsonschema
    except ImportError:  # pragma: no cover - optional in minimal installs
        return data
    try:
        jsonschema.validate(data, load_schema())
    except jsonschema.ValidationError as exc:
        raise ValueError(f"{p}: schema validation failed: {exc.message}") from exc
    return data


def discover_use_cases(root: Union[str, Path]) -> list[Path]:
    """Return all *.yaml files under root (recursive), excluding stubs/."""
    base = Path(root)
    out: list[Path] = []
    for p in sorted(base.rglob("*.yaml")):
        if "stubs" in p.parts:
            continue
        out.append(p)
    return out
