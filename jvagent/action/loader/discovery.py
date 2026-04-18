"""Discovery facades for ActionLoader decomposition."""

from pathlib import Path
from typing import Any, Dict, Optional

from . import core_discovery


class _LoaderShim:
    def __init__(self, *, base_path: Path, core_action_path: Optional[Path]) -> None:
        self.base_path = base_path
        self._core_action_path = core_action_path
        self._core_action_cache: Optional[Dict[str, Dict[str, Any]]] = None


def get_core_action_cache(
    *,
    action_root: Optional[Path],
    current_cache: Optional[Dict[str, Dict[str, Any]]],
) -> Dict[str, Dict[str, Any]]:
    """Return cached core action discovery map."""
    if current_cache is not None:
        return current_cache
    loader = _LoaderShim(base_path=Path.cwd(), core_action_path=action_root)
    return core_discovery.build_core_action_cache(loader)


def discover_single_core_action(namespace: str, name: str) -> Optional[Dict[str, Any]]:
    """Compatibility wrapper for a single core action lookup."""
    if namespace != "jvagent":
        return None

    loader = _LoaderShim(base_path=Path.cwd(), core_action_path=None)
    return core_discovery.build_core_action_cache(loader).get(name)
