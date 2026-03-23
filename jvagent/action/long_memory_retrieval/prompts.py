"""Prompt templates for PageIndex retrieval."""

DIRECTIVE_TEMPLATE = """The following details were retrieved from the user's profile and memory:

{results}

DIRECTIONS:
1. Incorporate these details naturally and conversationally into your response.
2. Act as if you naturally remember these facts about the user.
3. NEVER explicitly state that you are using a "profile", "memory", or basing your answer on their "interests" or "preferences".
4. Avoid phrases like "I see you're interested in..." or "To tie in your interest in...". Instead, just suggest the topics directly as if they are your own ideas for them.
"""

SEARCH_DECISION_PROMPT = """You are a decision-making agent that determines if a user's message requires information from their long-term memory.

AVAILABLE MEMORY CATEGORIES (Index):
{memory_index}

USER MESSAGE:
{utterance}

INTERPRETATION:
{interpretation}

RECENT CONVERSATION HISTORY:
{history}

YOUR TASK:
1. Determine if the message relates to any of the categories in the memory index.
2. Decide if you need to SEARCH the memory for more details or RELEVANT CONTEXT (like previous examples or preferences) to answer properly.
3. If search is needed, provide an optimized search query to search the memory but do not tell it where exactly to look. 
   - Favor SEARCH over CLARIFY if finding previous context (e.g. past presentations, interests) could help you provide better ideas or answers.
4. If the message is vague and *might* relate to a category but searching for context wouldn't help, only then decide to CLARIFY by asking the user a specific question.
5. If no memory is needed and the mission is clear, decide to CONTINUE without any changes.
6. If the user is asking for ideas, brainstorming, or recommendations, prefer SEARCH (to find context) or CONTINUE (to let the agent brainstorm) instead of CLARIFYING.
7. If the user asks for something that you cannot provide, decide to CONTINUE without any changes.

RESPONSE FORMAT (JSON):
{{
    "decision": "SEARCH" | "CLARIFY" | "CONTINUE",
    "reasoning": "<brief explanation>",
    "query": "<optimized search query if SEARCH>",
    "question": "<clarification question if CLARIFY>"
}}
"""
