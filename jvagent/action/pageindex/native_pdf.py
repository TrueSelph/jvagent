"""Thread boundary for PageIndex's synchronous PDF document algorithm."""

import asyncio
from typing import Any

from .llm_bridge import PageIndexCancelled, check_pageindex_cancelled
from .native_search import Complete


class NativePDFBridge:
    """Keep SDK execution and graph writes on the admitted operation's loop."""

    def __init__(self, complete: Complete) -> None:
        self.complete = complete
        self.loop = asyncio.get_running_loop()
        self.active: asyncio.Task[Any] | None = None
        self.closed = False
        self.failure: Exception | None = None

    async def _invoke(
        self, prompt: str, history: list[dict[str, Any]], partial: bool
    ) -> tuple[str, str, dict[str, int]]:
        from pydantic_ai.messages import (
            ModelRequest,
            ModelResponse,
            TextPart,
            UserPromptPart,
        )

        if self.closed:
            raise PageIndexCancelled()
        if (
            len(prompt.encode("utf-8"))
            + sum(len(item["content"].encode("utf-8")) for item in history)
            > 100_000
        ):
            raise ValueError("PageIndex PDF completion exceeds the native size limit")
        messages: list[Any] = []
        for item in history:
            if item["role"] == "user":
                messages.append(ModelRequest(parts=[UserPromptPart(item["content"])]))
            elif item["role"] == "assistant":
                messages.append(ModelResponse(parts=[TextPart(item["content"])]))
            else:
                raise ValueError("Unsupported PageIndex continuation role")
        self.active = asyncio.current_task()
        try:
            result = await self.complete(
                prompt, message_history=messages, allow_partial=partial
            )
        finally:
            self.active = None
        reason = result.response.finish_reason
        if reason not in (None, "stop", "length"):
            raise RuntimeError("PageIndex PDF completion did not finish")
        usage = result.response.usage
        finish = "max_output_reached" if reason == "length" else "finished"
        return (
            result.output,
            finish,
            {
                "prompt_tokens": usage.input_tokens,
                "completion_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
            },
        )

    def call(
        self,
        prompt: str,
        history: list[dict[str, Any]] | None = None,
        *,
        partial: bool = False,
    ) -> tuple[str, str, dict[str, int]]:
        """Serialize the worker's requests, including calls from its own async loop."""
        check_pageindex_cancelled()
        if self.failure is not None:
            raise PageIndexCancelled() from self.failure
        if self.closed:
            raise PageIndexCancelled()
        future = asyncio.run_coroutine_threadsafe(
            self._invoke(prompt, history or [], partial), self.loop
        )
        try:
            return future.result()
        except Exception as exc:
            # Some parser branches catch completion failures and continue. Refuse
            # later requests and publication even if the parser catches this.
            self.failure = exc
            raise PageIndexCancelled() from exc

    async def stop(self) -> None:
        """Drain an in-flight SDK request before the owner closes its receipts."""
        self.closed = True
        task = self.active
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def extract_native_pdf_pages(doc: Any) -> list[tuple[str, int]]:
    """Use the classic parser's PDF reader with a local byte bound."""
    from PyPDF2 import PdfReader

    reader = PdfReader(doc)
    if not 0 < len(reader.pages) <= 256:
        raise ValueError("Native PageIndex PDF ingestion requires 1 to 256 pages")
    pages = []
    total = 0
    for page in reader.pages:
        check_pageindex_cancelled()
        text = page.extract_text() or ""
        size = len(text.encode("utf-8"))
        total += size
        if total > 2 * 1024 * 1024:
            raise ValueError(
                "PageIndex PDF extracted text exceeds the native size limit"
            )
        pages.append((text, size))
    return pages
