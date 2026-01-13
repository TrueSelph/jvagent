"""DSPy LM adapter for jvagent's LanguageModelAction.

This module provides a bridge between DSPy's language model interface
and jvagent's LanguageModelAction, allowing DSPy modules to use jvagent's
existing model infrastructure with full caching and usage tracking support.
"""

import asyncio
import logging
from typing import Any, Optional

import dspy
from dspy.clients.base_lm import BaseLM

from jvagent.action.model.language.base import LanguageModelAction

logger = logging.getLogger(__name__)


class DSPyLM(BaseLM):
    """DSPy LM adapter for jvagent's LanguageModelAction.
    
    This adapter wraps a jvagent LanguageModelAction instance to make it
    compatible with DSPy's BaseLM interface. It handles message format
    conversion, async/sync bridging, caching, and usage metrics extraction.
    
    Features:
        - Full DSPy caching support via request_cache decorator
        - Usage metrics extraction from ModelActionResult
        - History tracking via BaseLM
        - Callback support via BaseLM decorators
        - Compatible with all DSPy optimizers and teleprompters
        - Model parameter override support (allows agent.yaml overrides)
        - Streaming support for real-time response generation
    
    Args:
        model_action: The jvagent LanguageModelAction instance to wrap
        model_type: Type of model ("chat" or "text"), defaults to "chat"
        model: Optional model identifier (overrides model_action.model if provided)
        temperature: Sampling temperature (overrides model_action.temperature if provided)
        max_tokens: Maximum tokens to generate (overrides model_action.max_tokens if provided)
        cache: Whether to enable DSPy caching (defaults to False, disabled to prevent bootstrap errors)
        **kwargs: Additional arguments passed to BaseLM
    """
    
    def __init__(
        self,
        model_action: LanguageModelAction,
        model_type: str = "chat",
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        cache: bool = False,
        **kwargs
    ):
        """Initialize the adapter with a jvagent LanguageModelAction.
        
        Args:
            model_action: The jvagent LanguageModelAction instance to wrap
            model_type: Type of model ("chat" or "text"), defaults to "chat"
            model: Optional model identifier to override model_action.model
            temperature: Sampling temperature (overrides model_action.temperature if provided)
            max_tokens: Maximum tokens to generate (overrides model_action.max_tokens if provided)
            cache: Whether to enable DSPy caching (defaults to False, disabled to prevent bootstrap errors)
            **kwargs: Additional arguments passed to BaseLM
        """
        # Use provided model override, or fall back to model_action's model identifier
        model_name = model if model is not None else getattr(model_action, "model", "unknown")
        
        # Use provided temperature/max_tokens or fall back to model_action defaults
        temp = temperature if temperature is not None else getattr(model_action, "temperature", 0.7)
        max_toks = max_tokens if max_tokens is not None else getattr(model_action, "max_tokens", 1000)
        
        super().__init__(
            model=model_name,
            model_type=model_type,
            temperature=temp,
            max_tokens=max_toks,
            cache=cache,
            **kwargs
        )
        
        self.model_action = model_action
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        
        # Cache is disabled by default - use uncached methods directly
        # Note: Streaming requests always bypass caching
        self._cached_forward = self._uncached_forward
        self._cached_aforward = self._uncached_aforward
    
    def forward(
        self,
        prompt: Optional[str] = None,
        messages: Optional[list[dict[str, Any]]] = None,
        **kwargs
    ):
        """Synchronous forward pass with caching support.
        
        DSPy's BaseLM expects synchronous calls, but jvagent's LanguageModelAction
        is async. This method bridges the gap by running the async call in an
        event loop. Caching is handled via the request_cache decorator.
        
        Args:
            prompt: Optional prompt string (if messages not provided)
            messages: Optional list of message dicts in DSPy format
            **kwargs: Additional arguments (temperature, max_tokens, stream, etc.)
            
        Returns:
            OpenAI-compatible response object (or streaming response if stream=True)
        """
        # Check if streaming is requested - bypass cache for streaming
        stream = kwargs.get("stream", False)
        if stream:
            # Streaming requests bypass caching
            return self._uncached_forward(prompt=prompt, messages=messages, **kwargs)
        
        # Use cached version if enabled
        return self._cached_forward(prompt=prompt, messages=messages, **kwargs)
    
    def _uncached_forward(
        self,
        prompt: Optional[str] = None,
        messages: Optional[list[dict[str, Any]]] = None,
        **kwargs
    ):
        """Uncached synchronous forward pass (internal).
        
        Handles both streaming and non-streaming requests.
        """
        # Check if streaming is requested
        stream = kwargs.get("stream", False)
        
        # Try to get existing event loop, or create new one if needed
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is already running, we need to use a different approach
                # Create a new thread with its own event loop
                return self._run_in_thread(prompt, messages, **kwargs)
        except RuntimeError:
            # No event loop exists, create one
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        try:
            return loop.run_until_complete(
                self._uncached_aforward(prompt=prompt, messages=messages, **kwargs)
            )
        finally:
            # Clean up if we created a new loop
            if not self._event_loop:
                loop.close()
    
    def _run_in_thread(self, prompt: Optional[str], messages: Optional[list[dict[str, Any]]], **kwargs):
        """Run async call in a separate thread with its own event loop."""
        import concurrent.futures
        
        def run_async():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                return new_loop.run_until_complete(
                    self._uncached_aforward(prompt=prompt, messages=messages, **kwargs)
                )
            finally:
                new_loop.close()
        
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_async)
            return future.result()
    
    async def aforward(
        self,
        prompt: Optional[str] = None,
        messages: Optional[list[dict[str, Any]]] = None,
        **kwargs
    ):
        """Async forward pass with caching support.
        
        Converts DSPy message format to jvagent format and calls the
        LanguageModelAction's query method to get usage metrics.
        
        Args:
            prompt: Optional prompt string (if messages not provided)
            messages: Optional list of message dicts in DSPy format
            **kwargs: Additional arguments (temperature, max_tokens, stream, etc.)
            
        Returns:
            OpenAI-compatible response object (mock object with .choices attribute)
            For streaming, returns a StreamingMockResponse with async generator support
        """
        # Check if streaming is requested - bypass cache for streaming
        stream = kwargs.get("stream", False)
        if stream:
            # Streaming requests bypass caching
            return await self._uncached_aforward(prompt=prompt, messages=messages, **kwargs)
        
        # Use cached version if enabled
        return await self._cached_aforward(prompt=prompt, messages=messages, **kwargs)
    
    async def _uncached_aforward(
        self,
        prompt: Optional[str] = None,
        messages: Optional[list[dict[str, Any]]] = None,
        **kwargs
    ):
        """Uncached async forward pass (internal).
        
        Handles both streaming and non-streaming requests.
        Uses query() to get usage metrics and streaming support.
        """
        # Check if streaming is requested
        stream = kwargs.pop("stream", False)
        
        if stream:
            return await self._uncached_aforward_stream(
                prompt=prompt, 
                messages=messages, 
                **kwargs
            )
        
        # Non-streaming path (existing logic)
        # Convert DSPy messages format to jvagent format
        system_message = None
        user_prompt = prompt
        
        if messages:
            # DSPy messages format: [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]
            # Extract system message and user content
            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                
                if role == "system":
                    system_message = content
                elif role == "user":
                    # If we already have a user prompt, append to it
                    if user_prompt:
                        user_prompt = f"{user_prompt}\n{content}"
                    else:
                        user_prompt = content
        
        # If no user prompt from messages, use the prompt parameter
        if not user_prompt:
            user_prompt = prompt or ""
        
        # Extract conversation history if present in messages
        history = None
        if messages and len(messages) > 2:  # More than system + user
            # Convert message history to jvagent format
            history = []
            for msg in messages[:-1]:  # Exclude last user message
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role in ["user", "assistant"]:
                    history.append({
                        "role": role,
                        "content": content
                    })
        
        # Merge kwargs with instance defaults
        merged_kwargs = {**self.kwargs, **kwargs}
        
        # Extract model parameters
        model_param = merged_kwargs.pop("model", self.model)
        temperature = merged_kwargs.pop("temperature", self.kwargs.get("temperature", 0.7))
        max_tokens = merged_kwargs.pop("max_tokens", self.kwargs.get("max_tokens", 1000))
        
        # Use query() to get usage metrics
        # query() returns ModelActionResult with usage information
        try:
            result = await self.model_action.query(
                prompt=user_prompt,
                stream=False,
                system=system_message,
                history=history,
                calling_action_name="DSPy",
                model=model_param,
                temperature=temperature,
                max_tokens=max_tokens,
                **{k: v for k, v in merged_kwargs.items() if k not in ["model", "temperature", "max_tokens"]}
            )
            
            # Get response text from result
            response_text = await result.get_response()
            
            # Extract usage metrics from result
            usage_dict = result.metrics if hasattr(result, 'metrics') else {}
            
            # Log usage metrics for non-streaming
            if usage_dict:
                logger.debug(
                    f"DSPyLM (non-streaming): Usage - "
                    f"prompt_tokens={usage_dict.get('prompt_tokens', 0)}, "
                    f"completion_tokens={usage_dict.get('completion_tokens', 0)}, "
                    f"total_tokens={usage_dict.get('total_tokens', 0)}, "
                    f"duration={usage_dict.get('duration', 0):.3f}s"
                )
            
            # Convert jvagent response to OpenAI-compatible format
            return self._create_openai_response(
                response_text, 
                model_param,
                usage_dict=usage_dict
            )
            
        except Exception as e:
            logger.error(f"DSPyLM: Error calling model_action.query: {e}", exc_info=True)
            raise
    
    async def _uncached_aforward_stream(
        self,
        prompt: Optional[str] = None,
        messages: Optional[list[dict[str, Any]]] = None,
        **kwargs
    ):
        """Uncached async streaming forward pass (internal).
        
        Handles streaming requests by using model_action.query(stream=True)
        and returning a streaming-compatible response object.
        """
        # Convert DSPy messages format to jvagent format (same as non-streaming)
        system_message = None
        user_prompt = prompt
        
        if messages:
            # DSPy messages format: [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]
            # Extract system message and user content
            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                
                if role == "system":
                    system_message = content
                elif role == "user":
                    # If we already have a user prompt, append to it
                    if user_prompt:
                        user_prompt = f"{user_prompt}\n{content}"
                    else:
                        user_prompt = content
        
        # If no user prompt from messages, use the prompt parameter
        if not user_prompt:
            user_prompt = prompt or ""
        
        # Extract conversation history if present in messages
        history = None
        if messages and len(messages) > 2:  # More than system + user
            # Convert message history to jvagent format
            history = []
            for msg in messages[:-1]:  # Exclude last user message
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role in ["user", "assistant"]:
                    history.append({
                        "role": role,
                        "content": content
                    })
        
        # Merge kwargs with instance defaults
        merged_kwargs = {**self.kwargs, **kwargs}
        
        # Extract model parameters
        model_param = merged_kwargs.pop("model", self.model)
        temperature = merged_kwargs.pop("temperature", self.kwargs.get("temperature", 0.7))
        max_tokens = merged_kwargs.pop("max_tokens", self.kwargs.get("max_tokens", 1000))
        
        # Use query() with streaming enabled
        try:
            result = await self.model_action.query(
                prompt=user_prompt,
                stream=True,  # Enable streaming
                system=system_message,
                history=history,
                calling_action_name="DSPy",
                model=model_param,
                temperature=temperature,
                max_tokens=max_tokens,
                **{k: v for k, v in merged_kwargs.items() if k not in ["model", "temperature", "max_tokens"]}
            )
            
            # Extract usage metrics from result (may be estimated for streaming)
            # Note: For streaming, metrics may be updated after stream completion via update_usage()
            # The StreamingMockUsage will access result.metrics dynamically to get the latest values
            usage_dict = result.metrics if hasattr(result, 'metrics') else {}
            
            # Log initial usage (may be empty/estimated for streaming)
            if usage_dict:
                logger.debug(
                    f"DSPyLM (streaming): Initial usage - "
                    f"prompt_tokens={usage_dict.get('prompt_tokens', 0)}, "
                    f"completion_tokens={usage_dict.get('completion_tokens', 0)}, "
                    f"total_tokens={usage_dict.get('total_tokens', 0)}"
                )
            
            # Convert jvagent streaming response to OpenAI-compatible format
            # Pass the result object so StreamingMockUsage can access updated metrics after streaming
            return self._create_streaming_response(result, model_param, usage_dict)
            
        except Exception as e:
            logger.error(f"DSPyLM: Error calling model_action.query (streaming): {e}", exc_info=True)
            raise
    
    def _create_openai_response(
        self, 
        response_text: str, 
        model: str,
        usage_dict: Optional[dict] = None
    ):
        """Create an OpenAI-compatible response object from jvagent's response.
        
        Args:
            response_text: The text response from jvagent
            model: Model identifier
            usage_dict: Optional usage metrics dictionary from ModelActionResult
            
        Returns:
            Mock object with OpenAI response structure
        """
        # Create a mock response object that mimics OpenAI's ChatCompletion format
        class MockChoice:
            def __init__(self, content: str):
                self.message = MockMessage(content)
                self.finish_reason = "stop"
                self.index = 0
        
        class MockMessage:
            def __init__(self, content: str):
                self.content = content
                self.role = "assistant"
        
        class MockUsage(dict):
            """Mock usage object that can be converted to dict.
            
            DSPy expects usage to be convertible via dict(response.usage),
            so we inherit from dict and set the attributes as dict items.
            Extracts actual usage from ModelActionResult if available.
            """
            def __init__(self, usage_dict: Optional[dict] = None):
                super().__init__()
                # Extract usage from ModelActionResult if available
                if usage_dict:
                    prompt_tokens = usage_dict.get("prompt_tokens", 0)
                    completion_tokens = usage_dict.get("completion_tokens", 0)
                    total_tokens = usage_dict.get("total_tokens", 0)
                else:
                    prompt_tokens = 0
                    completion_tokens = 0
                    total_tokens = 0
                
                # Set as dict items (for dict() conversion)
                self['prompt_tokens'] = prompt_tokens
                self['completion_tokens'] = completion_tokens
                self['total_tokens'] = total_tokens
                
                # Also set as attributes for compatibility
                self.prompt_tokens = prompt_tokens
                self.completion_tokens = completion_tokens
                self.total_tokens = total_tokens
        
        class MockResponse:
            def __init__(self, text: str, model: str, usage_dict: Optional[dict] = None):
                self.choices = [MockChoice(text)]
                self.model = model
                self.usage = MockUsage(usage_dict)
                self.id = "mock-response-id"
                self.object = "chat.completion"
                self.created = 0
        
        return MockResponse(response_text, model, usage_dict)
    
    def _create_streaming_response(
        self,
        result: Any,  # ModelActionResult
        model: str,
        usage_dict: Optional[dict] = None
    ):
        """Create a streaming-compatible response object from jvagent's streaming result.
        
        Args:
            result: ModelActionResult with streaming enabled
            model: Model identifier
            usage_dict: Optional usage metrics dictionary (may be estimated for streaming)
            
        Returns:
            StreamingMockResponse object with async generator support
        """
        from jvagent.action.model.language.base import ModelActionResult
        
        # Create streaming response wrapper
        class StreamingMockChoice:
            """Streaming choice that provides lazy content access."""
            def __init__(self, streaming_response):
                self.streaming_response = streaming_response
                self.finish_reason = "stop"
                self.index = 0
                self._message = None
            
            @property
            def message(self):
                """Lazy message access - collects stream on first access."""
                if self._message is None:
                    # This will be populated when content is accessed
                    self._message = StreamingMockMessage(self.streaming_response)
                return self._message
        
        class StreamingMockMessage:
            """Streaming message that provides lazy content access."""
            def __init__(self, streaming_response):
                self.streaming_response = streaming_response
                self.role = "assistant"
                self._content = None
            
            @property
            def content(self):
                """Lazy content access - collects stream on first access."""
                if self._content is None:
                    # For streaming, we need to collect the stream
                    # This will be done synchronously if accessed from sync context
                    # or asynchronously if accessed from async context
                    import asyncio
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            # Can't run async in sync context - return placeholder
                            self._content = "[Streaming response - use async iteration]"
                        else:
                            # Can run async - collect the stream
                            self._content = loop.run_until_complete(
                                self.streaming_response.collect()
                            )
                    except RuntimeError:
                        # No event loop - return placeholder
                        self._content = "[Streaming response - use async iteration]"
                return self._content
        
        class StreamingMockUsage(dict):
            """Mock usage object for streaming responses.
            
            Dynamically accesses usage from ModelActionResult to get updated metrics
            after stream completion (when update_usage() is called).
            """
            def __init__(self, result: Any, initial_usage_dict: Optional[dict] = None):
                super().__init__()
                self.result = result  # Store reference to ModelActionResult for dynamic access
                self._initial_usage = initial_usage_dict or {}
                self._update_usage()
            
            def _update_usage(self):
                """Update usage from result.metrics (may be updated after stream completion)."""
                # Get latest metrics from result (may have been updated via update_usage())
                if self.result and hasattr(self.result, 'metrics'):
                    usage_dict = self.result.metrics
                else:
                    usage_dict = self._initial_usage
                
                # Extract usage values
                prompt_tokens = usage_dict.get("prompt_tokens", 0) or 0
                completion_tokens = usage_dict.get("completion_tokens", 0) or 0
                total_tokens = usage_dict.get("total_tokens", 0) or 0
                
                # Set as dict items (for dict() conversion)
                # Properties defined below will handle attribute access
                self['prompt_tokens'] = prompt_tokens
                self['completion_tokens'] = completion_tokens
                self['total_tokens'] = total_tokens
            
            def __getitem__(self, key):
                """Dynamically get usage values, refreshing from result if needed."""
                self._update_usage()  # Refresh before returning
                return super().__getitem__(key)
            
            def _get_prompt_tokens(self):
                """Get prompt tokens, refreshing from result."""
                self._update_usage()
                return self.get('prompt_tokens', 0)
            
            def _set_prompt_tokens(self, value):
                self['prompt_tokens'] = value
            
            prompt_tokens = property(_get_prompt_tokens, _set_prompt_tokens)
            
            def _get_completion_tokens(self):
                """Get completion tokens, refreshing from result."""
                self._update_usage()
                return self.get('completion_tokens', 0)
            
            def _set_completion_tokens(self, value):
                self['completion_tokens'] = value
            
            completion_tokens = property(_get_completion_tokens, _set_completion_tokens)
            
            def _get_total_tokens(self):
                """Get total tokens, refreshing from result."""
                self._update_usage()
                return self.get('total_tokens', 0)
            
            def _set_total_tokens(self, value):
                self['total_tokens'] = value
            
            total_tokens = property(_get_total_tokens, _set_total_tokens)
        
        class StreamingMockResponse:
            """Streaming response wrapper compatible with DSPy and OpenAI format.
            
            Provides both:
            - Standard OpenAI-compatible interface (.choices[0].message.content)
            - Async generator interface for streaming chunks
            """
            def __init__(self, result: ModelActionResult, model: str, usage_dict: Optional[dict] = None):
                self.result = result
                self.model = model
                # Pass result object so StreamingMockUsage can access updated metrics after streaming
                self.usage = StreamingMockUsage(result, usage_dict)
                self.id = "mock-streaming-response-id"
                self.object = "chat.completion.chunk"
                self.created = 0
                self._collected_content = None
                self._choices = None
            
            @property
            def choices(self):
                """Lazy choices access."""
                if self._choices is None:
                    self._choices = [StreamingMockChoice(self)]
                return self._choices
            
            async def collect(self) -> str:
                """Collect the full streaming response asynchronously.
                
                Returns:
                    Complete response text after consuming the stream
                """
                if self._collected_content is None:
                    chunks = []
                    async for chunk in self.result.iter_stream():
                        chunks.append(chunk)
                    self._collected_content = "".join(chunks)
                return self._collected_content
            
            async def stream(self):
                """Async generator for streaming chunks.
                
                Yields:
                    Response text chunks as they arrive
                """
                async for chunk in self.result.iter_stream():
                    yield chunk
            
            def __aiter__(self):
                """Make the response itself iterable for DSPy's streamify."""
                return self.stream()
        
        return StreamingMockResponse(result, model, usage_dict)
    
    def copy(self, **kwargs):
        """Create a copy of this adapter with updated parameters.
        
        Args:
            **kwargs: Parameters to update in the copy
            
        Returns:
            New DSPyLM instance with updated parameters
        """
        # Create new instance with same model_action but updated kwargs
        new_kwargs = {**self.kwargs, **kwargs}
        model = new_kwargs.pop("model", self.model)  # Preserve current model setting
        temperature = new_kwargs.pop("temperature", self.kwargs.get("temperature"))
        max_tokens = new_kwargs.pop("max_tokens", self.kwargs.get("max_tokens"))
        cache = new_kwargs.pop("cache", self.cache)
        
        return DSPyLM(
            model_action=self.model_action,
            model_type=self.model_type,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            cache=cache,
            **new_kwargs
        )

