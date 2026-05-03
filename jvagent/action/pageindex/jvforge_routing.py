"""Tri-state routing for jvforge vs native ingest (REST, Drive, multipart).

``JVAGENT_JVFORGE_BASE_URL`` supplies the default forge origin; optional ``use_jvforge``
overrides whether requests may be sent there (multipart yes/no, JSON bool, Drive body).
"""

from __future__ import annotations

from typing import Optional

from jvspatial.api.exceptions import ValidationError


def resolve_effective_jvforge_base(
    forge_base: str, *, use_jvforge: Optional[bool]
) -> str:
    """Return the jvforge origin to call, or ``""`` for native-only paths.

    - ``use_jvforge`` **None** (field omitted): use a non-empty ``forge_base`` when set
      (legacy / env-driven).
    - **False**: do not route to jvforge even if ``forge_base`` is set.
    - **True**: require a configured base URL or raise.
    """
    fb = (forge_base or "").strip()
    if use_jvforge is False:
        return ""
    if use_jvforge is True and not fb:
        raise ValidationError(
            message=(
                "use_jvforge=yes requires JVAGENT_JVFORGE_BASE_URL to be set on this server."
            ),
            details={},
        )
    return fb
