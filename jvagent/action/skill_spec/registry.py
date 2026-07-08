"""Shared skill-spec registry — directory discovery and caching."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Dict, Generic, List, Optional, TypeVar, Union

from .base import SKILL_MD

logger = logging.getLogger(__name__)

TSpec = TypeVar("TSpec")

LoaderFn = Callable[[Union[str, Path]], Optional[TSpec]]


class BaseSkillRegistry(Generic[TSpec]):
    """Discovers, loads, and caches skill specs from skill directories."""

    def __init__(
        self,
        *,
        label: str,
        loader: LoaderFn[TSpec],
    ) -> None:
        self._label = label
        self._loader = loader
        self._specs: Dict[str, TSpec] = {}

    def discover(self, skills_dirs: List[str]) -> Dict[str, TSpec]:
        for skills_dir in skills_dirs:
            skills_path = Path(skills_dir)
            if not skills_path.is_dir():
                continue
            for skill_dir in skills_path.iterdir():
                if not skill_dir.is_dir() or not (skill_dir / SKILL_MD).is_file():
                    continue
                try:
                    spec = self._loader(skill_dir)
                except Exception as e:
                    logger.error(
                        "Failed to load %s spec from %s: %s",
                        self._label,
                        skill_dir,
                        e,
                    )
                    continue
                name = getattr(spec, "name", "") if spec is not None else ""
                if spec is None or not name:
                    continue
                self._specs[name] = spec
                logger.info(
                    "Loaded %s spec: %s from %s", self._label, name, skill_dir
                )
        return self._specs

    def get(self, name: str) -> Optional[TSpec]:
        return self._specs.get(name)

    def list_specs(self) -> List[str]:
        return list(self._specs.keys())

    def reload(self, skills_dirs: List[str]) -> Dict[str, TSpec]:
        self._specs.clear()
        return self.discover(skills_dirs)

    @property
    def specs(self) -> Dict[str, TSpec]:
        return self._specs
