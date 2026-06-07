"""Resolve reusable skill bundles from built-in and app-local sources."""

from __future__ import annotations

import fnmatch
import importlib
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import yaml

from jvagent.scaffold.sop_extend import (
    compose_extended_sop_bodies,
    parse_extends_ref,
    resolve_action_package_dir,
)

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
    """Normalize requires-actions into a list of non-empty requirement specs.

    Each spec is an Action class name with an optional inline PEP 508-style
    version constraint (the comparison operator is the delimiter), e.g.
    ``CodeExecutionAction``, ``PageIndexAction>=2.0``, ``WebFetchAction==1.4.0``,
    ``GmailAction>=1.0,<2.0``. Specs are kept verbatim here; the orchestrator
    parses name/constraint and enforces both presence and version at assembly.
    """
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
    verbatim_final = bool(frontmatter.get("verbatim-final"))
    always_active = bool(frontmatter.get("always-active"))
    locked_in = bool(frontmatter.get("locked-in") or frontmatter.get("locked_in"))
    # Skill spec: ``jv`` (default — an SOP that references action/IA tools) or
    # ``claude`` (a standard Anthropic Agent Skills folder whose bundled scripts
    # the model runs via the code-execution substrate). Unknown values fall back
    # to ``jv`` so a typo never silently changes execution semantics.
    spec_raw = str(frontmatter.get("spec") or "jv").strip().lower()
    spec = spec_raw if spec_raw in ("jv", "claude") else "jv"
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
    coactivate_with = _normalize_string_list(
        frontmatter.get("coactivate-with"), skill_file, key="coactivate-with"
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

    interview_block = frontmatter.get("interview")
    if interview_block is not None and not isinstance(interview_block, dict):
        logger.warning(
            "Skill bundle %s frontmatter 'interview' must be a mapping; ignoring",
            skill_file,
        )
        interview_block = None

    extends_raw = frontmatter.get("extends")
    extends_value: Optional[str] = None
    if extends_raw is not None:
        parsed_extends = parse_extends_ref(extends_raw)
        if parsed_extends is not None:
            kind, target = parsed_extends
            extends_value = f"{kind}:{target}"
        else:
            logger.warning(
                "Skill bundle %s has invalid extends %r; ignoring",
                skill_file,
                extends_raw,
            )

    return {
        "name": name,
        "description": description,
        "content": content,
        "extends": extends_value,
        "interview": interview_block,
        "dir": str(skill_dir),
        "tool_files": tool_files,
        "allowed_tools": allowed_tools,
        "requires_actions": requires_actions,
        "requires_jvagent": requires_jvagent,
        "verbatim_final": verbatim_final,
        "always_active": always_active,
        "locked_in": locked_in,
        "spec": spec,
        "dispatch": dispatch,
        "exports": exports,
        "imports": imports,
        "coactivate_with": coactivate_with,
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
    """Resolve pure app-local skills from agents/<ns>/<agent>/skills/*."""
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
        if parsed.get("requires_actions"):
            primary = str(parsed["requires_actions"][0]).split(">=")[0].split("==")[0]
            logger.warning(
                "Skill '%s' in agents/%s/%s/skills/ declares requires-actions "
                "(%s). Action-backed skills belong under "
                "agents/.../actions/<namespace>/<action>/skills/<name>/",
                parsed["name"],
                namespace,
                agent_name,
                primary,
            )
        key = parsed["name"]
        discovered[key] = parsed
    return discovered


def resolve_agent_action_refs_from_yaml(
    app_root: str,
    namespace: str,
    agent_name: str,
) -> List[str]:
    """Read action refs from agent.yaml (synchronous, best-effort)."""
    agent_yaml = (
        Path(app_root).resolve() / "agents" / namespace / agent_name / "agent.yaml"
    )
    if not agent_yaml.is_file():
        return []
    try:
        with open(agent_yaml, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.debug("Failed to read %s: %s", agent_yaml, exc)
        return []
    if not isinstance(data, dict):
        return []
    refs: List[str] = []
    for item in data.get("actions") or []:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("action") or "").strip()
        if "/" in ref:
            refs.append(ref)
    return refs


def action_ref_from_metadata(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
    """Build ``namespace/action_name`` from action loader metadata (info.yaml)."""
    if not metadata:
        return None
    namespace = str(metadata.get("namespace") or "").strip()
    action_name = str(metadata.get("name") or "").strip()
    if namespace and action_name:
        return f"{namespace}/{action_name}"
    return None


def action_overlay_skills_dir(
    agent_base: Union[str, Path],
    action_ref: str,
) -> Optional[str]:
    """Return ``agents/.../actions/<ns>/<action>/skills`` when it exists."""
    ref = str(action_ref).strip()
    if "/" not in ref:
        return None
    namespace, action_name = ref.split("/", 1)
    skills_root = Path(agent_base) / "actions" / namespace / action_name / "skills"
    return str(skills_root) if skills_root.is_dir() else None


def legacy_agent_skills_dir(agent_base: Union[str, Path]) -> Optional[str]:
    """Deprecated agent-level ``agents/.../skills`` (pre ADR-0020 overlay)."""
    skills_root = Path(agent_base) / "skills"
    return str(skills_root) if skills_root.is_dir() else None


def resolve_action_skill_scan_dirs(
    metadata: Dict[str, Any],
    *,
    app_root: Optional[str] = None,
    agent_namespace: Optional[str] = None,
    agent_name: Optional[str] = None,
    include_legacy_agent_skills: bool = True,
) -> List[str]:
    """Directories an action-backed runtime should scan for per-skill packages.

    Order: action overlay ``skills/`` first, then legacy agent ``skills/``.
    Overlay paths require loader metadata (``namespace`` + ``name`` from
    ``info.yaml`` ``package.name``). Legacy agent ``skills/`` is still scanned
    when ``include_legacy_agent_skills`` is true (deprecated layout).
    """
    action_ref = action_ref_from_metadata(metadata)
    dirs: List[str] = []
    agent_bases: List[str] = []

    def _append(path: Optional[str]) -> None:
        if path and path not in dirs:
            dirs.append(path)

    agent_dir = metadata.get("agent_dir")
    if agent_dir:
        agent_bases.append(str(agent_dir))

    if app_root and agent_namespace and agent_name:
        agent_base = os.path.join(
            str(app_root), "agents", str(agent_namespace), str(agent_name)
        )
        if not agent_dir or os.path.normpath(str(agent_dir)) != os.path.normpath(
            agent_base
        ):
            agent_bases.append(agent_base)

    for base in agent_bases:
        if action_ref:
            _append(action_overlay_skills_dir(base, action_ref))
        if include_legacy_agent_skills:
            _append(legacy_agent_skills_dir(base))

    return dirs


def _scan_action_skills_dir(
    skills_root: Path,
    *,
    source: str,
    action_ref: str,
) -> Dict[str, Dict[str, Any]]:
    if not skills_root.is_dir():
        return {}
    discovered: Dict[str, Dict[str, Any]] = {}
    for skill_dir in sorted(skills_root.iterdir()):
        if not skill_dir.is_dir():
            continue
        parsed = parse_skill_bundle(skill_dir, source=source)
        if not parsed:
            continue
        parsed = dict(parsed)
        parsed["action_ref"] = action_ref
        discovered[parsed["name"]] = parsed
    return discovered


def resolve_core_action_skills(
    action_refs: Optional[List[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Discover skills under core action packages' ``skills/`` subdirs."""
    if not action_refs:
        return {}
    discovered: Dict[str, Dict[str, Any]] = {}
    for ref in action_refs:
        ref = str(ref).strip()
        if not ref.startswith("jvagent/"):
            continue
        action_dir = resolve_action_package_dir(ref)
        if action_dir is None:
            continue
        skills_root = action_dir / "skills"
        for name, bundle in _scan_action_skills_dir(
            skills_root, source="action", action_ref=ref
        ).items():
            discovered[name] = bundle
    return discovered


def resolve_agent_action_skills(
    app_root: str,
    namespace: str,
    agent_name: str,
    *,
    action_refs: Optional[List[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Discover app overlays at agents/.../actions/<ns>/<action>/skills/*."""
    actions_root = (
        Path(app_root).resolve() / "agents" / namespace / agent_name / "actions"
    )
    if not actions_root.is_dir():
        return {}

    discovered: Dict[str, Dict[str, Any]] = {}
    if action_refs:
        for ref in action_refs:
            ref = str(ref).strip()
            if "/" not in ref:
                continue
            ns_part, action_name = ref.split("/", 1)
            skills_root = actions_root / ns_part / action_name / "skills"
            for name, bundle in _scan_action_skills_dir(
                skills_root, source="app", action_ref=ref
            ).items():
                discovered[name] = bundle
        return discovered

    for ns_dir in sorted(actions_root.iterdir()):
        if not ns_dir.is_dir():
            continue
        for action_dir in sorted(ns_dir.iterdir()):
            if not action_dir.is_dir():
                continue
            action_ref = f"{ns_dir.name}/{action_dir.name}"
            skills_root = action_dir / "skills"
            for name, bundle in _scan_action_skills_dir(
                skills_root, source="app", action_ref=action_ref
            ).items():
                discovered[name] = bundle
    return discovered


def resolve_merged_skill_bundles(
    app_root: str,
    namespace: str,
    agent_name: str,
    *,
    include_builtin: bool = True,
    action_refs: Optional[List[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Resolve skills with deterministic precedence.

    Merge order: builtin pure → core action skills → app pure (overrides
    builtin) → app action overlays (overrides core action skills by name).
    """
    refs = action_refs
    if refs is None:
        refs = resolve_agent_action_refs_from_yaml(app_root, namespace, agent_name)

    merged: Dict[str, Dict[str, Any]] = {}
    if include_builtin:
        merged.update(resolve_builtin_skills())
    merged.update(resolve_core_action_skills(refs))

    app_local = resolve_agent_skills(app_root, namespace, agent_name)
    for skill_name, skill_data in app_local.items():
        if skill_name in merged:
            logger.info(
                "App-local skill '%s' overrides skill for %s/%s",
                skill_name,
                namespace,
                agent_name,
            )
        merged[skill_name] = skill_data

    app_action = resolve_agent_action_skills(
        app_root, namespace, agent_name, action_refs=refs
    )
    for skill_name, skill_data in app_action.items():
        if skill_name in merged:
            logger.info(
                "App action skill '%s' overrides skill for %s/%s",
                skill_name,
                namespace,
                agent_name,
            )
        merged[skill_name] = skill_data

    return compose_extended_sop_bodies(
        merged,
        app_root=app_root,
        agent_namespace=namespace,
        agent_name=agent_name,
    )


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

    Skills whose frontmatter declares ``always-active: true`` slip through
    every selector branch (including ``None``/empty) so foundational skills
    are always available to the agent regardless of the operator's explicit
    selector list. ``denied`` still applies to them — operators can opt out
    by adding the skill name to the deny list.
    """
    always_active = {
        name: data
        for name, data in bundles.items()
        if bool(data.get("always_active", False))
    }

    if selector is None or selector == [] or selector == "":
        kept = dict(always_active)
    elif selector == SELECTOR_ALL:
        kept = dict(bundles)
    elif isinstance(selector, list):
        patterns = [
            str(pattern).strip() for pattern in selector if str(pattern).strip()
        ]
        names = set()
        for pattern in patterns:
            names.update(fnmatch.filter(list(bundles.keys()), pattern))
        kept = {name: data for name, data in bundles.items() if name in names}
        # Always-active skills slip past selector filtering.
        for name, data in always_active.items():
            if name not in kept:
                kept[name] = data
    else:
        kept = dict(always_active)

    for pattern in denied or []:
        denied_names = fnmatch.filter(list(kept.keys()), str(pattern))
        for name in denied_names:
            kept.pop(name, None)

    return kept
