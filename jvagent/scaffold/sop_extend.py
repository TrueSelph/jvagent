"""SOP inheritance via ``extends`` frontmatter (ADR-0020).

Resolves ``action:<namespace>/<action>`` and ``skill:<name>`` refs and composes
base markdown onto skill bundle bodies at discovery time.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from jvagent.action.loader import info_yaml

logger = logging.getLogger(__name__)

MAX_EXTEND_DEPTH = 12

_CORE_ACTION_PATH: Optional[Path] = None
_CORE_PACKAGE_INDEX: Optional[Dict[str, Path]] = None


def _get_core_action_path() -> Optional[Path]:
    global _CORE_ACTION_PATH
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
    if _CORE_PACKAGE_INDEX is not None:
        return _CORE_PACKAGE_INDEX

    index: Dict[str, Path] = {}
    core_path = _get_core_action_path()
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
        return _load_skill_md_body(skill_file)
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

    return out


def reset_sop_extend_cache() -> None:
    """Clear memoized core action path/index (for tests)."""
    global _CORE_ACTION_PATH, _CORE_PACKAGE_INDEX
    _CORE_ACTION_PATH = None
    _CORE_PACKAGE_INDEX = None


__all__ = [
    "MAX_EXTEND_DEPTH",
    "compose_extended_sop_bodies",
    "compose_skill_body",
    "load_action_base_sop_body",
    "parse_extends_ref",
    "resolve_action_package_dir",
    "reset_sop_extend_cache",
]
