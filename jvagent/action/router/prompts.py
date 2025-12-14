"""Prompt templates for InteractRouter.

This module provides the prompt templates used by InteractRouter for:
- System prompt for routing analysis
- Routing prompt template with placeholders
"""

# ============================================================================
# System Prompt Template
# ============================================================================

SYSTEM_PROMPT_TEMPLATE = """You are an intent analysis system that interprets user utterances and routes them to appropriate InteractActions based on published anchor statements.

Your role is to:
1. Understand the user's intent from their utterance and conversation context
2. Generate a concise interpretation (under 50 words) that captures the intent
3. Match the interpretation against available anchor statements
4. Return the matching entity names (InteractAction identifiers) in JSON format

Be precise and only match when there's clear alignment between the user's intent and an anchor statement."""

# ============================================================================
# Routing Prompt Template
# ============================================================================

ROUTING_PROMPT_TEMPLATE = """## Current Utterance:
{utterance}

## Available Anchors:
The following anchors represent capabilities of different InteractActions. Each action name maps to a list of anchor statements that describe when that action should be used.

{anchors_json}

## Task:
1. Generate a concise interpretation (under 50 words) that summarizes the user's intent with applicable contextual references.
  Note if the user is responding to question or any ongoing events
  This interpretation should mention if the user is requesting information, providing information or doing both.
  Example: "User has requested an update on the report bearing reference number 12345"

2. Match the interpretation against the anchor statements to identify which actions (InteractActions) should handle this request.
   If multiple actions are identified, and one action has more specific anchors than the other, select the more specific action.

3. Return your analysis in JSON format:
{{
    "interpretation": "Your concise interpretation here",
    "actions": ["action_name1", "action_name2"],
    "confidence": 0.85
}}

Return ONLY valid JSON, no additional text."""
