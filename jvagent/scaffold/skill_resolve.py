"""Resolve reusable skill bundles from built-in and app-local sources."""

from __future__ import annotations

import fnmatch
import importlib
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

BUILTIN_SKILLS_PACKAGE = "jvagent.skills"
SELECTOR_ALL = "-all"


def _parse_frontmatter(raw: str, skill_path: Path) -> Tuple[Dict[str, Any], str]:
    """Parse optional YAML frontmatter and return (meta, content)."""
    content = raw.strip()
    if not raw.startswith("---"):
        return {}, content

    parts = raw.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"Invalid frontmatter format in {skill_path}")

    parsed = yaml.safe_load(parts[1])
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise ValueError(f"Frontmatter must be a YAML mapping in {skill_path}")

    return parsed, parts[2].strip()


def _normalize_allowed_tools(raw_value: Any, skill_path: Path) -> List[str]:
    """Normalize allowed-tools into a list of non-empty tool names."""
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        value = raw_value.strip()
        return [value] if value else []
    if isinstance(raw_value, list):
        normalized = []
        for item in raw_value:
            value = str(item).strip()
            if value:
                normalized.append(value)
        return normalized
    logger.warning(
        "Skill bundle %s has invalid allowed-tools type: %s",
        skill_path,
        type(raw_value).__name__,
    )
    return []


def _normalize_requires_actions(raw_value: Any, skill_path: Path) -> List[str]:
    """Normalize requires-actions into a list of non-empty action type names."""
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        value = raw_value.strip()
        return [value] if value else []
    if isinstance(raw_value, list):
        normalized = []
        for item in raw_value:
            value = str(item).strip()
            if value:
                normalized.append(value)
        return normalized
    logger.warning(
        "Skill bundle %s has invalid requires-actions type: %s",
        skill_path,
        type(raw_value).__name__,
    )
    return []


def _normalize_requires_action_versions(
    raw_value: Any, skill_path: Path
) -> Dict[str, str]:
    """Normalize requires-action-versions to ``namespace/label`` -> constraint string."""
    if raw_value is None:
        return {}
    if isinstance(raw_value, dict):
        out: Dict[str, str] = {}
        for k, v in raw_value.items():
            ks = str(k).strip()
            vs = str(v).strip()
            if ks and vs:
                out[ks] = vs
        return out
    logger.warning(
        "Skill bundle %s has invalid requires-action-versions type: %s",
        skill_path,
        type(raw_value).__name__,
    )
    return {}


def _normalize_string_list(
    raw_value: Any, skill_path: Path, key: str = "list"
) -> List[str]:
    """Normalize a YAML value into a list of non-empty strings."""
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        value = raw_value.strip()
        return [value] if value else []
    if isinstance(raw_value, list):
        return [str(item).strip() for item in raw_value if str(item).strip()]
    logger.warning(
        "Skill bundle %s has invalid %s type: %s",
        skill_path,
        key,
        type(raw_value).__name__,
    )
    return []


def _normalize_response_mode(raw_value: Any, skill_path: Path) -> Optional[str]:
    """Normalize response-mode into 'publish', 'respond', or None (inherit)."""
    if raw_value is None:
        return None
    value = str(raw_value).strip().lower()
    if value in ("publish", "respond"):
        return value
    if value:
        logger.warning(
            "Skill bundle %s has invalid response-mode '%s'; expected 'publish' or 'respond'. "
            "Defaulting to inherit (None).",
            skill_path,
            raw_value,
        )
    return None


