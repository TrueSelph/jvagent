"""Tests for OpenAI embedding batch behavior."""

import json
from typing import Any, Dict

import httpx
import pytest

from jvagent.action.model.embedding.openai.openai import OpenAIEmbeddingModelAction


class _MockResponse:
    def __init__(self, payload: Dict[str, Any], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)
        self.request = httpx.Request("POST", "https://api.openai.com/v1/embeddings")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "request failed",
                request=self.request,
                response=httpx.Response(
                    self.status_code, request=self.request, json=self._payload
                ),
            )

    def json(self) -> Dict[str, Any]:
        return self._payload


class _MockHttpClient:
    def __init__(self, post_response: Any):
        self._post_response = post_response

    async def post(self, *args, **kwargs):
        return self._post_response


@pytest.mark.asyncio
async def test_openai_embedding_batch_uses_single_request_response():
    action = OpenAIEmbeddingModelAction()
    action._http_client = _MockHttpClient(
        _MockResponse(
            {
                "data": [
                    {"embedding": [0.1, 0.2]},
                    {"embedding": [0.3, 0.4]},
                ],
                "usage": {"total_tokens": 42},
            }
        )
    )

    vectors = await action.embed_batch(["one", "two"])
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
