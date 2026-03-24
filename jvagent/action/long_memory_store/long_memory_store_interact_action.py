"""UserLongMemoryStoreInteractAction

A passive background action that picks up on updated memory categories
and assimilates them into the PageIndex for semantic search.
"""

import asyncio
import hashlib
import logging
import tempfile
from pathlib import Path
from typing import Dict

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.memory.user_long_memory import UserLongMemory

logger = logging.getLogger(__name__)

_store_locks: Dict[str, asyncio.Lock] = {}


def _store_lock_for(user_id: str) -> asyncio.Lock:
    """Process-local mutex so two concurrent interactions do not duplicate ingest."""
    if user_id not in _store_locks:
        _store_locks[user_id] = asyncio.Lock()
    return _store_locks[user_id]


class UserLongMemoryStoreInteractAction(InteractAction):
    """Action that checks for updated memory categories and indexes them."""

    model: str = attribute(
        default="gpt-4o",
        description="Model to use for extraction/summarisation during assimilation",
    )
    collection: str = attribute(
        default="LongTermMemory",
        description="Collection to use for semantic memory extraction",
    )
    weight: int = attribute(
        default=160,
        description="Execution weight (high = late, after long_memory updates)",
    )
    always_execute: bool = attribute(
        default=True, description="Always execute regardless of routing results"
    )
    run_in_background: bool = attribute(
        default=True,
        description="Run in the background after the response is sent",
    )

    async def execute(self, visitor: InteractWalker) -> None:
        """Check for unindexed memory and assimilate if needed."""
        interaction = visitor.interaction
        if not interaction:
            return

        user = await interaction.get_user()
        if not user:
            return

        user_id = visitor.user_id

        user_long_memory = await UserLongMemory.get_for_user(user)
        if not user_long_memory:
            return

        unindexed_nodes = await user_long_memory.get_unindexed_categories()
        if not unindexed_nodes:
            return

        async with _store_lock_for(user_id):
            user_long_memory = await UserLongMemory.get_for_user(user)
            if not user_long_memory:
                return
            unindexed_nodes = await user_long_memory.get_unindexed_categories()
            if not unindexed_nodes:
                return

            logger.info(
                "UserLongMemoryStoreAction: Found %s updated categories for user %s",
                len(unindexed_nodes),
                user_id,
            )

            try:
                from jvagent.action.pageindex.documents import (
                    assimilate_document,
                    delete_document,
                    list_documents,
                )

                full_markdown = await user_long_memory.as_markdown(include_empty=False)
                digest = hashlib.sha256(full_markdown.encode("utf-8")).hexdigest()

                if full_markdown.strip() and digest == getattr(
                    user_long_memory, "pageindex_markdown_sha256", None
                ):
                    for node in unindexed_nodes:
                        node.needs_indexing = False
                        await node.save()
                    interaction.add_event(
                        "UserLongMemoryStoreInteractAction: PageIndex unchanged (markdown digest match), cleared indexing flags",
                        self.get_class_name(),
                    )
                    await interaction.save()
                    return

                agent_id = self.agent_id
                collection_name = f"{agent_id}_{self.collection}"

                try:
                    existing_docs = await list_documents(
                        collection_name=collection_name,
                        metadata_filter={"user_id": user_id},
                    )
                    for doc in existing_docs:
                        doc_name = doc.get("doc_name")
                        if doc_name:
                            await delete_document(
                                doc_name, collection_name=collection_name
                            )
                            logger.info(
                                "Deleted existing long memory doc: %s for user %s",
                                doc_name,
                                user_id,
                            )
                except Exception as e:
                    logger.warning(
                        "Failed to clean up existing long memory docs: %s", e
                    )

                if full_markdown.strip():
                    with tempfile.NamedTemporaryFile(
                        mode="w",
                        encoding="utf-8",
                        suffix=".md",
                        delete=False,
                    ) as tmp:
                        tmp.write(full_markdown)
                        tmp_path = tmp.name
                    try:
                        await assimilate_document(
                            tmp_path,
                            doc_name=f"user_long_memory_{user_id}",
                            model=self.model,
                            if_add_node_summary="yes",
                            collection_name=collection_name,
                            metadata={
                                "user_id": user_id,
                                "type": "user_long_memory",
                            },
                            doc_description="User long-term memory",
                        )
                    finally:
                        Path(tmp_path).unlink(missing_ok=True)

                user_long_memory.pageindex_markdown_sha256 = digest
                await user_long_memory.save()

                for node in unindexed_nodes:
                    node.needs_indexing = False
                    await node.save()

                interaction.add_event(
                    "UserLongMemoryStoreInteractAction: Assimilated updated long memory into PageIndex",
                    self.get_class_name(),
                )
                await interaction.save()

            except Exception as e:
                logger.error(
                    "UserLongMemoryStoreAction: Failed to assimilate into PageIndex: %s",
                    e,
                    exc_info=True,
                )
