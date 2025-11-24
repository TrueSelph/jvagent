"""Model action package for LLM integrations.

Provides a standardized interface for language model interactions with
support for both programmatic (library-style) and API usage.

Supports both text-only and multimodal (text + images) interactions.
"""

from jvagent.action.model.base import ContentPart, MessageContent, ModelAction, ModelActionResult
from jvagent.action.model.openai import OpenAIModelAction
from jvagent.action.model.openrouter import OpenRouterModelAction
from jvagent.action.model.templates import TemplateManager
from jvagent.action.model.tools import ToolCall, ToolDefinition, ToolManager

# Import endpoints module to ensure endpoints are discovered and registered
# This must be imported for endpoint discovery to work
from jvagent.action.model import endpoints  # noqa: F401

__all__ = [
    "ModelAction",
    "ModelActionResult",
    "ContentPart",
    "MessageContent",
    "OpenAIModelAction",
    "OpenRouterModelAction",
    "TemplateManager",
    "ToolDefinition",
    "ToolCall",
    "ToolManager",
]