def _normalize_dispatch(raw_value: Any, skill_path: Path) -> Optional[Dict[str, Any]]:
    """Normalize the optional ``dispatch`` frontmatter block.

    Schema (all keys optional except ``tool``)::

        dispatch:
          tool: pageindex__search   # required: registered tool name
          arg: query                # parameter name to fill (default: "query")
          source: utterance         # utterance | interpretation (default: utterance)
          extra:                    # optional fixed kwargs merged into the call
            limit: 5

    Returns ``None`` when the block is absent or malformed; the engine then
    falls back to the standard model-driven loop. A non-empty ``tool`` is the
    only mandatory field — everything else has safe defaults so a one-line
    ``dispatch: { tool: foo }`` works.
    """
    if raw_value is None or raw_value == "":
        return None
    if not isinstance(raw_value, dict):
        logger.warning(
            "Skill bundle %s has invalid dispatch type: %s (expected mapping)",
            skill_path,
            type(raw_value).__name__,
        )
        return None
    tool = str(raw_value.get("tool") or "").strip()
    if not tool:
        logger.warning(
            "Skill bundle %s: dispatch.tool is required when dispatch is set",
            skill_path,
        )
        return None
    arg = str(raw_value.get("arg") or "query").strip() or "query"
    source = str(raw_value.get("source") or "utterance").strip().lower()
    if source not in {"utterance", "interpretation"}:
        logger.warning(
            "Skill bundle %s: dispatch.source must be 'utterance' or "
            "'interpretation' (got %r); defaulting to 'utterance'",
            skill_path,
            source,
        )
        source = "utterance"
    raw_extra = raw_value.get("extra")
    if isinstance(raw_extra, dict):
        extra: Dict[str, Any] = {str(k): v for k, v in raw_extra.items() if k}
    else:
        if raw_extra is not None:
            logger.warning(
                "Skill bundle %s: dispatch.extra must be a mapping; ignoring",
                skill_path,
            )
        extra = {}
    return {"tool": tool, "arg": arg, "source": source, "extra": extra}


def _normalize_plan_steps(raw_value: Any, skill_path: Path) -> List[str]:
    """Normalize plan-steps into a list of non-empty step descriptions."""
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        value = raw_value.strip()
        return [value] if value else []
    if isinstance(raw_value, list):
        normalized = []
        for item in raw_value:
            value = str(item).strip()
            if value:
                normalized.append(value)
        return normalized
    logger.warning(
        "Skill bundle %s has invalid plan-steps type: %s",
        skill_path,
        type(raw_value).__name__,
    )
    return []


def parse_skill_bundle(
    skill_dir: Path,
    *,
    source: str,
) -> Optional[Dict[str, Any]]:
    """Parse one skill directory into SkillInteractAction-compatible metadata."""
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        return None

    try:
        raw = skill_file.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to read %s: %s", skill_file, exc, exc_info=True)
        return None

    try:
        frontmatter, content = _parse_frontmatter(raw, skill_file)
    except Exception as exc:
        logger.warning("Failed parsing frontmatter for %s: %s", skill_file, exc)
        return None

    name = str(frontmatter.get("name") or skill_dir.name).strip()
    if not name:
        logger.warning("Skill bundle %s missing name", skill_file)
        return None
    if "name" not in frontmatter:
        logger.warning(
            "Skill bundle %s missing frontmatter 'name'; defaulting to folder name '%s'",
            skill_file,
            name,
        )

    description = str(frontmatter.get("description") or "").strip()
    if not description:
        description = "Standard operating procedure."
        logger.warning(
            "Skill bundle %s missing frontmatter 'description'; using default",
            skill_file,
        )

    allowed_tools = _normalize_allowed_tools(
        frontmatter.get("allowed-tools"), skill_file
    )
    requires_actions = _normalize_requires_actions(
        frontmatter.get("requires-actions"), skill_file
    )
    requires_jvagent = str(frontmatter.get("requires-jvagent") or "").strip()
    requires_action_versions = _normalize_requires_action_versions(
        frontmatter.get("requires-action-versions"), skill_file
    )
    response_mode = _normalize_response_mode(
        frontmatter.get("response-mode"), skill_file
    )
    verbatim_final = bool(frontmatter.get("verbatim-final"))
    plan_steps = _normalize_plan_steps(frontmatter.get("plan-steps"), skill_file)
    dispatch = _normalize_dispatch(frontmatter.get("dispatch"), skill_file)
    tags = frontmatter.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    # Skill chaining: exports and imports are lists of key names.
    exports = _normalize_string_list(
        frontmatter.get("exports"), skill_file, key="exports"
    )
    imports = _normalize_string_list(
        frontmatter.get("imports"), skill_file, key="imports"
    )
    scope_hint = ", ".join(str(tag) for tag in tags if str(tag).strip())
    if not scope_hint:
        scope_hint = description
    tool_files = [
        str(path)
        for path in sorted(skill_dir.rglob("*.py"))
        if path.is_file() and not path.name.startswith("_")
    ]

    # Parse skill-to-skill version constraints: {skill_name: ">=1.0"}
    raw_deps = frontmatter.get("dependencies")
    if isinstance(raw_deps, dict):
        dependencies = {str(k): str(v) for k, v in raw_deps.items() if k and v}
    else:
        dependencies = {}

    return {
        "name": name,
        "description": description,
        "content": content,
        "dir": str(skill_dir),
        "tool_files": tool_files,
        "allowed_tools": allowed_tools,
        "requires_actions": requires_actions,
        "requires_jvagent": requires_jvagent,
        "requires_action_versions": requires_action_versions,
        "response_mode": response_mode,
        "verbatim_final": verbatim_final,
        "plan_steps": plan_steps,
        "dispatch": dispatch,
        "exports": exports,
        "imports": imports,
        "scope_hint": scope_hint,
        "source": source,
        "metadata": {
            "version": frontmatter.get("version"),
            "license": frontmatter.get("license"),
            "tags": tags,
            "dependencies": dependencies,
        },
    }


