"""Utility functions for DSPy integration.

This module provides shared utility functions used across DSPy integrations
in various actions.
"""

from typing import Any, Dict, List, Optional


def format_conversation_history_for_dspy(
    history: Optional[List[Dict[str, Any]]]
) -> Optional[str]:
    """Format conversation history for DSPy signature.
    
    Converts list of message dicts to a readable string format that can be
    passed to the DSPy signature. Uses simple chronological format:
    "User: ...\nAssistant: ...\nSystem: [INTERPRETATION] ...\nSystem: [EVENT] ..."
    
    This is a shared utility function used by both PersonaAction and
    InterviewInteractAction for formatting conversation history when using DSPy.
    
    Args:
        history: List of message dictionaries with 'role' and 'content' keys
        
    Returns:
        Formatted string representation of conversation history, or None if empty
        
    Example:
        >>> history = [
        ...     {"role": "user", "content": "Hello"},
        ...     {"role": "assistant", "content": "Hi there!"},
        ...     {"role": "system", "content": "[INTERPRETATION] User greeting"}
        ... ]
        >>> formatted = format_conversation_history_for_dspy(history)
        >>> print(formatted)
        User: Hello
        Assistant: Hi there!
        System: [INTERPRETATION] User greeting
    """
    if not history:
        return None
    
    formatted = []
    for msg in history:
        # Extract role and content
        role = msg.get("role", "user")
        content = msg.get("content", "") or msg.get("text", "") or msg.get("utterance", "")
        
        # Only include messages with actual content
        if content and content.strip():
            # Normalize role names for clarity
            # Distinguish between assistant responses and system messages (interpretations/events)
            if role.lower() in ["user", "human"]:
                role_display = "User"
            elif role.lower() in ["assistant", "ai"]:
                role_display = "Assistant"
            elif role.lower() == "system":
                role_display = "System"
            else:
                role_display = role.title()
            
            formatted.append(f"{role_display}: {content}")
    
    return "\n".join(formatted) if formatted else None

