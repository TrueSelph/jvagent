"""Base class for language model actions.

This module provides the base class for all language model implementations
and related types for text generation and multimodal interactions.
"""

import logging
from abc import ABC, abstractmethod
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncGenerator,
    Callable,
    Dict,
    List,
    Optional,
    Union,
)

from jvspatial.core.annotations import attribute

from jvagent.action.model.base import BaseModelAction

if TYPE_CHECKING:
    from jvagent.memory.interaction import Interaction

logger = logging.getLogger(__name__)

# Type aliases for multimodal content
ContentPart = Dict[
    str, Any
]  # {"type": "text", "text": "..."} or {"type": "image_url", ...}
MessageContent = Union[
    str, List[ContentPart]
]  # Content can be string or structured parts


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
        history: Optional[List[Dict[str, Any]]] = None,
        calling_action_name: Optional[str] = None,
        thinking_content: Optional[str] = None,
        thinking_tokens: Optional[int] = None,
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
            history: Conversation history used (if any)
            calling_action_name: Name of the action that initiated this model call
            thinking_content: Extended thinking text (Anthropic extended thinking)
            thinking_tokens: Number of tokens used for extended thinking
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
        self.history = history
        self.calling_action_name = calling_action_name
        self.thinking_content = thinking_content
        self.thinking_tokens = thinking_tokens

        # Build metrics dict with usage and duration
        self.metrics: Dict[str, Any] = {}
        if usage:
            self.metrics.update(usage)
        if duration is not None:
            self.metrics["duration"] = duration

        # Track whether usage was estimated (for streaming)
        self._usage_estimated: bool = False

    def update_usage(self, usage: Dict[str, int], estimated: bool = True) -> None:
        """Update usage metrics after stream completion.

        Used to update metrics for streaming results after the stream
        has been consumed and tokens have been estimated.

        Args:
            usage: Token usage dict with prompt_tokens, completion_tokens, total_tokens
            estimated: Whether the usage is estimated (True) or actual (False)
        """
        self.metrics.update(usage)
        self._usage_estimated = estimated

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
            "history": self.history,
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
    max_tokens: int = attribute(
        default=1000, description="Maximum tokens to generate", ge=1
    )
    top_p: float = attribute(
        default=1.0, description="Nucleus sampling parameter", ge=0.0, le=1.0
    )

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
        system: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        calling_action_name: Optional[str] = None,
        response_bus: Optional[Any] = None,
        interaction: Optional[Any] = None,
        transient: bool = False,
        **kwargs: Any,
    ) -> str:
        """Generate text with optional ResponseBus publishing.

        If response_bus and interaction are provided, messages will be published
        directly to the ResponseBus (streaming chunks or final message).

        Observability metrics are automatically emitted via context-based tracking.

        Args:
            prompt: User prompt (text or multimodal content)
            stream: Whether to stream the response
            system: Optional system message
            history: Optional conversation history
            tools: Optional list of tool/function definitions
            calling_action_name: Optional name of the action calling this method
            response_bus: Optional ResponseBus instance for direct publishing
            interaction: Optional Interaction node (required if response_bus provided)
            transient: If True, skip appending published content to interaction.response
            **kwargs: Additional parameters (temperature, max_tokens, model, etc.)

        Returns:
            Generated text response
        """
        # Validate: if response_bus is provided, interaction is required
        if response_bus and not interaction:
            raise ValueError("interaction is required when response_bus is provided")

        # Ensure interaction is in context for track_usage (observability_metrics)
        # Callers like PersonaAction pass interaction when streaming; others rely on
        # walker's set_interaction. Setting here guarantees observability when we have it.
        if interaction:
            from jvagent.action.model.context import set_interaction

            set_interaction(interaction)

        # If ResponseBus is provided, extract values from interaction and publish directly
        if response_bus and interaction:
            # Extract values from interaction node
            session_id = getattr(interaction, "session_id", None)
            channel = getattr(interaction, "channel", "default")
            interaction_id = getattr(interaction, "id", None)

            if not session_id:
                raise ValueError(
                    "interaction must have session_id when response_bus is provided"
                )

            user_id = getattr(interaction, "user_id", None)

            # Non-streaming: publish adhoc and return
            if not stream:
                result = await self.query(
                    prompt,
                    stream=False,
                    system=system,
                    history=history,
                    tools=tools,
                    calling_action_name=calling_action_name,
                    **kwargs,
                )
                full_text = await result.get_response()
                await response_bus.publish(
                    session_id=session_id,
                    content=full_text,
                    channel=channel,
                    stream=False,
                    interaction_id=interaction_id,
                    interaction=interaction,
                    user_id=user_id,
                    streaming_complete=True,
                    transient=transient,
                )
                return full_text

            # Streaming: publish chunks then flush with streaming_complete=True
            result = await self.query(
                prompt,
                stream=True,
                system=system,
                history=history,
                tools=tools,
                calling_action_name=calling_action_name,
                **kwargs,
            )

            chunks: List[str] = []
            async for chunk in result.iter_stream():
                if chunk:
                    chunks.append(chunk)
                    await response_bus.publish(
                        session_id=session_id,
                        content=chunk,
                        channel=channel,
                        stream=True,
                        interaction_id=interaction_id,
                        interaction=interaction,
                        user_id=user_id,
                        streaming_complete=False,
                        transient=transient,
                    )

            full_text = "".join(chunks)
            if not result.response:
                result.response = full_text

            await response_bus.publish(
                session_id=session_id,
                content="",
                channel=channel,
                stream=True,
                interaction_id=interaction_id,
                interaction=interaction,
                user_id=user_id,
                streaming_complete=True,
                transient=transient,
            )
            return full_text

        # No ResponseBus: just return the response without publishing
        if not stream:
            result = await self.query(
                prompt,
                stream=False,
                system=system,
                history=history,
                tools=tools,
                calling_action_name=calling_action_name,
                **kwargs,
            )
            return await result.get_response()

        # Streaming without ResponseBus: collect and return
        result = await self.query(
            prompt,
            stream=True,
            system=system,
            history=history,
            tools=tools,
            calling_action_name=calling_action_name,
            **kwargs,
        )

        chunks: List[str] = []
        async for chunk in result.iter_stream():
            if chunk:
                chunks.append(chunk)

        full_text = "".join(chunks)

        # Ensure response is cached in result (for observability)
        if not result.response:
            result.response = full_text

        return full_text

    async def query(
        self,
        prompt: MessageContent,
        stream: bool = False,
        system: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        calling_action_name: Optional[str] = None,
        **kwargs: Any,
    ) -> ModelActionResult:
        """Execute a query to the language model.

        Main entry point for both sync and streaming queries. This method
        handles message formatting, routing to the appropriate implementation,
        and metrics tracking.

        Supports both text-only and multimodal (text + images) queries.
        LanguageModelAction implementations are designed to handle multimodal
        content including images, enabling rich visual understanding capabilities.

        Observability metrics are automatically emitted via context-based tracking.

        Args:
            prompt: User prompt - can be:
                - String: Simple text prompt
                - List[ContentPart]: Multimodal content with text and images
            stream: Whether to stream the response
            system: Optional system message
            history: Optional conversation history (can include multimodal messages)
            tools: Optional list of tool/function definitions
            calling_action_name: Optional name of the action calling this method
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
        # Explicitly check if model is in kwargs (even if None/empty) to ensure overrides work
        # This ensures PersonaAction's model override takes precedence over LanguageModelAction's default
        if "model" in kwargs:
            # Model was explicitly passed (even if empty/None) - use it
            model_param = (
                kwargs["model"] or self.model
            )  # Fall back to instance default if empty/None
        else:
            # Model not in kwargs - use instance default
            model_param = self.model

        query_params = {
            "model": model_param,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "top_p": kwargs.get("top_p", self.top_p),
        }
        # Preserve provider-specific kwargs (e.g., Anthropic extended thinking).
        for key, value in kwargs.items():
            if key not in query_params:
                query_params[key] = value

        # Debug logging to track model selection
        if "model" in kwargs:
            logger.debug(
                f"LanguageModelAction.query: Using model='{model_param}' "
                f"(passed from caller: '{kwargs.get('model')}', instance default: '{self.model}')"
            )

        # Convert prompt to string if it's a list (multimodal content)
        prompt_str = prompt if isinstance(prompt, str) else str(prompt)
        return await self.query_messages(
            messages=messages,
            stream=stream,
            system=system,
            history=history,
            tools=tools,
            calling_action_name=calling_action_name,
            prompt_for_observability=prompt_str,
            start_time=start_time,
            **query_params,
        )

    async def query_messages(
        self,
        messages: List[Dict[str, Any]],
        stream: bool = False,
        system: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        calling_action_name: Optional[str] = None,
        prompt_for_observability: Optional[str] = None,
        start_time: Optional[float] = None,
        **kwargs: Any,
    ) -> ModelActionResult:
        """Execute a query from already-formatted messages with standard tracking.

        This is the common path for callers that maintain full message state
        themselves (e.g., think-act-observe loops with tool messages) and still
        need the same observability/profiling behavior as query().
        """
        import time

        if start_time is None:
            start_time = time.time()

        # Route to appropriate implementation
        if stream:
            result = await self._query_stream(messages, tools, **kwargs)
        else:
            result = await self._query(messages, tools, **kwargs)

        # Calculate duration
        duration = time.time() - start_time

        # Store context for logging/observability
        result.prompt = prompt_for_observability
        result.system = system
        result.history = history

        # Store calling_action_name in result for observability
        if calling_action_name:
            result.calling_action_name = calling_action_name

        # Update metrics with duration
        result.metrics["duration"] = duration

        # Record to request profile if profiling is enabled
        try:
            from jvagent.core.profiling import record_lm_call

            lm_label = f"lm:{calling_action_name or self.__class__.__name__}"
            record_lm_call(lm_label, duration)
        except ImportError:
            pass  # Profiling module not available

        # Store result temporarily for observability (to include response in metrics)
        self._last_result = result

        # Store messages and context for token estimation (for streaming)
        if stream:
            result._messages_for_estimation = messages
            result._model_for_estimation = kwargs.get("model", self.model)
            result._provider_for_estimation = getattr(self, "provider", "")

        # Track usage metrics (including duration)
        usage_dict = {
            "prompt_tokens": result.metrics.get("prompt_tokens", 0),
            "completion_tokens": result.metrics.get("completion_tokens", 0),
            "total_tokens": result.metrics.get("total_tokens", 0),
        }

        # For streaming results, skip initial observability emission
        # We'll emit after token estimation completes to avoid duplicate entries
        if not (stream and result.is_streaming):
            await self.track_usage(usage_dict, duration)

        # For streaming results, schedule token estimation after stream completion
        if stream and result.is_streaming:
            # Create a wrapper that estimates tokens when stream is consumed
            original_stream = result.stream
            if original_stream:

                async def stream_with_estimation():
                    """Stream wrapper that estimates tokens after completion."""
                    import time

                    stream_start_time = time.time()
                    chunks = []
                    try:
                        async for chunk in original_stream:
                            chunks.append(chunk)
                            yield chunk
                    finally:
                        # After stream completes, estimate tokens if the provider
                        # did not already attach usage (e.g. Anthropic message_stop).
                        if chunks:
                            full_response = "".join(chunks)
                            # Store response for later use
                            result.response = full_response

                            # Calculate actual duration (from query start to stream completion)
                            stream_end_time = time.time()
                            actual_duration = stream_end_time - start_time
                            result.metrics["duration"] = actual_duration

                            usage_dict = {
                                "prompt_tokens": result.metrics.get("prompt_tokens", 0),
                                "completion_tokens": result.metrics.get(
                                    "completion_tokens", 0
                                ),
                                "total_tokens": result.metrics.get("total_tokens", 0),
                            }

                            try:
                                if result.metrics.get("total_tokens", 0) > 0:
                                    try:
                                        from jvagent.action.model.context import (
                                            get_interaction,
                                        )

                                        if get_interaction() is not None:
                                            await self.track_usage(
                                                usage_dict, actual_duration
                                            )
                                    except Exception as e:
                                        logger.debug(
                                            f"Failed to emit observability after stream: {e}"
                                        )
                                else:
                                    from jvagent.action.model.utils.token_estimation import (
                                        estimate_completion_tokens,
                                        estimate_prompt_tokens,
                                    )

                                    messages = getattr(
                                        result, "_messages_for_estimation", []
                                    )
                                    model = getattr(
                                        result, "_model_for_estimation", result.model
                                    )
                                    provider = getattr(
                                        result,
                                        "_provider_for_estimation",
                                        result.provider,
                                    )

                                    prompt_tokens = estimate_prompt_tokens(
                                        messages, model, provider
                                    )
                                    completion_tokens = estimate_completion_tokens(
                                        full_response, model, provider
                                    )
                                    total_tokens = prompt_tokens + completion_tokens

                                    estimated_usage = {
                                        "prompt_tokens": prompt_tokens,
                                        "completion_tokens": completion_tokens,
                                        "total_tokens": total_tokens,
                                    }
                                    result.update_usage(estimated_usage, estimated=True)

                                    try:
                                        from jvagent.action.model.context import (
                                            get_interaction,
                                        )

                                        if get_interaction() is not None:
                                            await self.track_usage(
                                                estimated_usage, actual_duration
                                            )
                                    except Exception as e:
                                        logger.debug(
                                            f"Failed to emit observability after stream: {e}"
                                        )

                            except Exception as e:
                                logger.debug(
                                    f"Failed to estimate tokens for streaming result: {e}"
                                )

                result.stream = stream_with_estimation()

        return result

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
                {
                    "type": "image_url",
                    "image_url": {"url": image_url, "detail": image_detail},
                }
            )
        elif image_base64:
            # Add data URI prefix for base64
            data_uri = f"data:image/jpeg;base64,{image_base64}"
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": data_uri, "detail": image_detail},
                }
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
                        {
                            "type": "image_url",
                            "image_url": {"url": img["url"], "detail": detail},
                        }
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
