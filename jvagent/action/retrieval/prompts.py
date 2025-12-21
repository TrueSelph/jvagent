"""Prompt templates for RetrievalInteractAction.

This module provides the prompt templates used by RetrievalInteractAction:
- Directive template for formatting retrieved context
"""

# ============================================================================
# Directive Template
# ============================================================================

DIRECTIVE_TEMPLATE = """Context retrieved from knowledge base:

{results}

Use this context to inform your response to the user's query."""

