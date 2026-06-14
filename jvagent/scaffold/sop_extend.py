"""SOP inheritance via ``extends`` frontmatter (ADR-0020).

Resolves ``action:<namespace>/<action>`` and ``skill:<name>`` refs and composes
base markdown onto skill bundle bodies at discovery time. Merges ``allowed-tools``
from action/skill extends chains (additive) with optional ``disabled-tools``.
"""

from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml

from jvagent.action.loader import info_yaml

logger = logging.getLogger(__name__)

MAX_EXTEND_DEPTH = 12

_CORE_ACTION_PATH: Optional[Path] = None
_CORE_PACKAGE_INDEX: Optional[Dict[str, Path]] = None
_CORE_ACTION_ENV = "JVAGENT_CORE_ACTION_PATH"


def _env_core_action_path() -> Optional[Path]:
    raw = str(os.environ.get(_CORE_ACTION_ENV) or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    if not path.is_dir() or not info_yaml.has_info_yaml_files(path):
        logger.warning(
            "sop_extend: %s=%s is not a valid action root", _CORE_ACTION_ENV, path
        )
        return None
    return path


def _get_core_action_path() -> Optional[Path]:
    global _CORE_ACTION_PATH, _CORE_PACKAGE_INDEX

    env_path = _env_core_action_path()
    if env_path is not None:
        if _CORE_ACTION_PATH != env_path:
            _CORE_ACTION_PATH = env_path
            _CORE_PACKAGE_INDEX = None
        return env_path

    if _CORE_ACTION_PATH is not None and _CORE_ACTION_PATH.exists():
        if info_yaml.has_info_yaml_files(_CORE_ACTION_PATH):
            return _CORE_ACTION_PATH
        _CORE_ACTION_PATH = None

    try:
        spec = importlib.util.find_spec("jvagent")
        if spec and spec.origin:
            action_path = Path(spec.origin).parent / "action"
            if action_path.is_dir() and info_yaml.has_info_yaml_files(action_path):
                _CORE_ACTION_PATH = action_path
                return action_path
    except Exception as exc:
        logger.debug("sop_extend: could not resolve jvagent action path: %s", exc)

    dev_path = Path(__file__).resolve().parent.parent / "action"
    if dev_path.is_dir() and info_yaml.has_info_yaml_files(dev_path):
        _CORE_ACTION_PATH = dev_path
        return dev_path

    return None


def _build_core_package_index() -> Dict[str, Path]:
    """Map ``jvagent/<action_name>`` → action package directory."""
    global _CORE_PACKAGE_INDEX
    # Resolve core path first: this invalidates _CORE_PACKAGE_INDEX when the
    # env-override path changes. Checking the cache before this would return a
    # stale index for the previous path.
    core_path = _get_core_action_path()
    if _CORE_PACKAGE_INDEX is not None:
        return _CORE_PACKAGE_INDEX

    index: Dict[str, Path] = {}
    if core_path:
        for info_file in core_path.rglob("info.yaml"):
            if "__pycache__" in info_file.parts or any(
                part.startswith("_") for part in info_file.parts[:-1]
            ):
                continue
            data = info_yaml.load_info_yaml(info_file)
            if not data:
                continue
            package = data.get("package", {})
            if not isinstance(package, dict):
                continue
            full_name = str(package.get("name") or "").strip()
            if "/" in full_name:
                index[full_name] = info_file.parent

    _CORE_PACKAGE_INDEX = index
    return index


def parse_extends_ref(raw: Any) -> Optional[Tuple[str, str]]:
    """Parse ``extends`` into (kind, target) where kind is ``action`` or ``skill``."""
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    if value.startswith("action:"):
        target = value[len("action:") :].strip()
        if "/" not in target:
            logger.warning("sop_extend: invalid action extends ref %r", value)
            return None
        return ("action", target)
    if value.startswith("skill:"):
        target = value[len("skill:") :].strip()
        if not target:
            logger.warning("sop_extend: invalid skill extends ref %r", value)
            return None
        return ("skill", target)
    logger.warning(
        "sop_extend: extends must use action: or skill: prefix (got %r)", value
    )
    return None


def _load_skill_md_frontmatter(skill_file: Path) -> Dict[str, Any]:
    """Parse YAML frontmatter from a SKILL.md file."""
    try:
        raw = skill_file.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("sop_extend: failed to read %s: %s", skill_file, exc)
        return {}
    if not raw.strip().startswith("---"):
        return {}
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        parsed = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        logger.warning("sop_extend: invalid frontmatter in %s: %s", skill_file, exc)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def load_action_frontmatter(
    action_ref: str,
    *,
    app_root: Optional[str] = None,
    agent_namespace: Optional[str] = None,
    agent_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Load frontmatter mapping from ``<action_dir>/SKILL.md``."""
    action_dir = resolve_action_package_dir(
        action_ref,
        app_root=app_root,
        agent_namespace=agent_namespace,
        agent_name=agent_name,
    )
    if action_dir is None:
        return {}
    skill_file = action_dir / "SKILL.md"
    if not skill_file.is_file():
        return {}
    return _load_skill_md_frontmatter(skill_file)


def _normalize_tool_name_list(raw_value: Any) -> List[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        value = raw_value.strip()
        return [value] if value else []
    if isinstance(raw_value, list):
        return [str(item).strip() for item in raw_value if str(item).strip()]
    return []


def _merge_allowed_tools_for_bundle(
    name: str,
    bundle: Mapping[str, Any],
    bundles: Mapping[str, Mapping[str, Any]],
    *,
    memo: Dict[str, List[str]],
    stack: List[str],
    app_root: Optional[str],
    agent_namespace: Optional[str],
    agent_name: Optional[str],
) -> List[str]:
    if name in memo:
        return memo[name]

    extends_raw = bundle.get("extends")
    child_add = _normalize_tool_name_list(bundle.get("allowed_tools_add"))
    disabled = set(_normalize_tool_name_list(bundle.get("disabled_tools")))

    base: List[str] = []
    if extends_raw:
        chain_key = str(extends_raw)
        if chain_key in stack:
            raise ValueError(
                f"extends cycle detected: {' -> '.join(stack + [chain_key])}"
            )
        parsed = parse_extends_ref(extends_raw)
        if parsed is not None:
            kind, target = parsed
            new_stack = stack + [chain_key]
            if kind == "action":
                fm = load_action_frontmatter(
                    target,
                    app_root=app_root,
                    agent_namespace=agent_namespace,
                    agent_name=agent_name,
                )
                base = _normalize_tool_name_list(fm.get("allowed-tools"))
            elif kind == "skill":
                parent = bundles.get(target)
                if parent is not None:
                    base = _merge_allowed_tools_for_bundle(
                        target,
                        parent,
                        bundles,
                        memo=memo,
                        stack=new_stack,
                        app_root=app_root,
                        agent_namespace=agent_namespace,
                        agent_name=agent_name,
                    )

    merged: List[str] = []
    seen: set = set()
    for tool in base + child_add:
        if tool in disabled or tool in seen:
            continue
        merged.append(tool)
        seen.add(tool)
    memo[name] = merged
    return merged


def merge_extends_allowed_tools(
    bundles: Dict[str, Dict[str, Any]],
    *,
    app_root: Optional[str] = None,
    agent_namespace: Optional[str] = None,
    agent_name: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Merge ``allowed-tools`` along ``extends`` chains for each bundle."""
    memo: Dict[str, List[str]] = {}
    out = dict(bundles)
    for name, bundle in bundles.items():
        if not bundle.get("extends"):
            continue
        try:
            merged = _merge_allowed_tools_for_bundle(
                name,
                bundle,
                bundles,
                memo=memo,
                stack=[],
                app_root=app_root,
                agent_namespace=agent_namespace,
                agent_name=agent_name,
            )
        except ValueError:
            raise
        except Exception as exc:
            logger.warning(
                "sop_extend: allowed-tools merge failed for %s: %s", name, exc
            )
            continue
        updated = dict(bundle)
        updated["allowed_tools"] = merged
        out[name] = updated
    return out


def _task_lock_from_extends_chain(
    bundle: Mapping[str, Any],
    bundles: Mapping[str, Mapping[str, Any]],
    *,
    stack: List[str],
    app_root: Optional[str],
    agent_namespace: Optional[str],
    agent_name: Optional[str],
) -> bool:
    """True if any ancestor in this bundle's ``extends`` chain declares task-lock.

    The chain may end at an action SKILL.md (``extends: action:…``) or another
    skill bundle (``extends: skill:…``). A skill that extends a task-lock
    action/skill is itself task-locked — that is how interview skills inherit the
    base interview procedure's turn-lock without each restating it.
    """
    extends_raw = bundle.get("extends")
    if not extends_raw:
        return False
    chain_key = str(extends_raw)
    if chain_key in stack:
        return False
    parsed = parse_extends_ref(extends_raw)
    if parsed is None:
        return False
    kind, target = parsed
    new_stack = stack + [chain_key]
    if kind == "action":
        fm = load_action_frontmatter(
            target,
            app_root=app_root,
            agent_namespace=agent_namespace,
            agent_name=agent_name,
        )
        if bool(fm.get("task-lock") or fm.get("task_lock")):
            return True
        # An action SKILL.md may itself extend another action/skill.
        return _task_lock_from_extends_chain(
            fm,
            bundles,
            stack=new_stack,
            app_root=app_root,
            agent_namespace=agent_namespace,
            agent_name=agent_name,
        )
    parent = bundles.get(target)
    if parent is None:
        return False
    if bool(parent.get("task_lock")):
        return True
    return _task_lock_from_extends_chain(
        parent,
        bundles,
        stack=new_stack,
        app_root=app_root,
        agent_namespace=agent_namespace,
        agent_name=agent_name,
    )


def inherit_extends_task_lock(
    bundles: Dict[str, Dict[str, Any]],
    *,
    app_root: Optional[str] = None,
    agent_namespace: Optional[str] = None,
    agent_name: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Propagate ``task-lock`` along ``extends`` chains.

    Without this, a skill that ``extends: action:jvagent/interview`` (whose base
    SKILL.md sets ``task-lock: true``) loads with ``task_lock=False`` — the
    orchestrator's turn-lock resolver then can't bind the active skill each turn,
    so the lock is silently dropped after the first turn.
    """
    out = dict(bundles)
    for name, bundle in bundles.items():
        if bool(bundle.get("task_lock")) or not bundle.get("extends"):
            continue
        try:
            inherited = _task_lock_from_extends_chain(
                bundle,
                bundles,
                stack=[],
                app_root=app_root,
                agent_namespace=agent_namespace,
                agent_name=agent_name,
            )
        except Exception as exc:
            logger.warning("sop_extend: task-lock inherit failed for %s: %s", name, exc)
            continue
        if inherited:
            updated = dict(out.get(name, bundle))
            updated["task_lock"] = True
            out[name] = updated
    return out


def _norm_companion_list(raw: Any) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [raw]
    return [str(c).strip() for c in raw if str(c).strip()]


def _lock_companions_from_chain(
    bundle: Mapping[str, Any],
    bundles: Mapping[str, Mapping[str, Any]],
    *,
    stack: List[str],
    app_root: Optional[str],
    agent_namespace: Optional[str],
    agent_name: Optional[str],
) -> List[str]:
    """Union of ``lock-companions`` along this bundle's ``extends`` chain."""
    own = _norm_companion_list(bundle.get("lock_companions"))
    base: List[str] = []
    extends_raw = bundle.get("extends")
    if extends_raw and str(extends_raw) not in stack:
        parsed = parse_extends_ref(extends_raw)
        if parsed is not None:
            kind, target = parsed
            new_stack = stack + [str(extends_raw)]
            if kind == "action":
                fm = load_action_frontmatter(
                    target,
                    app_root=app_root,
                    agent_namespace=agent_namespace,
                    agent_name=agent_name,
                )
                base = _norm_companion_list(
                    fm.get("lock-companions") or fm.get("lock_companions")
                ) + _lock_companions_from_chain(
                    fm,
                    bundles,
                    stack=new_stack,
                    app_root=app_root,
                    agent_namespace=agent_namespace,
                    agent_name=agent_name,
                )
            else:
                parent = bundles.get(target)
                if parent is not None:
                    base = _lock_companions_from_chain(
                        parent,
                        bundles,
                        stack=new_stack,
                        app_root=app_root,
                        agent_namespace=agent_namespace,
                        agent_name=agent_name,
                    )
    out: List[str] = []
    seen: set = set()
    for c in base + own:
        if c not in seen:
            out.append(c)
            seen.add(c)
    return out


def inherit_extends_lock_companions(
    bundles: Dict[str, Dict[str, Any]],
    *,
    app_root: Optional[str] = None,
    agent_namespace: Optional[str] = None,
    agent_name: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Merge ``lock-companions`` (additively) along ``extends`` chains."""
    out = dict(bundles)
    for name, bundle in bundles.items():
        if not bundle.get("extends"):
            continue
        try:
            merged = _lock_companions_from_chain(
                bundle,
                bundles,
                stack=[],
                app_root=app_root,
                agent_namespace=agent_namespace,
                agent_name=agent_name,
            )
        except Exception as exc:
            logger.warning(
                "sop_extend: lock-companions inherit failed for %s: %s", name, exc
            )
            continue
        if merged != _norm_companion_list(bundle.get("lock_companions")):
            updated = dict(out.get(name, bundle))
            updated["lock_companions"] = merged
            out[name] = updated
    return out


def _load_skill_md_body(skill_file: Path) -> str:
    raw = skill_file.read_text(encoding="utf-8")
    if raw.strip().startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return raw.strip()


def resolve_action_package_dir(
    action_ref: str,
    *,
    app_root: Optional[str] = None,
    agent_namespace: Optional[str] = None,
    agent_name: Optional[str] = None,
) -> Optional[Path]:
    """Resolve ``namespace/action_name`` to an action package directory."""
    ref = str(action_ref).strip()
    if "/" not in ref:
        return None
    namespace, action_name = ref.split("/", 1)
    if not namespace or not action_name:
        return None

    if namespace == "jvagent":
        return _build_core_package_index().get(ref)

    if app_root and agent_namespace and agent_name:
        overlay = (
            Path(app_root).resolve()
            / "agents"
            / agent_namespace
            / agent_name
            / "actions"
            / namespace
            / action_name
        )
        if overlay.is_dir():
            return overlay

    return None


def load_action_base_sop_body(
    action_ref: str,
    *,
    app_root: Optional[str] = None,
    agent_namespace: Optional[str] = None,
    agent_name: Optional[str] = None,
) -> str:
    """Load markdown body from ``<action_dir>/SKILL.md``."""
    action_dir = resolve_action_package_dir(
        action_ref,
        app_root=app_root,
        agent_namespace=agent_namespace,
        agent_name=agent_name,
    )
    if action_dir is None:
        logger.warning("sop_extend: action package not found for %s", action_ref)
        return ""
    skill_file = action_dir / "SKILL.md"
    if not skill_file.is_file():
        logger.warning(
            "sop_extend: no SKILL.md base SOP at %s for %s", skill_file, action_ref
        )
        return ""
    try:
        body = _load_skill_md_body(skill_file)
        logger.debug(
            "sop_extend: loaded base SOP for %s from %s", action_ref, skill_file
        )
        return body
    except OSError as exc:
        logger.warning("sop_extend: failed to read %s: %s", skill_file, exc)
        return ""


def compose_skill_body(base: str, custom: str) -> str:
    """Prepend base SOP to custom markdown."""
    base_text = (base or "").strip()
    custom_text = (custom or "").strip()
    if not base_text:
        return custom_text
    if not custom_text:
        return base_text
    return f"{base_text}\n\n{custom_text}"


def _resolve_extends_chain(
    extends_raw: Any,
    *,
    bundles: Mapping[str, Mapping[str, Any]],
    memo: Dict[str, str],
    stack: List[str],
    depth: int,
    app_root: Optional[str],
    agent_namespace: Optional[str],
    agent_name: Optional[str],
    raw_content_by_name: Mapping[str, str],
) -> str:
    if depth > MAX_EXTEND_DEPTH:
        raise ValueError(f"extends depth exceeded ({MAX_EXTEND_DEPTH})")

    parsed = parse_extends_ref(extends_raw)
    if parsed is None:
        return ""

    kind, target = parsed
    chain_key = f"{kind}:{target}"
    if chain_key in stack:
        raise ValueError(f"extends cycle detected: {' -> '.join(stack + [chain_key])}")

    if chain_key in memo:
        return memo[chain_key]

    new_stack = stack + [chain_key]
    parent_body = ""

    if kind == "action":
        parent_body = load_action_base_sop_body(
            target,
            app_root=app_root,
            agent_namespace=agent_namespace,
            agent_name=agent_name,
        )
    elif kind == "skill":
        bundle = bundles.get(target)
        if bundle is None:
            logger.warning("sop_extend: skill %r not found for extends", target)
            memo[chain_key] = ""
            return ""
        nested = bundle.get("extends")
        nested_base = ""
        if nested:
            nested_base = _resolve_extends_chain(
                nested,
                bundles=bundles,
                memo=memo,
                stack=new_stack,
                depth=depth + 1,
                app_root=app_root,
                agent_namespace=agent_namespace,
                agent_name=agent_name,
                raw_content_by_name=raw_content_by_name,
            )
        own = str(raw_content_by_name.get(target) or bundle.get("content") or "")
        parent_body = compose_skill_body(nested_base, own)

    memo[chain_key] = parent_body
    return parent_body


def compose_extended_sop_bodies(
    bundles: Dict[str, Dict[str, Any]],
    *,
    app_root: Optional[str] = None,
    agent_namespace: Optional[str] = None,
    agent_name: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Compose ``extends`` chains onto each bundle's ``content`` field."""
    raw_content = {name: str(b.get("content") or "") for name, b in bundles.items()}
    memo: Dict[str, str] = {}
    out = dict(bundles)

    for name, bundle in bundles.items():
        extends_raw = bundle.get("extends")
        if not extends_raw:
            continue
        try:
            base = _resolve_extends_chain(
                extends_raw,
                bundles=bundles,
                memo=memo,
                stack=[],
                depth=0,
                app_root=app_root,
                agent_namespace=agent_namespace,
                agent_name=agent_name,
                raw_content_by_name=raw_content,
            )
        except ValueError:
            raise
        except Exception as exc:
            logger.warning("sop_extend: compose failed for skill %s: %s", name, exc)
            continue
        updated = dict(bundle)
        updated["content"] = compose_skill_body(base, raw_content.get(name, ""))
        out[name] = updated

    merged = merge_extends_allowed_tools(
        out,
        app_root=app_root,
        agent_namespace=agent_namespace,
        agent_name=agent_name,
    )
    with_lock = inherit_extends_task_lock(
        merged,
        app_root=app_root,
        agent_namespace=agent_namespace,
        agent_name=agent_name,
    )
    return inherit_extends_lock_companions(
        with_lock,
        app_root=app_root,
        agent_namespace=agent_namespace,
        agent_name=agent_name,
    )


def reset_sop_extend_cache() -> None:
    """Clear memoized core action path/index (for tests)."""
    global _CORE_ACTION_PATH, _CORE_PACKAGE_INDEX
    _CORE_ACTION_PATH = None
    _CORE_PACKAGE_INDEX = None


__all__ = [
    "MAX_EXTEND_DEPTH",
    "compose_extended_sop_bodies",
    "inherit_extends_task_lock",
    "inherit_extends_lock_companions",
    "compose_skill_body",
    "load_action_base_sop_body",
    "load_action_frontmatter",
    "merge_extends_allowed_tools",
    "parse_extends_ref",
    "resolve_action_package_dir",
    "reset_sop_extend_cache",
]
