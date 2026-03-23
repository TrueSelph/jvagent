"""Dynamic import of action modules (filesystem and namespace packages)."""

import importlib
import importlib.util
import logging
import sys
import types
from pathlib import Path
from typing import Optional, Type

from jvagent.action.base import Action

logger = logging.getLogger(__name__)


def ensure_action_parent_packages(
    module_name: str, action_dir: Path, actions_prefix: str
) -> None:
    """Ensure parent packages exist in ``sys.modules`` with correct ``__path__``.

    Does not rely on JvagentActionsImporter (which may fail in Lambda). Creates
    namespace packages manually using the same path mapping as the finder.
    """
    if not module_name.startswith(actions_prefix):
        return
    rest = module_name[len(actions_prefix) :]
    parts = rest.split(".")
    if len(parts) < 1:
        return
    agents_path = action_dir
    for _ in range(5):
        agents_path = agents_path.parent
    if agents_path.name != "agents":
        logger.debug(
            "Expected 'agents' when walking up from %s, got %s",
            action_dir,
            agents_path.name,
        )
        return
    if "jvagent.actions" not in sys.modules:
        mod = types.ModuleType("jvagent.actions")
        mod.__path__ = [str(agents_path)]
        mod.__package__ = "jvagent"
        sys.modules["jvagent.actions"] = mod
    for i in range(1, len(parts)):
        parent_name = actions_prefix + ".".join(parts[:i])
        if parent_name in sys.modules:
            continue
        if i == 1:
            dir_path = agents_path / parts[0]
        elif i == 2:
            dir_path = agents_path / parts[0] / parts[1] / "actions"
        elif i == 3:
            dir_path = agents_path / parts[0] / parts[1] / "actions" / parts[2]
        else:
            dir_path = agents_path / parts[0] / parts[1] / "actions" / parts[2]
            for j in range(3, i):
                dir_path = dir_path / parts[j]
        if not dir_path.exists() or not dir_path.is_dir():
            continue
        mod = types.ModuleType(parent_name)
        mod.__path__ = [str(dir_path)]
        mod.__package__ = (
            "jvagent.actions" if i == 1 else actions_prefix + ".".join(parts[: i - 1])
        )
        sys.modules[parent_name] = mod


def load_action_module(
    module_name: str,
    action_dir: Path,
    action_name: str,
    archetype: str,
    actions_prefix: str,
) -> Optional[Type[Action]]:
    """Load an action class from a module or package under ``action_dir``."""
    ensure_action_parent_packages(module_name, action_dir, actions_prefix)

    if module_name in sys.modules:
        existing = sys.modules[module_name]
        action_class = getattr(existing, archetype, None)
        if action_class is not None and issubclass(action_class, Action):
            return action_class

    init_file = action_dir / "__init__.py"
    module_file = action_dir / f"{action_name}.py"

    if init_file.exists():
        try:
            spec = importlib.util.spec_from_file_location(
                module_name, init_file, submodule_search_locations=[str(action_dir)]
            )

            if spec is None or spec.loader is None:
                logger.debug("Could not load spec for package: %s", init_file)
            else:
                package = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = package
                try:
                    spec.loader.exec_module(package)
                except (ImportError, NameError, ModuleNotFoundError) as e:
                    logger.warning(
                        "Error importing action package %s: %s. "
                        "This may be due to missing dependencies or import errors.",
                        init_file,
                        e,
                    )
                else:
                    action_class = getattr(package, archetype, None)

                    if action_class is None:
                        if module_file.exists():
                            module_spec = importlib.util.spec_from_file_location(
                                f"{module_name}.{action_name}",
                                module_file,
                                submodule_search_locations=[str(action_dir)],
                            )
                            if module_spec and module_spec.loader:
                                module = importlib.util.module_from_spec(module_spec)
                                module_spec.loader.exec_module(module)
                                action_class = getattr(module, archetype, None)
                                if action_class:
                                    setattr(package, archetype, action_class)

                    if action_class is not None:
                        if not issubclass(action_class, Action):
                            logger.warning(
                                "Class %s is not a subclass of Action", archetype
                            )
                            return None
                        return action_class
        except Exception as e:
            logger.warning("Error loading package from %s: %s", init_file, e)

    if not module_file.exists():
        return None

    try:
        spec = importlib.util.spec_from_file_location(
            module_name, module_file, submodule_search_locations=[str(action_dir)]
        )

        if spec is None or spec.loader is None:
            logger.debug("Could not load spec for module: %s", module_file)
            return None

        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        action_class = getattr(module, archetype, None)

        if action_class is None:
            logger.warning("Class %s not found in module %s", archetype, module_file)
            return None

        if not issubclass(action_class, Action):
            logger.warning("Class %s is not a subclass of Action", archetype)
            return None

        return action_class

    except Exception as e:
        logger.error(
            "Error loading action class from %s: %s", module_file, e, exc_info=True
        )
        return None
