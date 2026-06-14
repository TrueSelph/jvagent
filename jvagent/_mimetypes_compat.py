"""Deterministic MIME-type registration for text formats jvagent emits.

jvspatial's local storage validator detects a file's MIME type via
``python-magic`` when available, otherwise it falls back to the stdlib
``mimetypes`` module (extension-based). When *neither* knows an extension the
type resolves to ``application/octet-stream``, which is not in jvspatial's
allow-list — so the save is rejected with::

    File type 'application/octet-stream' is not in allowed types

Markdown is the common casualty: ``mimetypes`` only maps ``.md`` →
``text/markdown`` when the host OS mime database (e.g. ``/etc/mime.types``)
happens to include it. On hosts without that entry and without ``libmagic``
installed, every ``.md`` an agent writes (research reports, notes, …) fails to
save even though ``text/markdown`` *is* in jvspatial's allow-list.

We close the gap deterministically by registering the markdown extensions in
the ``mimetypes`` module at import time, before any file-save path runs. Only
extensions whose target type is already in jvspatial's default allow-list are
registered here — this changes detection, never the policy.

This mirrors the early ``_logging_compat`` install in :mod:`jvagent` and must
run before any submodule performs a save.
"""

from __future__ import annotations

import mimetypes

# (mime_type, extension) pairs. Each ``mime_type`` MUST already be present in
# jvspatial's ``FileValidator.DEFAULT_ALLOWED_MIME_TYPES`` — we only fix
# *detection* of well-known text formats, never the allow-list itself.
_EXTRA_TYPES = (
    ("text/markdown", ".md"),
    ("text/markdown", ".markdown"),
)

_installed = False


def install() -> None:
    """Register markdown extensions in ``mimetypes`` (idempotent)."""
    global _installed
    if _installed:
        return
    # ``mimetypes.add_type`` lazily inits the module's maps on first use, so an
    # explicit init is unnecessary; add_type is safe to call repeatedly.
    for mime_type, ext in _EXTRA_TYPES:
        mimetypes.add_type(mime_type, ext)
    _installed = True


__all__ = ["install"]
