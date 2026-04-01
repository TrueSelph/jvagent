"""Import hook for jvagent.actions.* module resolution.

Invariants (keep in sync with ``ActionLoader`` and app layout):

- Only module names under ``jvagent.actions.`` are handled; all others are ignored.
- ``ActionLoader.__init__`` sets ``_actions_importer_base_path`` to the app root before
  loading actions; the finder resolves paths under ``{base}/agents/``.
- Layout: ``agents/{agent_ns}/{agent_name}/actions/{action_ns}/{action_name}/`` with
  optional ``__init__.py`` or ``{module}.py`` for Python submodules.
- The hook is registered at import time (``sys.meta_path``); tests that load multiple
  app roots must not assume the base path is process-global without resetting the loader.
"""

import importlib
import importlib.abc
import importlib.util
import sys
import types
from pathlib import Path
from typing import Callable, Optional, Sequence, Union

# Module path prefix for app-loaded actions (custom actions in app's agents/ directory).
# Format: jvagent.actions.{agent_ns}.{agent_name}.{action_ns}.{action_name}
_ACTIONS_PREFIX = "jvagent.actions."

# Global base path for the importer, set by ActionLoader.__init__
_actions_importer_base_path: Optional[Path] = None


class JvagentActionsImporter(importlib.abc.MetaPathFinder):
    """Import hook that resolves jvagent.actions.* to the app directory's agents/ tree.

    Used when jvagent is installed as a pip package and the app directory (e.g. iris_ai)
    is the deployment target. Custom actions live under {base_path}/agents/ and are
    exposed as jvagent.actions.{agent_ns}.{agent_name}.{action_ns}.{action_name}.

    Supports lazy base_path via a callable for early registration.
    """

    def __init__(self, base_path: Union[Path, Callable[[], Optional[Path]]]):
        self._base_path = base_path

    def find_spec(
        self,
        fullname: str,
        path: Optional[Sequence[str]],
        target: Optional[types.ModuleType] = None,
    ) -> Optional[importlib.machinery.ModuleSpec]:
        if not fullname.startswith(_ACTIONS_PREFIX):
            return None

        base_path = self._base_path() if callable(self._base_path) else self._base_path
        if base_path is None:
            return None

        agents_path = base_path / "agents"
        if not agents_path.exists() or not agents_path.is_dir():
            return None

        rest = fullname[len(_ACTIONS_PREFIX) :]

        if rest == "":
            spec = importlib.machinery.ModuleSpec(
                fullname, loader=None, is_package=True
            )
            spec.submodule_search_locations = [str(agents_path)]
            return spec

        parts = rest.split(".")
        if len(parts) < 1:
            return None

        if len(parts) == 1:
            dir_path = agents_path / parts[0]
        elif len(parts) == 2:
            dir_path = agents_path / parts[0] / parts[1] / "actions"
        elif len(parts) == 3:
            dir_path = agents_path / parts[0] / parts[1] / "actions" / parts[2]
        elif len(parts) == 4:
            dir_path = (
                agents_path / parts[0] / parts[1] / "actions" / parts[2] / parts[3]
            )
        else:
            action_dir = (
                agents_path / parts[0] / parts[1] / "actions" / parts[2] / parts[3]
            )
            submodule = ".".join(parts[4:])
            module_file = action_dir / f"{parts[4]}.py"
            if len(parts) == 5 and module_file.exists():
                return importlib.util.spec_from_file_location(
                    fullname, module_file, submodule_search_locations=[str(action_dir)]
                )
            subpath = action_dir / parts[4]
            if len(parts) == 5:
                init = subpath / "__init__.py"
                if subpath.is_dir() and init.exists():
                    return importlib.util.spec_from_file_location(
                        fullname, init, submodule_search_locations=[str(subpath)]
                    )
                if module_file.exists():
                    return importlib.util.spec_from_file_location(
                        fullname,
                        module_file,
                        submodule_search_locations=[str(action_dir)],
                    )
            else:
                mid = action_dir
                for i in range(4, len(parts) - 1):
                    mid = mid / parts[i]
                last = parts[-1]
                file_py = mid / f"{last}.py"
                dir_init = mid / last / "__init__.py"
                if file_py.exists():
                    return importlib.util.spec_from_file_location(
                        fullname, file_py, submodule_search_locations=[str(mid)]
                    )
                if dir_init.exists():
                    return importlib.util.spec_from_file_location(
                        fullname,
                        dir_init,
                        submodule_search_locations=[str(mid / last)],
                    )
            return None

        if not dir_path.exists() or not dir_path.is_dir():
            return None

        if len(parts) <= 3:
            spec = importlib.machinery.ModuleSpec(
                fullname, loader=None, is_package=True
            )
            spec.submodule_search_locations = [str(dir_path)]
            return spec

        init_file = dir_path / "__init__.py"
        module_file = dir_path / f"{parts[3]}.py"
        if init_file.exists():
            return importlib.util.spec_from_file_location(
                fullname,
                init_file,
                submodule_search_locations=[str(dir_path)],
            )
        if module_file.exists():
            return importlib.util.spec_from_file_location(
                fullname, module_file, submodule_search_locations=[str(dir_path)]
            )
        return None


# Global importer instance registered at module load time
_actions_importer = JvagentActionsImporter(lambda: _actions_importer_base_path)

if _actions_importer not in sys.meta_path:
    sys.meta_path.insert(0, _actions_importer)
