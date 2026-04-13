"""Load package data without deprecated ``importlib.resources.read_text`` / ``open_text``."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import List


def read_package_text(package_name: str, filename: str) -> str:
    """Read UTF-8 text from *filename* next to package *package_name*."""
    try:
        from importlib.resources import files

        return files(package_name).joinpath(filename).read_text(encoding="utf-8")
    except (AttributeError, FileNotFoundError, OSError, TypeError, UnicodeError):
        pass

    mod = importlib.import_module(package_name)
    pkg_path = getattr(mod, "__file__", None)
    if not pkg_path:
        raise FileNotFoundError(
            f"Cannot load {filename!r} from package {package_name!r} (no __file__)"
        )
    path = Path(pkg_path).resolve().parent / filename
    return path.read_text(encoding="utf-8")


def list_package_names(package_name: str, *, suffix: str) -> List[str]:
    """Basenames of files directly under *package_name* matching *suffix* (e.g. ``.yaml``)."""
    try:
        from importlib.resources import files

        return sorted(
            p.name for p in files(package_name).iterdir() if p.name.endswith(suffix)
        )
    except (AttributeError, FileNotFoundError, OSError):
        pass

    mod = importlib.import_module(package_name)
    pkg_path = getattr(mod, "__file__", None)
    if not pkg_path:
        return []
    d = Path(pkg_path).resolve().parent
    pat = f"*{suffix}" if suffix.startswith(".") else f"*.{suffix}"
    return sorted(p.name for p in d.glob(pat))
