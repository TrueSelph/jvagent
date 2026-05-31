"""Markdown MIME registration makes ``.md`` saves pass jvspatial's allow-list.

Regression: on hosts without libmagic and without an OS mime entry for
markdown, ``mimetypes.guess_type('x.md')`` returns ``None`` → jvspatial detects
``application/octet-stream`` → save rejected. ``jvagent`` registers the markdown
extensions at import time so detection yields ``text/markdown`` (allow-listed).
"""

from __future__ import annotations

import importlib
import mimetypes


def test_importing_jvagent_registers_markdown() -> None:
    # Importing the package runs the early install() shim.
    import jvagent  # noqa: F401

    assert mimetypes.guess_type("report.md")[0] == "text/markdown"
    assert mimetypes.guess_type("notes.markdown")[0] == "text/markdown"


def test_install_is_idempotent() -> None:
    from jvagent import _mimetypes_compat

    # Calling repeatedly must not raise and must keep the mapping stable.
    _mimetypes_compat.install()
    _mimetypes_compat.install()
    assert mimetypes.guess_type("x.md")[0] == "text/markdown"


def test_registered_type_is_allowed_by_jvspatial() -> None:
    """The detected type for ``.md`` must be in jvspatial's allow-list."""
    import jvagent  # noqa: F401

    validator_mod = importlib.import_module("jvspatial.storage.security.validator")
    # Find the allow-list set regardless of the exact validator class name.
    allowed = None
    for obj in vars(validator_mod).values():
        allowed = getattr(obj, "DEFAULT_ALLOWED_MIME_TYPES", None)
        if allowed:
            break

    detected = mimetypes.guess_type("research_report.md")[0]
    assert detected == "text/markdown"
    if allowed is not None:
        assert detected in allowed
