"""RetrievalInteractAction for retrieving context from vector stores.

This module provides RetrievalInteractAction, an InteractAction that retrieves
relevant context from a vector store using the interaction's interpretation (or
utterance as fallback) and composes a structured directive for PersonaAction.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core.annotations import attribute
from jvspatial.core import on_visit

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.vectorstore.base import VectorStore

if TYPE_CHECKING:
    from jvagent.memory.interaction import Interaction

logger = logging.getLogger(__name__)


class RetrievalInteractAction(InteractAction):
    """InteractAction that retrieves context from vector stores and adds directives.

    RetrievalInteractAction:
    1. Uses the interaction's interpretation (or utterance as fallback) as search query
    2. Retrieves relevant context from a configured vector store
    3. Formats retrieved results into a structured directive
    4. Adds the directive to the interaction for PersonaAction to use

    Attributes:
        vectorstore_action_type: Entity type of VectorStore action (e.g., "TypesenseVectorStore")
        collection: Collection name to search in (default: "default")
        k: Number of search results to retrieve (default: 10)
        weight: Execution weight (default: -50, runs after InteractRouter but before PersonaAction)
        directive_template: Optional template for formatting the directive with placeholder: {results}
        min_score_threshold: Optional minimum similarity score to include results
    """

    vectorstore_action_type: str = attribute(
        default="",
        description="Entity type of VectorStore action to use (e.g., 'TypesenseVectorStore'). If empty, uses first available.",
    )
    collection: str = attribute(
        default="",
        description="Collection name to search in the vector store",
    )
    k: int = attribute(
        default=10,
        description="Number of search results to retrieve",
        ge=1,
    )
    weight: int = attribute(
        default=-50,
        description="Execution weight (runs after InteractRouter but before PersonaAction)",
    )
    directive_template: Optional[str] = attribute(
        default=None,
        description="Optional template for formatting the directive. Uses default structured format if not provided. Placeholder: {results}",
    )
    min_score_threshold: Optional[float] = attribute(
        default=None,
        description="Optional minimum similarity score to include results (0.0-1.0)",
        ge=0.0,
        le=1.0,
    )

    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute retrieval and add directive to interaction.

        Args:
            visitor: The InteractWalker visiting this action
        """
        interaction = visitor.interaction
        if not interaction:
            logger.warning("RetrievalInteractAction: No interaction available")
            return

        # Keep track of whether we produced a directive
        directive_added = False

        try:
            # Get search query (interpretation or utterance fallback)
            query = self._get_search_query(interaction)
            if not query:
                logger.debug("RetrievalInteractAction: No query available, skipping retrieval")
                return

            # Get VectorStore action
            vectorstore = await self._get_vectorstore_action()
            if not vectorstore:
                logger.warning("RetrievalInteractAction: VectorStore action not found, skipping retrieval")
                return

            # Perform search
            logger.debug(f"RetrievalInteractAction: Searching collection '{self.collection}' with query: {query[:100]}")
            resolved_collection = await vectorstore._resolve_collection_name(self.collection)
            results = await vectorstore.search(
                collection=resolved_collection,
                query=query,
                k=self.k,
            )

            # Filter results by score threshold if configured
            if self.min_score_threshold and results:
                filtered_results = [
                    r for r in results
                    if r.get("score", 0.0) >= self.min_score_threshold
                ]
                if len(filtered_results) < len(results):
                    logger.debug(
                        f"RetrievalInteractAction: Filtered {len(results) - len(filtered_results)} "
                        f"results below threshold {self.min_score_threshold}"
                    )
                results = filtered_results

            # Format and add directive if results found
            if results:
                directive = self._format_directive(results)
                interaction.add_directive(directive)
                await interaction.save()
                directive_added = True
                logger.debug(
                    f"RetrievalInteractAction: Added directive with {len(results)} retrieved context items"
                )
            else:
                logger.debug("RetrievalInteractAction: No results found, no directive added")

        except Exception as e:
            logger.error(f"RetrievalInteractAction: Error during retrieval: {e}", exc_info=True)
            # Don't raise - allow other actions to continue

        # If a directive was produced, optionally invoke PersonaAction to produce a response
        if directive_added:
            try:
                persona = await self._get_persona_action()
                if persona:
                    # PersonaAction.respond now supports visitor (for streaming via ResponseBus)
                    response = await persona.respond(interaction, visitor=visitor)
                    if response and visitor.interaction:
                        visitor.interaction.set_response(response)
                        await visitor.interaction.save()
                else:
                    logger.debug("RetrievalInteractAction: PersonaAction not found; skipping auto-respond")
            except Exception as e:
                logger.error(f"RetrievalInteractAction: Error calling PersonaAction.respond: {e}", exc_info=True)

    async def _get_vectorstore_action(self) -> Optional[VectorStore]:
        """Get the VectorStore action for retrieval.

        Returns:
            VectorStore instance or None if not found
        """
        agent = await self.get_agent()
        if not agent:
            logger.error("RetrievalInteractAction: Agent not found")
            return None

        # Try to get by type if specified
        if self.vectorstore_action_type:
            vectorstore = await agent.get_action_by_type(self.vectorstore_action_type)
            if vectorstore and isinstance(vectorstore, VectorStore):
                return vectorstore

        # Fallback: find first available VectorStore action
        from jvagent.action.vectorstore.base import VectorStore as VectorStoreBase
        actions_manager = await agent.get_actions_manager()
        if actions_manager:
            all_actions = await actions_manager.get_actions(enabled_only=True)
            for action in all_actions:
                if isinstance(action, VectorStoreBase):
                    return action

        return None

    async def _get_persona_action(self) -> Optional[Any]:
        """Get the PersonaAction for responding with persona prompt.

        Returns:
            PersonaAction instance or None if not found
        """
        agent = await self.get_agent()
        if not agent:
            logger.error("RetrievalInteractAction: Agent not found")
            return None

        from jvagent.action.persona.base import PersonaAction

        actions_manager = await agent.get_actions_manager()
        if actions_manager:
            all_actions = await actions_manager.get_actions(enabled_only=True)
            for action in all_actions:
                if isinstance(action, PersonaAction):
                    return action
        return None

    def _get_search_query(self, interaction: "Interaction") -> Optional[str]:
        """Get search query from interpretation or utterance.

        Args:
            interaction: The interaction to get query from

        Returns:
            Query string or None if not available
        """
        # Prefer interpretation (from InteractRouter) over utterance
        query = interaction.interpretation or interaction.utterance
        return query.strip() if query else None

    def _format_directive(self, results: List[Dict[str, Any]]) -> str:
        """Format retrieved results into a structured directive.

        Args:
            results: List of search results, each containing document, score, distance, metadata

        Returns:
            Formatted directive string
        """
        # Format results as a string
        results_parts = []
        for i, result in enumerate(results, 1):
            # Extract document content
            document = result.get("document", {})
            if isinstance(document, dict):
                content = document.get("content", str(document))
            else:
                content = str(document)

            # Get score if available
            score = result.get("score")
            score_str = f" (Relevance score: {score:.3f})" if score is not None else ""

            # Format each result
            results_parts.append(f"{i}. {content}{score_str}")

        results_str = "\n".join(results_parts)

        if self.directive_template:
            # Use custom template if provided
            # Template should use {results} placeholder
            return self.directive_template.format(results=results_str)

        # Default structured format
        directive_parts = ["Context retrieved from knowledge base:\n"]
        directive_parts.append(results_str)
        directive_parts.append("\nUse this context to inform your response to the user's query.")

        return "\n".join(directive_parts)

