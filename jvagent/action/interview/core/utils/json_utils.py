"""JSON utility functions for interview module.

This module provides utilities for parsing and extracting JSON data from various sources.
"""

import json
import logging
import re
from typing import Any, Dict

logger = logging.getLogger(__name__)


def extract_json(response: str, context: str = "") -> Dict[str, Any]:
    """Extract JSON from response string.
    
    Attempts to parse the response as JSON. If that fails, tries to extract
    a JSON object from within the text using regex pattern matching.
    
    Args:
        response: Response string that may contain JSON
        context: Optional context string for logging (e.g., class name)
        
    Returns:
        Parsed JSON dictionary, or empty dict if extraction fails
        
    Example:
        >>> extract_json('{"key": "value"}')
        {'key': 'value'}
        
        >>> extract_json('Here is the data: {"key": "value"} and more text')
        {'key': 'value'}
        
        >>> extract_json('No JSON here')
        {}
    """
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        # Try to extract JSON from text using regex
        json_match = re.search(r'\{[^{}]*\}', response)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        
        # Log warning with context if provided
        if context:
            logger.warning(f"{context}: Failed to extract JSON from response")
        else:
            logger.warning("Failed to extract JSON from response")
        
        return {}
