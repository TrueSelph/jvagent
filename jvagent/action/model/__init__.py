"""Model action package for model integrations.

Provides a standardized interface for model interactions (Language Model and Embedding)
with support for both programmatic (library-style) and API usage.

LanguageModelAction implementations support both text-only and multimodal
(text + images) interactions, enabling rich visual understanding capabilities.
"""

from jvagent.action.model.base import BaseModelAction

# Alias for backward compatibility
ModelAction = BaseModelAction
from jvagent.action.model.embedding import (
    EmbeddingModelAction,
    GenericEmbeddingModelAction,
    HuggingFaceEmbeddingModelAction,
    OpenAIEmbeddingModelAction,
    OpenRouterEmbeddingModelAction,
)
from jvagent.action.model.embedding import (  # noqa: F401
    endpoints as embedding_endpoints,
)

# Import endpoints modules to ensure endpoints are discovered and registered
# This must be imported for endpoint discovery to work
from jvagent.action.model.language import endpoints  # noqa: F401
from jvagent.action.model.language import (
    OpenAILanguageModelAction,
    OpenRouterLanguageModelAction,
    TemplateManager,
    ToolCall,
    ToolDefinition,
    ToolManager,
)
from jvagent.action.model.language.base import (
    ContentPart,
    LanguageModelAction,
    MessageContent,
    ModelActionResult,
)

__all__ = [
    # Base classes
    "BaseModelAction",
    "ModelAction",
    "LanguageModelAction",
    "EmbeddingModelAction",
    # Result types
    "ModelActionResult",
    "ContentPart",
    "MessageContent",
    # LLM implementations
    "OpenAILanguageModelAction",
    "OpenRouterLanguageModelAction",
    # Embedding implementations
    "OpenAIEmbeddingModelAction",
    "HuggingFaceEmbeddingModelAction",
    "OpenRouterEmbeddingModelAction",
    "GenericEmbeddingModelAction",
    # Utilities
    "TemplateManager",
    "ToolDefinition",
    "ToolCall",
    "ToolManager",
]
