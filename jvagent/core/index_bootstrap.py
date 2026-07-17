"""Eager index migration and cleanup for jvagent.

Run once at process startup — before any traffic is served — to ensure every
indexed entity class has its current index definitions applied to the database
and any orphaned indexes from previous schema versions are removed.

Why eager bootstrap matters
---------------------------
``ensure_indexes`` is normally called lazily on the first ``save()``/``find()``
for each entity class.  In practice that means the first real user request after
a code-change deployment triggers the migration, racing with live traffic.
Calling ``run_index_migration()`` during startup moves all migration work to a
controlled pre-traffic phase.

What this module does
---------------------
1. Calls ``database.drop_deprecated_indexes()`` so orphaned index names are
   removed before the eager-create sweep.  Database adapters that do not
   support named indexes (e.g. in-memory) silently no-op this step.
2. Clears the in-process ``_ensured_indexes`` cache so that
   ``ensure_indexes`` re-checks every class even if the process was already
   partially warmed up (e.g. a FastAPI lifespan hook that fired before this
   function).
3. Iterates the full list of indexed entity classes and calls
   ``ensure_indexes`` for each, which creates missing indexes and auto-drops /
   recreates any that have changed options (via the adapter's conflict-handling
   logic — for MongoDB this is the code-85/86 path in ``jvspatial.db.mongodb``).

Database adapter flexibility
----------------------------
All steps go through the ``Database`` base-class interface so a non-MongoDB
adapter works without changes:

- ``create_index`` — base class no-ops with a warning; override to implement.
- ``drop_deprecated_indexes`` — base class no-ops silently; override to
  clean up orphaned named indexes.  The ``DEPRECATED_INDEXES`` map uses
  MongoDB collection/index naming conventions; translate as needed for other
  adapters.

User-facing documentation for this module lives in the jvagent repo:
``docs/database-indexing.md`` (separate from jvspatial’s indexing docs).
"""

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Deprecated index registry
# ---------------------------------------------------------------------------
# Maps collection name -> list of index names that were removed or renamed in
# code.  Add entries here whenever an index is dropped or renamed; the name
# will be cleaned up automatically at next startup.
#
# History:
#   - conv_id_only: removed from Interaction (was redundant with the
#     single-field indexed=True on conversation_id).
#   - context.session_id_1: created by old Conversation.session_id indexed=True
#     before that attribute was changed to rely solely on the compound unique
#     conversation_session_id index.  Will be dropped by the code-86 conflict
#     handler in mongodb.py when conversation_session_id is (re)created, but
#     listed here as a safety net for DBs where the compound index already
#     existed under both names simultaneously.
DEPRECATED_INDEXES: Dict[str, List[str]] = {
    "node": [
        "conv_id_only",
        "context.session_id_1",
        # Old Action unique index keyed on (agent_id, label) only. Replaced by
        # ``agent_ns_label`` on (agent_id, namespace, label) so a label reused
        # across namespaces no longer collides. Drop the old name so the new
        # index can be created without a leftover wrong-shape unique constraint.
        # AUDIT-core C3.
        "agent_label",
    ],
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_index_migration() -> None:
    """Drop deprecated indexes then eagerly ensure all current indexes.

    Call this once at startup after the database connection is established and
    before the HTTP server begins accepting requests.
    """
    await _drop_deprecated()
    await _ensure_all_indexes()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _drop_deprecated() -> None:
    """Drop all indexes listed in DEPRECATED_INDEXES."""
    from jvspatial.core.context import get_default_context

    ctx = get_default_context()
    await ctx.database.drop_deprecated_indexes(DEPRECATED_INDEXES)


async def _ensure_all_indexes() -> None:
    """Force ensure_indexes for every indexed entity class."""
    # Clear the process-level cache so that any class touched before this call
    # (e.g. during server setup) is re-evaluated with the current definitions.
    from jvspatial.core.context import _ensured_indexes, get_default_context

    _ensured_indexes.clear()

    ctx = get_default_context()

    # Collect all entity classes that define indexes.
    # Optional integrations (OAuth tokens) are wrapped in ImportError guards.
    entity_classes = _collect_entity_classes()

    logger.info(
        "Index migration: ensuring indexes for %d entity classes", len(entity_classes)
    )

    for cls in entity_classes:
        try:
            await ctx.ensure_indexes(cls)
        except Exception as exc:
            # Log and continue — a single class failure must not abort startup.
            logger.warning(
                "Index migration: failed to ensure indexes for %s: %s",
                cls.__name__,
                exc,
            )

    logger.info("Index migration complete")


def _collect_entity_classes():
    """Return the ordered list of entity classes to bootstrap indexes for."""
    classes = []

    # jvagent core entities
    from jvagent.action.base import Action
    from jvagent.core.agent import Agent
    from jvagent.core.repair_state import RepairState
    from jvagent.memory.conversation import Conversation
    from jvagent.memory.interaction import Interaction
    from jvagent.memory.user import User

    classes.extend(
        [
            Agent,
            Action,
            Conversation,
            Interaction,
            User,
            RepairState,
        ]
    )

    # jvspatial logging
    try:
        from jvspatial.logging.models import DBLog

        classes.append(DBLog)
    except ImportError:
        pass

    # Optional OAuth token entities
    try:
        from jvagent.action.microsoft.microsoft_token import MicrosoftToken

        classes.append(MicrosoftToken)
    except ImportError:
        pass

    try:
        from jvagent.action.google.google_token import GoogleToken

        classes.append(GoogleToken)
    except ImportError:
        pass

    return classes
