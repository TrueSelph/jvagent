"""WebSearchRetrievalInteractAction

Performs web searches via a configured BaseWebSearchAction and injects
results as a directive into the agent's response pipeline.

Mirrors RetrievalInteractAction's relationship to VectorStore.
"""

import logging
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
            (e.g., ``"SerpAPIWebSearchAction"``).  If empty, uses the first
            available ``BaseWebSearchAction`` registered with the agent.
        directive: Template for formatting results. Placeholder: ``{results}``
        weight: Execution weight (default: ``-75``, runs before PersonaAction)
    """

    web_search_action_type: str = attribute(
        default="",
        description=(
            "Class name of the BaseWebSearchAction to use "
            "(e.g., 'SerpAPIWebSearchAction'). "
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
        action = await self._get_web_search_action()
        if not action:
            configured = self.web_search_action_type or "(first available)"
            logger.warning(
                f"WebSearchRetrievalInteractAction '{self.label}': "
                f"web_search_action_type='{configured}' not found. "
                "Ensure a BaseWebSearchAction is registered with the agent."
            )
        else:
            logger.info(
                f"WebSearchRetrievalInteractAction '{self.label}': "
                f"using search action '{action.get_class_name()}' ({action.label})"
            )

    async def on_reload(self) -> None:
        """Re-validate after a hot-reload."""
        await super().on_reload()
        action = await self._get_web_search_action()
        if not action:
            configured = self.web_search_action_type or "(first available)"
            logger.warning(
                f"WebSearchRetrievalInteractAction '{self.label}': "
                f"after reload, '{configured}' not found."
            )
        else:
            logger.info(
                f"WebSearchRetrievalInteractAction '{self.label}': "
                f"reload — active search action '{action.get_class_name()}' ({action.label})"
            )

    # ------------------------------------------------------------------ #
    # Execute
    # ------------------------------------------------------------------ #

    async def execute(self, visitor: "InteractWalker") -> None:
        """Resolve the search action, run the query, inject results as directive."""
        interaction = visitor.interaction
        if not interaction:
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
            logger.debug(
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
        query = interaction.utterance or interaction.interpretation
        return query.strip() if query else None

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
