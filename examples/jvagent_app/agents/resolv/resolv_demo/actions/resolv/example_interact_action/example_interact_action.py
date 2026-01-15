"""RetrievalInteractAction for retrieving context from vector stores.

This module provides RetrievalInteractAction, an InteractAction that retrieves
relevant context from a vector store using the interaction's interpretation (or
utterance as fallback) and composes a structured directive for PersonaAction.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker

if TYPE_CHECKING:
    from jvagent.memory.interaction import Interaction

logger = logging.getLogger(__name__)


class ExampleInteractAction(InteractAction):
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
    description: str = "Example interact action that knows about JVAgent and provides info to users."
    collection: str = attribute(
        default="",
        description="Collection name to search in the vector store",
    )
    weight: int = attribute(
        default=-50,
        description="Execution weight (runs after InteractRouter but before PersonaAction)",
    )
    directive_template: Optional[str] = attribute(
        default=None,
        description="Optional template for formatting the directive. Uses default structured format if not provided. Placeholder: {results}",
    )

    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute example interact action and add directive to interaction.

        Args:
            visitor: The InteractWalker visiting this action
        """
        interaction = visitor.interaction
        if not interaction:
            logger.warning("ExampleInteractAction: No interaction available")
            return

        result = "Tell the user that they are currently interacting with an AI agent on JVAgent and can learn more about jvagent at https://jvagent.com/docs"
        if result:
            # Generate response via PersonaAction with directive
            await self.respond(
                visitor,
                directives=[result],
                parameters=self.parameters if self.parameters else None
            )
            logger.debug(
                f"ExampleInteractAction: Generated response with directives and parameters"
            )
        else:
            logger.debug("ExampleInteractAction: No result, skipping response")

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
