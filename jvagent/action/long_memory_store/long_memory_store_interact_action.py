"""UserLongMemoryStoreInteractAction

A passive background action that picks up on updated memory categories
and assimilates them into the PageIndex for semantic search.
"""

import logging

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.memory.user_long_memory import UserLongMemory

logger = logging.getLogger(__name__)


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

        # Check if any category needs indexing
        unindexed_nodes = await user_long_memory.get_unindexed_categories()
        if not unindexed_nodes:
            return

        logger.info(
            f"UserLongMemoryStoreAction: Found {len(unindexed_nodes)} updated categories for user {user_id}"
        )

        try:
            from jvagent.action.pageindex.documents import (
                delete_document,
                list_documents,
            )
            from jvagent.action.pageindex.endpoints import _do_assimilate

            # Generate combined markdown of all memory
            full_markdown = await user_long_memory.as_markdown(include_empty=False)
            agent_id = self.agent_id
            collection_name = f"{agent_id}_{self.collection}"

            # Delete existing user model doc(s) for this user to avoid duplicates
            try:
                existing_docs = await list_documents(
                    collection_name=collection_name,
                    metadata_filter={"user_id": user_id},
                )
                for doc in existing_docs:
                    doc_name = doc.get("doc_name")
                    if doc_name:
                        await delete_document(doc_name, collection_name=collection_name)
                        logger.info(
                            f"Deleted existing long memory doc: {doc_name} for user {user_id}"
                        )
            except Exception as e:
                logger.warning(f"Failed to clean up existing long memory docs: {e}")

            if full_markdown.strip():
                await _do_assimilate(
                    content=full_markdown.encode("utf-8"),
                    ext="md",
                    doc_name=f"user_long_memory_{user_id}",
                    model=self.model,
                    if_add_node_summary="yes",
                    collection_name=collection_name,
                    metadata={"user_id": user_id, "type": "user_long_memory"},
                    doc_description="User long-term memory",
                )

            # Clear the needs_indexing flags
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
                f"UserLongMemoryStoreAction: Failed to assimilate into PageIndex: {e}",
                exc_info=True,
            )
