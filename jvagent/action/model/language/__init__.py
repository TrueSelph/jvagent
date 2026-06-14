"""Language model implementations and utilities.

This package contains language model provider implementations and related utilities
for text generation and multimodal interactions.
"""

# Import endpoints module to ensure endpoints are discovered and registered
# This must be imported for endpoint discovery to work
from jvagent.action.model.language import endpoints  # noqa: F401
from jvagent.action.model.language.anthropic import AnthropicLanguageModelAction
from jvagent.action.model.language.base import (
    ContentPart,
    LanguageModelAction,
    MessageContent,
    ModelActionResult,
)
from jvagent.action.model.language.ollama import OllamaLanguageModelAction
from jvagent.action.model.language.openai import OpenAILanguageModelAction
from jvagent.action.model.language.openrouter import OpenRouterLanguageModelAction
from jvagent.action.model.language.templates import TemplateManager
from jvagent.action.model.language.tools import ToolCall, ToolDefinition, ToolManager

__all__ = [
    # Base class and types
    "LanguageModelAction",
    "ModelActionResult",
    "ContentPart",
    "MessageContent",
    # Language model implementations
    "AnthropicLanguageModelAction",
    "OpenAILanguageModelAction",
    "OllamaLanguageModelAction",
    "OpenRouterLanguageModelAction",
    # Utilities
    "TemplateManager",
    "ToolDefinition",
    "ToolCall",
    "ToolManager",
]
