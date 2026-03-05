"""WebSearchRetrievalInteractAction

Performs web searches via a configured BaseWebSearchAction and injects
results as a directive into the agent's response pipeline.

Mirrors RetrievalInteractAction's relationship to VectorStore.
"""

import logging
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.web_search.base import BaseWebSearchAction

from .prompts import DIRECTIVE_TEMPLATE

if TYPE_CHECKING:
    from jvagent.memory.interaction import Interaction

logger = logging.getLogger(__name__)


class WebSearchRetrievalInteractAction(InteractAction):
    """InteractAction that retrieves web search results and injects them as a directive.

    Mirrors ``RetrievalInteractAction``'s relationship to ``VectorStore``:

    1. Resolves a ``BaseWebSearchAction`` by ``web_search_action_type``
    2. Uses the interaction's interpretation (or utterance as fallback) as query
    3. Calls ``search()`` on the resolved action
    4. Formats the results into a structured directive for ``PersonaAction``

    Attributes:
        web_search_action_type: Class name of the ``BaseWebSearchAction`` to use
            (e.g., ``"SerperWebSearchAction"``, ``"SerpAPIWebSearchAction"``).
            If empty, uses the first available ``BaseWebSearchAction`` registered with the agent.
        directive: Template for formatting results. Placeholder: ``{results}``
        weight: Execution weight (default: ``-75``, runs before PersonaAction)
    """

    web_search_action_type: str = attribute(
        default="",
        description=(
            "Class name of the BaseWebSearchAction to use "
            "(e.g., 'SerperWebSearchAction', 'SerpAPIWebSearchAction'). "
            "If empty, uses first available BaseWebSearchAction."
        ),
    )
    weight: int = attribute(
        default=-75,
        description="Execution weight (runs after InteractRouter but before PersonaAction)",
    )
    directive: str = attribute(
        default=DIRECTIVE_TEMPLATE,
        description="Template for formatting the search results directive. Placeholder: {results}",
    )
    parameters: List[Dict[str, Any]] = attribute(
        default=[
            {
                "condition": (
                    "There is no useful information in the web search results "
                    "or none were returned"
                ),
                "response": (
                    "Answer based on your own knowledge but note that the information "
                    "may be outdated and encourage the user to verify from external sources."
                ),
            }
        ],
        description="Conditions and responses to customize behavior.",
    )

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def on_register(self) -> None:
        """Validate configuration at agent startup."""
        await super().on_register()
        await self._log_web_search_action_status(is_reload=False)

    async def on_reload(self) -> None:
        """Re-validate after a hot-reload."""
        await super().on_reload()
        await self._log_web_search_action_status(is_reload=True)

    # ------------------------------------------------------------------ #
    # Execute
    # ------------------------------------------------------------------ #

    async def execute(self, visitor: "InteractWalker") -> None:
        """Resolve the search action, run the query, inject results as directive."""
        interaction = visitor.interaction
        if not interaction:
            logger.warning("WebSearchRetrievalInteractAction: No interaction available")
            return

        query = self._get_search_query(interaction)
        if not query:
            logger.debug(
                "WebSearchRetrievalInteractAction: no query available, skipping"
            )
            return

        search_action = await self._get_web_search_action()
        if not search_action:
            configured = self.web_search_action_type or "(first available)"
            logger.warning(
                f"WebSearchRetrievalInteractAction: search action '{configured}' not found, "
                "skipping web search."
            )
            return

        try:
            results: List[Dict[str, Any]] = await search_action.search(query)
        except Exception as e:
            logger.error(
                f"WebSearchRetrievalInteractAction: search failed for query {query!r}: {e}",
                exc_info=True,
            )
            return

        if results:
            directive = self._format_directive(results)
            logger.debug(
                f"WebSearchRetrievalInteractAction: {len(results)} result(s) for {query!r}"
            )
            await visitor.add_directive(directive)
        else:
            logger.warning(
                f"WebSearchRetrievalInteractAction: no results for query {query!r}"
            )

        if self.parameters:
            await visitor.add_parameters(self.parameters)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    async def _get_web_search_action(self) -> Optional[BaseWebSearchAction]:
        """Resolve the BaseWebSearchAction by type or fallback to first available.

        Mirrors ``RetrievalInteractAction._get_vectorstore_action()``.
        """
        if self.web_search_action_type:
            action: Optional[BaseWebSearchAction] = await self.get_action(
                self.web_search_action_type
            )
            if action:
                return action

        # Fallback: first registered BaseWebSearchAction
        return await self.get_action(BaseWebSearchAction)

    def _get_search_query(self, interaction: "Interaction") -> Optional[str]:
        """Use interpretation if available, otherwise fall back to utterance."""
        interpretation = interaction.interpretation
        search_query = self._convert_to_search_query(interpretation)
        return search_query

    def _convert_to_search_query(self, statement: str) -> str:
        """
        Convert a descriptive statement into a concise Google search query.

        Args:
            statement (str): The descriptive statement to convert

        Returns:
            str: A clean search query
        """
        # Remove common prefixes and meta descriptions
        prefixes = [
            r"^User is asking about ",
            r"^User is asking for ",
            r"^User is inquiring about ",
            r"^User asked about ",
            r"^User asked for",
            r"^User inquired about ",
            r"^User questions ",
            r"^The user wants to know ",
            r"^The user is asking about ",
            r"^The user is asking for ",
            r"^The user asked about ",
            r"^The user asked for ",
            r"^The user inquired about ",
            r"^User wants to know ",
            r"^This is a query about ",
            r"^Question regarding ",
            r"^Inquiry about ",
        ]
        suffixes = [
            r"\s*;\s*(clear|this is).+$",  # "; clear informational inquiry..."
            r"\s*,\s*indicating\s+.+$",  # ", indicating an informational inquiry..."
            r"\s*,\s*which is (a|an)\s+.+$",  # ", which is a location-based query..."
            r"\s*,\s*as (he|she|they|the user)\s+.+$",  # ", as the user wants..."
        ]

        query = statement

        # Remove prefixes
        for prefix in prefixes:
            query = re.sub(prefix, "", query, flags=re.IGNORECASE)

        # Remove suffixes
        for suffix in suffixes:
            query = re.sub(suffix, "", query, flags=re.IGNORECASE)

        best_query = query

        # Clean up the query
        # Remove any remaining meta text
        best_query = re.sub(r"\s+", " ", best_query)  # Normalize spaces
        best_query = re.sub(r"[.!?]$", "", best_query)  # Remove trailing punctuation

        # Ensure it's a proper search query
        search_query = best_query.strip()

        return search_query

    async def _log_web_search_action_status(self, *, is_reload: bool = False) -> None:
        """Log web search action resolution status (used by on_register and on_reload)."""
        action = await self._get_web_search_action()
        configured = self.web_search_action_type or "(first available)"
        if not action:
            if is_reload:
                logger.warning(
                    f"WebSearchRetrievalInteractAction '{self.label}': "
                    f"after reload, '{configured}' not found."
                )
            else:
                logger.warning(
                    f"WebSearchRetrievalInteractAction '{self.label}': "
                    f"web_search_action_type='{configured}' not found. "
                    "Ensure a BaseWebSearchAction is registered with the agent."
                )
        else:
            if is_reload:
                logger.info(
                    f"WebSearchRetrievalInteractAction '{self.label}': "
                    f"reload — active search action '{action.get_class_name()}' ({action.label})"
                )
            else:
                logger.info(
                    f"WebSearchRetrievalInteractAction '{self.label}': "
                    f"using search action '{action.get_class_name()}' ({action.label})"
                )

    def _format_directive(self, results: List[Dict[str, Any]]) -> str:
        """Format search results into the directive template."""
        parts = []
        for r in results:
            title = r.get("title", "")
            link = r.get("link", "")
            snippet = r.get("snippet", "")
            entry = f"- **{title}**"
            if link:
                entry += f" ({link})"
            if snippet:
                entry += f"\n  {snippet}"
            parts.append(entry)

        results_str = "\n".join(parts)
        return self.directive.format(results=results_str)
