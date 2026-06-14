"""Prompt templates for WebSearchRetrievalInteractAction.

This module provides the directive template used to format web search results
for injection into the agent's response pipeline.
"""

DIRECTIVE_TEMPLATE = """\
Using the following live web search results to inform your response.
Prioritize this information as it is current and up to date:

{results}
"""
