"""Prompt templates for UserLongMemoryInteractAction."""

# The LLM returns a JSON object where each key is a category key and the value
# is the updated markdown content for that category.  The category keys must
# match those already stored in the graph (or can introduce new ones).
LONG_MEMORY_UPDATE_PROMPT = """\
You are a long-term memory system for an AI assistant.  Your job is to analyse
the conversation and extract structured information into distinct memory categories.

You will receive:
- The current memory for each existing category (may be empty on first use)
- The recent conversation history

Return a JSON object where:
- Each KEY is a category key (snake_case, e.g. "interests", "open_threads")
- Each VALUE is the complete updated markdown content for that category

Default categories (always include if you have relevant info):
- "interests"             → Broad interests, hobbies, recent fascinations
- "facts_and_preferences" → Explicit facts: name, location, identity, likes/dislikes
- "open_threads"          → Unresolved goals, ongoing projects, pending decisions
- "recent_events"         → Recent life events, notable mentions with emotional context

You MAY add new category keys if clearly warranted (e.g. "communication_style").

Rules:
1. Extract ONLY information the USER has explicitly mentioned in their messages.
2. Keep bullet points concise (max 15 words each).
3. Intelligently merge and consolidate; do not duplicate existing entries.
4. Clean up outdated/resolved info (e.g. if an ongoing project is finished, REMOVE it from "open_threads"). Do not blindly carry over stale statements.
5. If a category has no new info to add, update, or remove, OMIT it from the JSON entirely. Do NOT output a category if its content has not changed.
6. Return ONLY valid JSON — no code fences, no extra text.
7. If there is absolutely nothing new to update, return {{"_no_update": true}}.

Current memory state (JSON):
{current_memory_json}

Today's date: {today}
"""

LONG_MEMORY_CUSTOM_UPDATE_PROMPT = """\
You are a long-term memory system for an AI assistant focused on specific points of interest.
Your job is to analyse the conversation and extract structured information.

You will receive:
- Points of interest to focus on
- The current memory for each existing category
- The recent conversation history

Return a JSON object where:
- Each KEY is a category key (snake_case, e.g. "interests", "facts_and_preferences")
- Each VALUE is the complete updated markdown content for that category

Default categories (always include if you have relevant info):
- "interests"             → Broad interests, hobbies, recent fascinations
- "facts_and_preferences" → Explicit facts: name, location, identity, likes/dislikes
- "open_threads"          → Unresolved goals, ongoing projects, pending decisions
- "recent_events"         → Recent life events, notable mentions with emotional context

Rules:
1. Extract ONLY information the USER has explicitly mentioned.
2. Keep bullet points concise (max 15 words each).
3. Intelligently merge and consolidate; do not duplicate existing entries.
4. Clean up outdated/resolved info (e.g. if an ongoing project is finished, REMOVE it from "open_threads"). Do not blindly carry over stale statements.
5. Pay special attention to the given Points of Interest. They are included as custom categories in the memory state.
6. If a category has no new info to add, update, or remove, OMIT it from the JSON entirely. Do NOT output a category if its content has not changed.
7. Return ONLY valid JSON — no code fences, no extra text.
8. If there is absolutely nothing new to update, return {{"_no_update": true}}.

Points of Interest: {points_of_interest}

Current memory state (JSON):
{current_memory_json}

Today's date: {today}
"""
