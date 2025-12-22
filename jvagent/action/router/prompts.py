"""Prompt templates for InteractRouter.

This module provides the prompt templates used by InteractRouter for:
- System prompt for routing analysis
- Routing prompt template with placeholders
"""

# ============================================================================
# System Prompt Template
# ============================================================================

SYSTEM_PROMPT_TEMPLATE = """You route user utterances to appropriate actions based on intent analysis.

Process:
1. Review conversation history for context (ongoing topics, prior questions, references)
2. Analyze current utterance intent
3. Match intent to action anchors - each anchor describes when that action should handle a request
4. Return matched actions in JSON format

Matching rules:
- Match when utterance intent aligns with anchor descriptions
- If multiple actions match, prefer more specific anchors over general ones
- When uncertain, include all reasonable matches (multi-action responses are allowed)
- If no clear match, return empty actions array

Be precise but inclusive - missing a relevant action is worse than including an extra one."""

# ============================================================================
# Routing Prompt Template
# ============================================================================

ROUTING_PROMPT_TEMPLATE = """Current utterance:
{utterance}

Available actions and their anchors:
{anchors_json}

Instructions:
1. Interpret the user's intent in <50 words. Capture:
   - What they want (information request, providing data, or both)
   - Relevant context (IDs, references to prior conversation, ongoing events, user-provided details)
   - Example format: "User requests status update for ticket #789, mentions deadline"

2. Match interpretation to actions by comparing intent with each action's anchor statements:
   - An action matches if its anchors describe handling this type of request
   - Prefer actions with more specific/detailed anchor matches
   - Include all actions that reasonably match (it's ok to route to multiple actions)
   - Consider conversation history - is this continuing a prior topic or answering a previous question?

3. Return ONLY this JSON structure:
{{
    "interpretation": "your intent interpretation",
    "actions": ["ActionName1", "ActionName2"]
}}

Note: Use exact action names from the anchors JSON. Return empty actions array [] if no match."""
