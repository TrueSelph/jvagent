"""Prompt templates for PageIndex retrieval."""

DIRECTIVE_TEMPLATE = """The following details was retrieved from the user's profile:

{results}

Use this information to personalize your response to the user's message when appropriate.
Avoid repeating this information back to the user unless it is necessary to do so.
"""
