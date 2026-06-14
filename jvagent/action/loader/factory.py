"""Factory helpers for action-loader instance construction."""

from typing import Any, Dict, List, Optional


def build_action_metadata_payload(
    *,
    metadata: Any,
    merged_config: Dict[str, Any],
    config_overrides: Optional[Dict[str, Any]],
    agent_name: str,
    agent_namespace: str = "",
    agent_dir: str = "",
    loaded_modules: List[str],
) -> Dict[str, Any]:
    """Build persisted action metadata payload for Action instances."""
    return {
        "name": metadata.name,
        "title": metadata.title,
        "namespace": metadata.namespace,
        "version": metadata.version,
        "module": metadata.module,
        "module_root": str(metadata.path),
        "class": metadata.class_name,
        "archetype": metadata.archetype,
        "author": metadata.author,
        "group": metadata.group,
        "type": metadata.type,
        "config": merged_config,
        "config_overrides": config_overrides or {},
        "dependencies": metadata.dependencies,
        "agent_name": agent_name,
        "agent_namespace": agent_namespace
        or getattr(metadata, "agent_namespace", "")
        or "",
        "agent_dir": agent_dir or "",
        "loaded_modules": loaded_modules,
        "is_core_action": bool(getattr(metadata, "is_core_action", False)),
        "core_module_path": getattr(metadata, "core_module_path", None),
        # ADR-0010: surface the raw manifest payload so
        # Action.get_manifest() can parse it lazily on first access.
        # ``None`` if no ``manifest:`` block was declared in info.yaml.
        "manifest": getattr(metadata, "manifest", None),
    }
