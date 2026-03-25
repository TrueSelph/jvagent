"""Prompt templates for PageIndex retrieval."""

DIRECTIVE_TEMPLATE = """The following details were retrieved from the user's profile and memory:

{results}

DIRECTIONS:
1. Incorporate these details naturally and conversationally into your response.
2. Act as if you naturally remember these facts about the user.
3. NEVER explicitly state that you are using a "profile", "memory", or basing your answer on their "interests" or "preferences".
4. Avoid phrases like "I see you're interested in..." or "To tie in your interest in...". Instead, just suggest the topics directly as if they are your own ideas for them.
"""
