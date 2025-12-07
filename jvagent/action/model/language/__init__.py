"""Language model implementations and utilities.

This package contains language model provider implementations and related utilities
for text generation and multimodal interactions.
"""

from jvagent.action.model.language.base import (
    ContentPart,
    LanguageModelAction,
    MessageContent,
    ModelActionResult,
)
from jvagent.action.model.language.openai import OpenAILanguageModelAction
from jvagent.action.model.language.openrouter import OpenRouterLanguageModelAction
from jvagent.action.model.language.templates import TemplateManager
from jvagent.action.model.language.tools import ToolCall, ToolDefinition, ToolManager

# Import endpoints module to ensure endpoints are discovered and registered
# This must be imported for endpoint discovery to work
from jvagent.action.model.language import endpoints  # noqa: F401

__all__ = [
    # Base class and types
    "LanguageModelAction",
    "ModelActionResult",
    "ContentPart",
    "MessageContent",
    # Language model implementations
    "OpenAILanguageModelAction",
    "OpenRouterLanguageModelAction",
    # Utilities
    "TemplateManager",
    "ToolDefinition",
    "ToolCall",
    "ToolManager",
]

