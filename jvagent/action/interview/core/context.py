"""Typed context class for interview session context.

This module provides a typed wrapper for session.context that provides
type-safe access to known context fields while still allowing arbitrary data.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .enums import ContextKey


@dataclass
class InterviewContext:
    """Typed context for interview session.
    
    Provides type-safe access to known context fields while maintaining
    compatibility with arbitrary data storage.
    
    Attributes:
        directive_override_replace_mode: Flag indicating replace mode override was used
        directive_override_append_mode: Flag indicating append mode override was used
        question_node_cache: Cache of question nodes for performance
        state_target: Target state for state transitions
        matched_training_times: Example-specific field (can be extended)
        processed: Generic processed flag
        _extra: Dictionary for any additional arbitrary data
    """
    directive_override_replace_mode: bool = False
    directive_override_append_mode: bool = False
    question_node_cache: Optional[Dict[str, Any]] = None
    state_target: Optional[str] = None
    matched_training_times: List[str] = field(default_factory=list)
    processed: bool = False
    _extra: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "InterviewContext":
        """Create InterviewContext from dictionary (session.context).
        
        Args:
            data: Dictionary from session.context (may be None)
            
        Returns:
            InterviewContext instance
        """
        if not data:
            return cls()
        
        # Extract known fields
        known_fields = {
            "directive_override_replace_mode": data.get(ContextKey.DIRECTIVE_OVERRIDE_REPLACE_MODE, False),
            "directive_override_append_mode": data.get(ContextKey.DIRECTIVE_OVERRIDE_APPEND_MODE, False),
            "question_node_cache": data.get("_question_node_cache"),
            "state_target": data.get("_state_target"),
            "matched_training_times": data.get("matched_training_times", []),
            "processed": data.get("processed", False),
        }
        
        # Store remaining fields in _extra
        known_keys = {
            ContextKey.DIRECTIVE_OVERRIDE_REPLACE_MODE,
            ContextKey.DIRECTIVE_OVERRIDE_APPEND_MODE,
            "_question_node_cache",
            "_state_target",
            "matched_training_times",
            "processed",
        }
        extra = {k: v for k, v in data.items() if k not in known_keys}
        
        return cls(_extra=extra, **known_fields)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert InterviewContext to dictionary for session.context.
        
        Returns:
            Dictionary representation suitable for session.context
        """
        result = self._extra.copy()
        
        # Add known fields
        if self.directive_override_replace_mode:
            result[ContextKey.DIRECTIVE_OVERRIDE_REPLACE_MODE] = True
        if self.directive_override_append_mode:
            result[ContextKey.DIRECTIVE_OVERRIDE_APPEND_MODE] = True
        if self.question_node_cache is not None:
            result["_question_node_cache"] = self.question_node_cache
        if self.state_target is not None:
            result["_state_target"] = self.state_target
        if self.matched_training_times:
            result["matched_training_times"] = self.matched_training_times
        if self.processed:
            result["processed"] = True
        
        return result
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get value from context (checks both typed fields and extra).
        
        Args:
            key: Context key
            default: Default value if key not found
            
        Returns:
            Value from context or default
        """
        # Check typed fields first
        if key == ContextKey.DIRECTIVE_OVERRIDE_REPLACE_MODE:
            return self.directive_override_replace_mode
        if key == ContextKey.DIRECTIVE_OVERRIDE_APPEND_MODE:
            return self.directive_override_append_mode
        if key == "_question_node_cache":
            return self.question_node_cache
        if key == "_state_target":
            return self.state_target
        if key == "matched_training_times":
            return self.matched_training_times
        if key == "processed":
            return self.processed
        
        # Check extra
        return self._extra.get(key, default)
    
    def set(self, key: str, value: Any) -> None:
        """Set value in context.
        
        Args:
            key: Context key
            value: Value to set
        """
        # Set typed fields
        if key == ContextKey.DIRECTIVE_OVERRIDE_REPLACE_MODE:
            self.directive_override_replace_mode = bool(value)
        elif key == ContextKey.DIRECTIVE_OVERRIDE_APPEND_MODE:
            self.directive_override_append_mode = bool(value)
        elif key == "_question_node_cache":
            self.question_node_cache = value
        elif key == "_state_target":
            self.state_target = value
        elif key == "matched_training_times":
            self.matched_training_times = value if isinstance(value, list) else [value]
        elif key == "processed":
            self.processed = bool(value)
        else:
            # Store in extra
            self._extra[key] = value
    
    def pop(self, key: str, default: Any = None) -> Any:
        """Remove and return value from context.
        
        Args:
            key: Context key
            default: Default value if key not found
            
        Returns:
            Removed value or default
        """
        # Handle typed fields
        if key == ContextKey.DIRECTIVE_OVERRIDE_REPLACE_MODE:
            value = self.directive_override_replace_mode
            self.directive_override_replace_mode = False
            return value
        if key == ContextKey.DIRECTIVE_OVERRIDE_APPEND_MODE:
            value = self.directive_override_append_mode
            self.directive_override_append_mode = False
            return value
        if key == "_question_node_cache":
            value = self.question_node_cache
            self.question_node_cache = None
            return value
        if key == "_state_target":
            value = self.state_target
            self.state_target = None
            return value
        if key == "matched_training_times":
            value = self.matched_training_times
            self.matched_training_times = []
            return value
        if key == "processed":
            value = self.processed
            self.processed = False
            return value
        
        # Remove from extra
        return self._extra.pop(key, default)
