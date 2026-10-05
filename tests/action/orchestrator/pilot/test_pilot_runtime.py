"""The pilot uses the model Action configured on the existing JV agent."""

import asyncio

import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelRequest as PAIModelRequest
from pydantic_ai.messages import RetryPromptPart
from pydantic_ai.usage import UsageLimits

from jvagent.action.model.contract import ModelResponse, ToolCall, Usage
from jvagent.action.orchestrator.pilot.contracts import ResearchBrief
from jvagent.action.orchestrator.pilot.runtime import (
    _to_jv_messages,
    function_model_for_action,
)


def test_pydantic_runtime_adapts_to_the_configured_jv_model_action():
    expected = {
        "question": "the question",
        "findings": ["Supported finding"],
        "source_ids": ["source-1"],
        "limitations": [],
        "brief": "Supported finding (source-1).",
    }

    class ConfiguredModelAction:
        model = "operator-configured-model"

        def __init__(self):
            self.request = None

        async def complete(self, request, *, calling_action_name=None):
            self.request = request
            output = [
                item["function"]["name"]
                for item in request.tools
                if item["function"]["name"].startswith("final_result")
            ]
            assert output
            return ModelResponse(
                tool_calls=[ToolCall(id="call-1", name=output[0], arguments=expected)],
                finish_reason="tool_calls",
                usage=Usage(
                    prompt_tokens=20,
                    completion_tokens=10,
                    total_tokens=30,
                ),
                model=self.model,
            )

    model_action = ConfiguredModelAction()
    agent = Agent(
        function_model_for_action(model_action),
        instructions="Follow the configured JV skill SOP.",
        output_type=ResearchBrief,
        retries=0,
    )
    result = asyncio.run(agent.run("the question"))

    assert result.output == ResearchBrief.model_validate(expected)
    assert model_action.request.model == "operator-configured-model"
    assert model_action.request.messages[0] == {
        "role": "system",
        "content": "Follow the configured JV skill SOP.",
    }
    assert model_action.request.messages[-1] == {
        "role": "user",
        "content": "the question",
    }
    assert result.usage.input_tokens == 20
    assert result.usage.output_tokens == 10
    assert result.usage.total_tokens == 30


def test_pydantic_validation_retry_details_are_forwarded_as_text():
    retry = RetryPromptPart(
        content=[
            {
                "type": "missing",
                "loc": ("findings",),
                "msg": "Field required",
                "input": {},
            }
        ],
        tool_name="final_result",
        tool_call_id="call-invalid-output",
    )

    converted = _to_jv_messages([PAIModelRequest(parts=[retry])], instructions=None)

    assert converted == [
        {
            "role": "tool",
            "tool_call_id": "call-invalid-output",
            "name": "final_result",
            "content": retry.model_response(),
        }
    ]
    assert "findings" in converted[0]["content"]


def test_pilot_total_token_limit_uses_provider_reported_usage():
    expected = {
        "question": "the question",
        "findings": ["Supported finding"],
        "source_ids": ["source-1"],
        "limitations": [],
        "brief": "Supported finding (source-1).",
    }

    class OverBudgetModelAction:
        model = "operator-configured-model"

        def __init__(self):
            self.calls = 0

        async def complete(self, request, *, calling_action_name=None):
            self.calls += 1
            output_name = next(
                item["function"]["name"]
                for item in request.tools
                if item["function"]["name"].startswith("final_result")
            )
            return ModelResponse(
                tool_calls=[
                    ToolCall(id="call-1", name=output_name, arguments=expected)
                ],
                usage=Usage(prompt_tokens=20, completion_tokens=10, total_tokens=30),
                model=self.model,
            )

    model_action = OverBudgetModelAction()
    agent = Agent(
        function_model_for_action(model_action),
        output_type=ResearchBrief,
        retries=0,
    )
    with pytest.raises(UsageLimitExceeded, match="total_tokens_limit"):
        asyncio.run(
            agent.run(
                "the question",
                usage_limits=UsageLimits(total_tokens_limit=29, request_limit=1),
            )
        )
    assert model_action.calls == 1