def _resolve_builtin_root() -> Optional[Path]:
    """Resolve filesystem root for built-in skill package data."""
    try:
        module = importlib.import_module(BUILTIN_SKILLS_PACKAGE)
    except Exception as exc:
        logger.warning("Failed to import builtin skills package: %s", exc)
        return None

    module_file = getattr(module, "__file__", None)
    if not module_file:
        logger.warning(
            "Builtin skills package %s has no __file__", BUILTIN_SKILLS_PACKAGE
        )
        return None
    return Path(module_file).resolve().parent


def resolve_builtin_skills() -> Dict[str, Dict[str, Any]]:
    """Resolve built-in reusable skills shipped with jvagent."""
    root = _resolve_builtin_root()
    if root is None or not root.is_dir():
        return {}

    discovered: Dict[str, Dict[str, Any]] = {}
    for skill_dir in sorted(root.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name.startswith("_"):
            continue
        parsed = parse_skill_bundle(skill_dir, source="builtin")
        if not parsed:
            continue
        key = parsed["name"]
        discovered[key] = parsed
    return discovered


def resolve_agent_skills(
    app_root: str,
    namespace: str,
    agent_name: str,
) -> Dict[str, Dict[str, Any]]:
    """Resolve app-local skills from agents/<ns>/<agent>/skills/*."""
    skills_dir = Path(app_root).resolve() / "agents" / namespace / agent_name / "skills"
    if not skills_dir.is_dir():
        return {}

    discovered: Dict[str, Dict[str, Any]] = {}
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        parsed = parse_skill_bundle(skill_dir, source="app")
        if not parsed:
            continue
        key = parsed["name"]
        discovered[key] = parsed
    return discovered


def resolve_merged_skill_bundles(
    app_root: str,
    namespace: str,
    agent_name: str,
    *,
    include_builtin: bool = True,
) -> Dict[str, Dict[str, Any]]:
    """Resolve skills with deterministic precedence: app-local overrides built-in."""
    builtin = resolve_builtin_skills() if include_builtin else {}
    app_local = resolve_agent_skills(app_root, namespace, agent_name)

    merged = dict(builtin)
    for skill_name, skill_data in app_local.items():
        if skill_name in merged:
            logger.info(
                "App-local skill '%s' overrides built-in skill for %s/%s",
                skill_name,
                namespace,
                agent_name,
            )
        merged[skill_name] = skill_data
    return merged


def list_builtin_skill_names() -> List[str]:
    """Return sorted built-in skill names."""
    return sorted(resolve_builtin_skills().keys())


def list_agent_skill_names(app_root: str, namespace: str, agent_name: str) -> List[str]:
    """Return sorted app-local skill names for an agent."""
    return sorted(resolve_agent_skills(app_root, namespace, agent_name).keys())


def apply_skill_selector(
    bundles: Dict[str, Dict[str, Any]],
    selector: Any,
    denied: Optional[List[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Filter skill bundles by selector and deny patterns.

    Supported selector values:
    - ``"-all"``: keep all bundles
    - ``list[str]``: keep bundles whose names match any pattern via fnmatch
    - ``None`` / ``[]`` / ``""``: keep none
    """
    if selector is None or selector == [] or selector == "":
        return {}

    if selector == SELECTOR_ALL:
        kept = dict(bundles)
    elif isinstance(selector, list):
        patterns = [
            str(pattern).strip() for pattern in selector if str(pattern).strip()
        ]
        names = set()
        for pattern in patterns:
            names.update(fnmatch.filter(list(bundles.keys()), pattern))
        kept = {name: data for name, data in bundles.items() if name in names}
    else:
        return {}

    for pattern in denied or []:
        denied_names = fnmatch.filter(list(kept.keys()), str(pattern))
        for name in denied_names:
            kept.pop(name, None)

    return kept
