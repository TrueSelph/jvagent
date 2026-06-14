"""Single chokepoint for jvspatial private-API access.

We sometimes need to inspect raw DB records (not jvspatial-deserialized
``Node`` objects) — e.g. to find ghost rows whose Python class no longer
imports. The straightforward path goes through ``context._get_entity_type_code``
and ``context._get_collection_name``, both of which are private. Centralising
that coupling here means every future jvspatial release only has to be
adapted in one place.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


async def find_raw_node_records(
    field: str, value: Any, *, missing_ok: bool = True
) -> List[Dict[str, Any]]:
    """Return raw node documents whose ``context.<field>`` equals *value*.

    Bypasses jvspatial's entity-type filtering so ghost rows (whose classes
    no longer import) remain visible.

    If the private API ever changes shape, raise the failure here and return
    an empty list when *missing_ok* (default) — callers using this purely for
    cleanup scans can continue without aborting startup.
    """
    try:
        from jvspatial.core.context import get_default_context
        from jvspatial.core.entities.node import Node

        context = get_default_context()
        type_code = context._get_entity_type_code(
            Node
        )  # noqa: SLF001 — see module docstring
        collection = context._get_collection_name(type_code)  # noqa: SLF001
        return await context.database.find(collection, {f"context.{field}": value})
    except (AttributeError, TypeError) as exc:
        # Private API drift in a future jvspatial release.
        if missing_ok:
            logger.warning(
                "find_raw_node_records: jvspatial private API unavailable (%s); "
                "returning empty result. Update jvagent.core.jvspatial_compat.",
                exc,
            )
            return []
        raise
