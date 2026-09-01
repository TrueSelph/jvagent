"""Resolve action profiles: extends, include, and merge into agent actions lists."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

import yaml

from jvagent.scaffold.resource_io import read_package_text

MAX_EXTEND_DEPTH = 12

_BUILTIN_PKG = "jvagent.scaffold.builtin_profiles"


def merge_action_lists(*lists: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Concatenate action lists; later entries with the same ``action`` id replace earlier."""
    flat: List[Dict[str, Any]] = []
    for lst in lists:
        for item in lst:
            if isinstance(item, dict) and "action" in item:
                flat.append(copy.deepcopy(item))
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for item in reversed(flat):
        aid = item.get("action")
        if not isinstance(aid, str) or aid in seen:
            continue
        seen.add(aid)
        out.append(item)
    out.reverse()
    return out


def _read_yaml_path(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def _read_builtin_yaml(name: str) -> Dict[str, Any]:
    resource = f"{name}.yaml"
    try:
        text = read_package_text(_BUILTIN_PKG, resource)
    except (FileNotFoundError, OSError, TypeError, UnicodeError, ModuleNotFoundError):
        raise FileNotFoundError(f"Unknown built-in profile: {name}") from None
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


def _resolve_profile_path(app_root: Optional[str], ref: str) -> Optional[Path]:
    """Resolve ``ref`` to an existing YAML file under app ``profiles/``."""
    if app_root is None:
        return None
    root = Path(app_root).resolve()
    candidates = [
        root / "profiles" / ref,
        root / "profiles" / f"{ref}.yaml",
        root / "profiles" / "builtin" / ref,
        root / "profiles" / "builtin" / f"{ref}.yaml",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _load_profile_doc(app_root: Optional[str], key: str) -> Dict[str, Any]:
    """Load a profile document by logical key (no extension)."""
    if app_root:
        path = _resolve_profile_path(app_root, key)
        if path is not None:
            return _read_yaml_path(path)

    try:
        return _read_builtin_yaml(key)
    except FileNotFoundError:
        pass

    raise FileNotFoundError(
        f"Profile {key!r} not found under app profiles/ and not a built-in profile"
    )


def _collect_includes(
    app_root: Optional[str],
    include_refs: List[Union[str, Any]],
    stack: List[str],
    depth: int,
) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    if not include_refs:
        return actions
    for ref in include_refs:
        if not isinstance(ref, str):
            continue
        sub = _resolve_profile_actions_inner(app_root, ref, stack, depth + 1)
        actions = merge_action_lists(actions, sub)
    return actions


def _resolve_profile_actions_inner(
    app_root: Optional[str],
    key: str,
    stack: List[str],
    depth: int,
) -> List[Dict[str, Any]]:
    if depth > MAX_EXTEND_DEPTH:
        raise ValueError(f"Profile extends depth exceeded ({MAX_EXTEND_DEPTH}): {key}")
    if key in stack:
        raise ValueError(f"Profile extend cycle detected: {' -> '.join(stack + [key])}")

    new_stack = stack + [key]
    doc = _load_profile_doc(app_root, key)
    merged: List[Dict[str, Any]] = []

    extends = doc.get("extends")
    if extends is not None:
        if not isinstance(extends, str):
            raise ValueError(f"extends must be a string in profile {key!r}")
        parent_actions = _resolve_profile_actions_inner(
            app_root, extends, new_stack, depth + 1
        )
        merged = merge_action_lists(merged, parent_actions)

    includes = doc.get("include") or []
    if includes:
        if not isinstance(includes, list):
            raise ValueError(f"include must be a list in profile {key!r}")
        inc_actions = _collect_includes(app_root, includes, new_stack, depth)
        merged = merge_action_lists(merged, inc_actions)

    own = doc.get("actions") or []
    if own and not isinstance(own, list):
        raise ValueError(f"actions must be a list in profile {key!r}")
    merged = merge_action_lists(merged, own)
    return merged


def resolve_profile_actions(
    app_root: Optional[str],
    profile_key: str,
    extra_actions: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Resolve a profile name to a full list of action assignment dicts.

    ``profile_key`` is a logical name (e.g. ``minimal``) resolved from
    ``app_root/profiles/`` then built-in package data.
    """
    base = _resolve_profile_actions_inner(app_root, profile_key, stack=[], depth=0)
    if extra_actions:
        return merge_action_lists(base, extra_actions)
    return base


def parse_agent_spec(spec: str) -> tuple[str, Optional[str]]:
    """Parse ``namespace/id`` or ``namespace/id@profile``."""
    spec = spec.strip()
    if not spec or "/" not in spec:
        raise ValueError(
            "Agent spec must be namespace/agent_id or namespace/agent_id@profile"
        )
    if "@" in spec:
        agent_part, profile = spec.rsplit("@", 1)
        agent_part = agent_part.strip()
        profile = profile.strip() or None
    else:
        agent_part = spec
        profile = None
    if "/" not in agent_part:
        raise ValueError("Agent spec must include namespace/agent_id")
    return agent_part, profile


def parse_extra_action_flags(action_strings: List[str]) -> List[Dict[str, Any]]:
    """Turn ``jvagent/foo`` CLI tokens into minimal action assignment dicts."""
    out: List[Dict[str, Any]] = []
    for s in action_strings:
        s = s.strip()
        if not s:
            continue
        out.append({"action": s, "context": {"enabled": True}})
    return out
