"""Base class for language model actions.

This module provides the base class for all language model implementations
and related types for text generation and multimodal interactions.
"""

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, AsyncGenerator, Callable, Dict, List, Optional, Union

from jvspatial.core.annotations import attribute

from jvagent.action.model.base import BaseModelAction

if TYPE_CHECKING:
    from jvagent.memory.interaction import Interaction

logger = logging.getLogger(__name__)

# Type aliases for multimodal content
ContentPart = Dict[str, Any]  # {"type": "text", "text": "..."} or {"type": "image_url", ...}
MessageContent = Union[str, List[ContentPart]]  # Content can be string or structured parts


class ModelActionResult:
    """Encapsulates the result of a model action query.

    Supports both synchronous and streaming responses, providing a unified
    interface for API endpoints and programmatic calls from other actions.

    Attributes:
        prompt: The prompt that produced the response
        system: System message used (if any)
        response: Complete response text (sync mode)
        stream: Async generator yielding chunks (streaming mode)
        metrics: Query metrics including token usage and duration
        model: Model identifier used for the query
        provider: Provider name (e.g., 'openai', 'openrouter')
        finish_reason: Completion reason ('stop', 'length', 'tool_calls', etc.)
        tool_calls: List of function calls (if any)
        is_streaming: Whether this result is streaming

    Examples:
        Sync usage:
        >>> result = await model_action.query_sync("Hello")
        >>> text = await result.get_response()
        >>> print(f"Used {result.metrics['total_tokens']} tokens in {result.metrics['duration']}s")

        Streaming usage:
        >>> result = await model_action.query_stream("Tell me a story")
        >>> async for chunk in result.iter_stream():
        ...     print(chunk, end="", flush=True)
    """

    def __init__(
        self,
        response: Optional[str] = None,
        stream: Optional[AsyncGenerator[str, None]] = None,
        usage: Optional[Dict[str, int]] = None,
        model: str = "",
        provider: str = "",
        finish_reason: Optional[str] = None,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        duration: Optional[float] = None,
        prompt: Optional[str] = None,
        system: Optional[str] = None,
    ):
        """Initialize a model action result.

        Args:
            response: Complete response text (for sync queries)
            stream: Async generator for streaming responses
            usage: Token usage dict with prompt_tokens, completion_tokens, total_tokens
            model: Model identifier
            provider: Provider name
            finish_reason: Reason for completion
            tool_calls: Function/tool calls made (if any)
            duration: Query duration in seconds
            prompt: The prompt that produced the response
            system: System message used (if any)
        """
        self.response = response
        self.stream = stream
        self.model = model
        self.provider = provider
        self.finish_reason = finish_reason
        self.tool_calls = tool_calls or []
        self.is_streaming = stream is not None
        self.prompt = prompt
        self.system = system

        # Build metrics dict with usage and duration
        self.metrics: Dict[str, Any] = {}
        if usage:
            self.metrics.update(usage)
        if duration is not None:
            self.metrics["duration"] = duration

    async def get_response(self) -> str:
        """Get the complete response text.

        For streaming results, this will consume the stream and return
        the complete concatenated text. The response is cached for
        subsequent calls.

        Returns:
            Complete response text
        """
        if self.response:
            return self.response

        if self.stream:
            chunks = []
            async for chunk in self.stream:
                chunks.append(chunk)
            self.response = "".join(chunks)
            return self.response

        return ""

    async def iter_stream(self) -> AsyncGenerator[str, None]:
        """Iterate over streaming response chunks.

        For non-streaming results, yields the complete response as a single chunk.
        For streaming results, yields chunks as they arrive.

        Yields:
            Response text chunks
        """
        if not self.is_streaming:
            yield self.response or ""
        else:
            if self.stream:
                async for chunk in self.stream:
                    yield chunk

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary for API responses.

        Returns:
            Dictionary representation of the result
        """
        return {
            "prompt": self.prompt,
            "system": self.system,
            "response": self.response,
            "metrics": self.metrics,
            "model": self.model,
            "provider": self.provider,
            "finish_reason": self.finish_reason,
            "tool_calls": self.tool_calls,
            "is_streaming": self.is_streaming,
        }


class LanguageModelAction(BaseModelAction, ABC):
    """Base class for language model actions (text generation and multimodal).

    This abstract class defines the standard interface that all language model provider
    implementations must implement. It provides both programmatic (library-style)
    and API interfaces for language model interactions.

    LanguageModelAction implementations support both text-only and multimodal
    (text + images) queries, enabling rich interactions with visual content.

    Providers should implement:
    - _query(): Execute a synchronous query
    - _query_stream(): Execute a streaming query

    Additional Attributes (Language model-specific):
        temperature: Sampling temperature (0.0 to 2.0)
        max_tokens: Maximum tokens to generate
        top_p: Nucleus sampling parameter

    Examples:
        Programmatic usage from another action:
        >>> model = await OpenAILanguageModelAction.get(action_id)
        >>> result = await model.query_sync("Explain quantum physics")
        >>> response = await result.get_response()

        Streaming usage:
        >>> result = await model.query_stream("Write a story")
        >>> async for chunk in result.iter_stream():
        ...     print(chunk, end="")

        Multimodal usage:
        >>> content = model.create_image_content(
        ...     "What's in this image?",
        ...     image_url="https://example.com/image.jpg"
        ... )
        >>> result = await model.query(content)
    """

    # Language model-specific configuration attributes
    temperature: float = attribute(
        default=0.7, description="Sampling temperature (0.0-2.0)", ge=0.0, le=2.0
    )
    max_tokens: int = attribute(default=1000, description="Maximum tokens to generate", ge=1)
    top_p: float = attribute(default=1.0, description="Nucleus sampling parameter", ge=0.0, le=1.0)

    # ============================================================================
    # Abstract Methods (Provider Implementation)
    # ============================================================================

    @abstractmethod
    async def _query(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ModelActionResult:
        """Execute a synchronous query to the language model provider.

        This method must be implemented by provider subclasses to handle
        the actual API call and return a complete response.

        Args:
            messages: List of message dicts with 'role' and 'content'
            tools: Optional list of tool/function definitions
            **kwargs: Additional provider-specific parameters

        Returns:
            ModelActionResult with complete response
        """
        pass

    @abstractmethod
    async def _query_stream(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ModelActionResult:
        """Execute a streaming query to the language model provider.

        This method must be implemented by provider subclasses to handle
        streaming API calls and return an async generator.

        Args:
            messages: List of message dicts with 'role' and 'content'
            tools: Optional list of tool/function definitions
            **kwargs: Additional provider-specific parameters

        Returns:
            ModelActionResult with streaming generator
        """
        pass

    # ============================================================================
    # Programmatic Interface (Public API)
    # ============================================================================

    async def generate(
        self,
        prompt: MessageContent,
        stream: bool = False,
        on_stream_chunk: Optional[Callable[[str], None]] = None,
        on_stream_end: Optional[Callable[[str], None]] = None,
        system: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        interaction: Optional["Interaction"] = None,
        **kwargs: Any,
    ) -> str:
        """Generate text with optional streaming callbacks.

        If stream=True and callbacks are provided, partial chunks are emitted
        via on_stream_chunk. The full text is returned in all cases.
        
        When interaction is provided, model results are automatically logged
        to the interaction's model_log. This provides transparent tracking
        without requiring explicit logging statements in actions.

        Args:
            prompt: User prompt (text or multimodal content)
            stream: Whether to stream the response
            on_stream_chunk: Optional callback for each stream chunk
            on_stream_end: Optional callback when streaming completes
            system: Optional system message
            history: Optional conversation history
            tools: Optional list of tool/function definitions
            interaction: Optional Interaction object for automatic result logging
            **kwargs: Additional parameters (temperature, max_tokens, model, etc.)

        Returns:
            Generated text response
        """
        # Fast path: no streaming requested
        if not stream or on_stream_chunk is None:
            result = await self.query(
                prompt,
                stream=False,
                system=system,
                history=history,
                tools=tools,
                interaction=interaction,
                **kwargs,
            )
            full_text = await result.get_response()
            if on_stream_end:
                on_stream_end(full_text)
            
            # Auto-log model result if interaction provided
            if interaction:
                self._log_model_result(interaction, result, full_text, stream=False, **kwargs)
            
            return full_text

        # Streaming path
        result = await self.query(
            prompt,
            stream=True,
            system=system,
            history=history,
            tools=tools,
            interaction=interaction,
            **kwargs,
        )

        chunks: List[str] = []
        async for chunk in result.iter_stream():
            if chunk:
                chunks.append(chunk)
                try:
                    on_stream_chunk(chunk)
                except Exception as exc:  # protect caller
                    logger.warning("on_stream_chunk callback raised: %s", exc, exc_info=True)

        full_text = "".join(chunks)
        if on_stream_end:
            try:
                on_stream_end(full_text)
            except Exception as exc:
                logger.warning("on_stream_end callback raised: %s", exc, exc_info=True)
        
        # Auto-log model result if interaction provided (after streaming completes)
        if interaction:
            self._log_model_result(interaction, result, full_text, stream=True, **kwargs)
        
        return full_text

    async def query(
        self,
        prompt: MessageContent,
        stream: bool = False,
        system: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        interaction: Optional["Interaction"] = None,
        **kwargs: Any,
    ) -> ModelActionResult:
        """Execute a query to the language model.

        Main entry point for both sync and streaming queries. This method
        handles message formatting, routing to the appropriate implementation,
        and metrics tracking.

        Supports both text-only and multimodal (text + images) queries.
        LanguageModelAction implementations are designed to handle multimodal
        content including images, enabling rich visual understanding capabilities.

        Note: When interaction is provided, model results are automatically
        logged via generate(). This method does not log directly - use generate()
        for automatic logging, or query() if you need the ModelActionResult object.

        Args:
            prompt: User prompt - can be:
                - String: Simple text prompt
                - List[ContentPart]: Multimodal content with text and images
            stream: Whether to stream the response
            system: Optional system message
            history: Optional conversation history (can include multimodal messages)
            tools: Optional list of tool/function definitions
            interaction: Optional Interaction object (passed through to generate() for logging)
            **kwargs: Additional parameters (temperature, max_tokens, etc.)

        Returns:
            ModelActionResult with response or stream

        Examples:
            Text query:
            >>> result = await model.query("Hello", stream=False)

            Multimodal query:
            >>> content = model.create_image_content(
            ...     "What's in this image?",
            ...     image_url="https://example.com/image.jpg"
            ... )
            >>> result = await model.query(content, stream=False)
        """
        import time

        # Start timing
        start_time = time.time()

        # Format messages
        messages = self.format_messages(prompt, system, history)

        # Merge kwargs with instance defaults
        query_params = {
            "model": kwargs.get("model", self.model),
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "top_p": kwargs.get("top_p", self.top_p),
        }

        # Route to appropriate implementation
        if stream:
            result = await self._query_stream(messages, tools, **query_params)
        else:
            result = await self._query(messages, tools, **query_params)

        # Calculate duration
        duration = time.time() - start_time

        # Store the prompt and system in the result for logging
        # Convert prompt to string if it's a list (multimodal content)
        prompt_str = prompt if isinstance(prompt, str) else str(prompt)
        result.prompt = prompt_str
        result.system = system

        # Update metrics with duration
        result.metrics["duration"] = duration

        # Track usage metrics (including duration)
        # Extract usage dict from metrics for tracking
        usage_dict = {
            "prompt_tokens": result.metrics.get("prompt_tokens", 0),
            "completion_tokens": result.metrics.get("completion_tokens", 0),
            "total_tokens": result.metrics.get("total_tokens", 0),
        }
        if any(usage_dict.values()):  # Only track if there are actual token counts
            self.track_usage(usage_dict, duration)

        return result

    def _log_model_result(
        self,
        interaction: "Interaction",
        result: ModelActionResult,
        response_text: str,
        stream: bool,
        **kwargs: Any,
    ) -> None:
        """Automatically log model result to interaction.
        
        This is called transparently when interaction is provided to generate().
        Extracts relevant metadata from the result and logs it to the interaction's
        model_log. Errors in logging are caught to prevent breaking model calls.
        
        Args:
            interaction: The Interaction object to log to
            result: The ModelActionResult from the query
            response_text: The full response text
            stream: Whether this was a streaming call
            **kwargs: Additional parameters (may include model name override)
        """
        try:
            # Extract model name (from kwargs override, result, or instance)
            model_name = kwargs.get("model") or result.model or self.model or ""
            
            # Extract provider (from result or instance)
            provider = result.provider or getattr(self, "provider", "")
            
            # Build model result dict
            model_result: Dict[str, Any] = {
                "response": response_text,
                "model": model_name,
                "is_streaming": stream,
            }
            
            # Add provider if available
            if provider:
                model_result["provider"] = provider
            
            # Add metrics if available
            if result.metrics:
                model_result["metrics"] = result.metrics
            
            # Add finish reason if available
            if result.finish_reason:
                model_result["finish_reason"] = result.finish_reason
            
            # Add tool calls if available
            if result.tool_calls:
                model_result["tool_calls"] = result.tool_calls
            
            # Log to interaction
            interaction.add_model_result(model_result)
        except Exception as e:
            # Logging failures should not break model calls
            logger.warning(f"Failed to log model result to interaction: {e}", exc_info=True)

    async def query_sync(
        self,
        prompt: MessageContent,
        system: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ModelActionResult:
        """Execute a synchronous (non-streaming) query.

        Convenience method for programmatic usage when streaming is not needed.
        Supports both text and multimodal content.

        Args:
            prompt: User prompt (string or multimodal content)
            system: Optional system message
            history: Optional conversation history
            tools: Optional list of tool/function definitions
            **kwargs: Additional parameters

        Returns:
            ModelActionResult with complete response
        """
        return await self.query(
            prompt, stream=False, system=system, history=history, tools=tools, **kwargs
        )

    async def query_stream(
        self,
        prompt: MessageContent,
        system: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ModelActionResult:
        """Execute a streaming query.

        Convenience method for programmatic usage when streaming is desired.
        Supports both text and multimodal content.

        Args:
            prompt: User prompt (string or multimodal content)
            system: Optional system message
            history: Optional conversation history
            tools: Optional list of tool/function definitions
            **kwargs: Additional parameters

        Returns:
            ModelActionResult with streaming generator
        """
        return await self.query(
            prompt, stream=True, system=system, history=history, tools=tools, **kwargs
        )

    # ============================================================================
    # Helper Methods
    # ============================================================================

    def format_messages(
        self,
        prompt: MessageContent,
        system: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Format a prompt into the messages format expected by language model APIs.

        Supports both text-only and multimodal (text + images) content.
        LanguageModelAction implementations can process multimodal inputs,
        enabling interactions with visual content alongside text.

        Args:
            prompt: User prompt - string or list of content parts
            system: Optional system message
            history: Optional conversation history (can include multimodal messages)

        Returns:
            List of message dicts with 'role' and 'content' keys
        """
        messages: List[Dict[str, Any]] = []

        # Add system message if provided
        if system:
            messages.append({"role": "system", "content": system})

        # Add conversation history if provided
        if history:
            messages.extend(history)

        # Add current user prompt (supports both string and structured content)
        messages.append({"role": "user", "content": prompt})

        return messages

    def create_image_content(
        self,
        text: str,
        image_url: Optional[str] = None,
        image_base64: Optional[str] = None,
        image_detail: str = "auto",
    ) -> List[ContentPart]:
        """Create multimodal content with text and image.

        Helper method to construct content for multimodal queries.
        Supports images from URLs or base64-encoded data.

        Args:
            text: Text prompt/query
            image_url: URL to image (http/https)
            image_base64: Base64-encoded image data (without data URI prefix)
            image_detail: Image detail level - "auto", "low", or "high" (OpenAI)

        Returns:
            List of content parts for multimodal message

        Examples:
            With URL:
            >>> content = model.create_image_content(
            ...     "What's in this image?",
            ...     image_url="https://example.com/image.jpg"
            ... )

            With base64:
            >>> import base64
            >>> with open("image.jpg", "rb") as f:
            ...     img_data = base64.b64encode(f.read()).decode()
            >>> content = model.create_image_content(
            ...     "Analyze this",
            ...     image_base64=img_data
            ... )
        """
        content: List[ContentPart] = [{"type": "text", "text": text}]

        if image_url:
            content.append(
                {"type": "image_url", "image_url": {"url": image_url, "detail": image_detail}}
            )
        elif image_base64:
            # Add data URI prefix for base64
            data_uri = f"data:image/jpeg;base64,{image_base64}"
            content.append(
                {"type": "image_url", "image_url": {"url": data_uri, "detail": image_detail}}
            )

        return content

    def create_multimodal_content(
        self,
        text: str,
        images: Optional[List[Dict[str, str]]] = None,
    ) -> List[ContentPart]:
        """Create multimodal content with text and multiple images.

        Advanced helper for multiple images in a single message.

        Args:
            text: Text prompt/query
            images: List of image dicts with keys:
                - 'url': Image URL, or
                - 'base64': Base64-encoded image data
                - 'detail': Optional detail level ("auto", "low", "high")

        Returns:
            List of content parts for multimodal message

        Examples:
            >>> content = model.create_multimodal_content(
            ...     "Compare these images",
            ...     images=[
            ...         {"url": "https://example.com/img1.jpg"},
            ...         {"url": "https://example.com/img2.jpg", "detail": "high"}
            ...     ]
            ... )
        """
        content: List[ContentPart] = [{"type": "text", "text": text}]

        if images:
            for img in images:
                detail = img.get("detail", "auto")

                if "url" in img:
                    content.append(
                        {"type": "image_url", "image_url": {"url": img["url"], "detail": detail}}
                    )
                elif "base64" in img:
                    data_uri = f"data:image/jpeg;base64,{img['base64']}"
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": data_uri, "detail": detail},
                        }
                    )

        return content

    async def apply_template(self, template_name: str, **variables: Any) -> str:
        """Apply a prompt template with variables.

        Args:
            template_name: Name of the template
            **variables: Template variables

        Returns:
            Rendered template string
        """
        # Import here to avoid circular dependency
        from jvagent.action.model.language.templates import TemplateManager

        manager = TemplateManager(self)
        return await manager.render(template_name, **variables)

