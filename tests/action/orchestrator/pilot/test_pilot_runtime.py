"""The pilot uses the model Action configured on the existing JV agent."""

import asyncio
import json

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelRequest as PAIModelRequest
from pydantic_ai.messages import RetryPromptPart
from pydantic_ai.usage import UsageLimits

from jvagent.action.model.contract import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ToolCall,
    Usage,
)
from jvagent.action.model.language.base import ModelActionResult
from jvagent.action.orchestrator.pilot.contracts import (
    ConversationalReply,
    PilotCaller,
    PilotOutput,
    PilotRunContext,
    ResearchBrief,
)
from jvagent.action.orchestrator.pilot.runtime import (
    PilotBudgetExceeded,
    PilotEvidenceCollector,
    PilotModelAdapterError,
    _to_jv_messages,
    build_research_agent,
    function_model_for_action,
    request_input_token_upper_bound,
    run_research_agent,
)
from jvagent.action.orchestrator.skills import SkillDoc


def test_model_request_guard_stops_before_calling_the_provider():
    class ConfiguredModelAction:
        calls = 0

        async def complete(self, request, *, calling_action_name=None):
            self.calls += 1
            raise AssertionError("provider must not be called after budget exhaustion")

    model_action = ConfiguredModelAction()
    guarded_requests = []

    def exhausted(request):
        guarded_requests.append(request)
        raise PilotBudgetExceeded("configured dollar budget exhausted")

    agent = Agent(
        function_model_for_action(model_action, request_guard=exhausted),
        output_type=ConversationalReply,
        retries=0,
    )
    with pytest.raises(PilotBudgetExceeded, match="dollar budget"):
        asyncio.run(agent.run("hello"))
    assert model_action.calls == 0
    assert len(guarded_requests) == 1
    assert guarded_requests[0].messages[-1]["content"] == "hello"


def test_request_input_upper_bound_includes_all_active_tool_schemas():
    base = ModelRequest(messages=[{"role": "user", "content": "hello"}])
    with_tools = ModelRequest(
        messages=[{"role": "user", "content": "hello"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "web_search__search",
                    "parameters": {"properties": {"query": {"type": "string"}}},
                },
            }
        ],
    )

    assert request_input_token_upper_bound(
        with_tools
    ) > request_input_token_upper_bound(base)


def test_pydantic_runtime_adapts_to_the_configured_jv_model_action():
    expected = {
        "question": "the question",
        "findings": [{"claim": "Supported finding", "source_ids": ["source-1"]}],
        "limitations": [],
    }

    class ConfiguredModelAction:
        pydantic_ai_supported_settings = frozenset(
            {"seed", "top_k", "presence_penalty", "stop_sequences"}
        )
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
        model_settings={
            "temperature": 0.2,
            "max_tokens": 2048,
            "top_p": 0.9,
            "seed": 17,
            "top_k": 4,
            "presence_penalty": 0.2,
            "stop_sequences": ["done"],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "response_format": {"type": "json_object"},
            "reasoning_effort": "high",
            "reasoning": {"enabled": True, "budget_tokens": 1024},
        },
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
    assert model_action.request.temperature == 0.2
    assert model_action.request.max_tokens == 2048
    assert model_action.request.top_p == 0.9
    assert model_action.request.tool_choice == "auto"
    assert model_action.request.parallel_tool_calls is False
    assert model_action.request.response_format == {"type": "json_object"}
    assert model_action.request.reasoning_effort == "high"
    assert model_action.request.reasoning == {"enabled": True, "budget_tokens": 1024}
    assert model_action.request.extra == {
        "seed": 17,
        "top_k": 4,
        "presence_penalty": 0.2,
        "stop": ["done"],
    }
    assert result.usage.input_tokens == 20
    assert result.usage.output_tokens == 10
    assert result.usage.total_tokens == 30


def test_undeclared_provider_setting_fails_before_model_action_call():
    class ConfiguredModelAction:
        calls = 0

        async def complete(self, request, *, calling_action_name=None):
            self.calls += 1
            return ModelResponse(text="should not be called")

    model_action = ConfiguredModelAction()
    agent = Agent(
        function_model_for_action(model_action),
        output_type=ConversationalReply,
        model_settings={"frequency_penalty": 0.4},
        retries=0,
    )

    with pytest.raises(PilotModelAdapterError, match="does not declare support"):
        asyncio.run(agent.run("hello"))
    assert model_action.calls == 0


def test_model_usage_observer_receives_usage_before_agent_continues():
    observed = []

    class ConfiguredModelAction:
        async def complete(self, request, *, calling_action_name=None):
            return ModelResponse(
                text="A concise response.",
                usage=Usage(prompt_tokens=41, completion_tokens=9, total_tokens=50),
            )

    async def observe(response):
        observed.append(
            (response.usage.prompt_tokens, response.usage.completion_tokens)
        )

    asyncio.run(
        Agent(
            function_model_for_action(ConfiguredModelAction(), usage_observer=observe),
            output_type=str,
            retries=0,
        ).run("hello")
    )

    assert observed == [(41, 9)]


def test_host_reasoning_settings_are_mapped_to_the_jv_request():
    class ConfiguredModelAction:
        request = None

        async def complete(self, request, *, calling_action_name=None):
            self.request = request
            return ModelResponse(text="Acknowledged.")

    model_action = ConfiguredModelAction()
    model = function_model_for_action(
        model_action,
        request_overrides={
            "reasoning_effort": "high",
            "reasoning": {"enabled": True, "budget_tokens": 1024},
        },
    )
    asyncio.run(Agent(model, output_type=str, retries=0).run("hello"))

    assert model_action.request.reasoning_effort == "high"
    assert model_action.request.reasoning == {
        "enabled": True,
        "budget_tokens": 1024,
    }


def test_pydantic_model_adapter_forwards_provider_reasoning_trace():
    traces = []

    class ReasoningModelAction:
        async def complete(self, request, *, calling_action_name=None):
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name=next(
                            item["function"]["name"]
                            for item in request.tools
                            if item["function"]["name"].startswith("final_result")
                        ),
                        arguments={
                            "question": "Reply briefly",
                            "findings": [
                                {"claim": "Done.", "source_ids": ["source-1"]}
                            ],
                            "limitations": [],
                        },
                    )
                ],
                thinking="Checking the requested evidence.",
            )

    async def observe(text):
        traces.append(text)

    agent = Agent(
        function_model_for_action(ReasoningModelAction(), reasoning_observer=observe),
        output_type=PilotOutput,
        retries=0,
    )
    asyncio.run(agent.run("Reply briefly"))
    assert traces == ["Checking the requested evidence."]


def test_streaming_model_action_forwards_reasoning_and_validated_tool_output():
    traces = []
    expected = {
        "question": "Reply briefly",
        "findings": [{"claim": "Done.", "source_ids": ["source-1"]}],
        "limitations": [],
    }

    class StreamingModelAction:
        model = "streaming-test-model"

        async def complete(self, request, *, calling_action_name=None):
            raise AssertionError("stream-capable models should use query_messages")

        async def query_messages(
            self, *, messages, stream=False, calling_action_name=None, **kwargs
        ):
            assert stream is True
            tool_name = next(
                item["function"]["name"]
                for item in kwargs["tools"]
                if item["function"]["name"].startswith("final_result")
            )

            async def text_stream():
                yield "{"  # Draft output remains inside Pydantic AI.
                yield '"question":'

            result = ModelActionResult(
                stream=text_stream(),
                usage={"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
                model=self.model,
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": "output-1",
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(expected),
                        },
                    }
                ],
                thinking_queue=asyncio.Queue(),
            )
            result.push_thinking_delta("Checking the evidence.")
            result.close_thinking_stream()
            return result

    async def observe(text):
        traces.append(text)

    context = PilotRunContext(
        caller=PilotCaller(agent_id="a", user_id="u", session_id="s"),
        task_id="task-1",
        run_id="run-1",
        skill_id="research",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
    )
    agent = Agent(
        function_model_for_action(StreamingModelAction(), reasoning_observer=observe),
        output_type=ResearchBrief,
        retries=0,
    )
    output = asyncio.run(
        run_research_agent(
            agent,
            "Reply briefly",
            run_context=context,
        )
    )

    assert output == ResearchBrief.model_validate(expected)
    assert traces == ["Checking the evidence."]


def test_cancelling_streamed_pilot_closes_provider_stream_and_reasoning_pump():
    stream_started = asyncio.Event()
    stream_closed = asyncio.Event()

    class StreamingModelAction:
        async def complete(self, request, *, calling_action_name=None):
            raise AssertionError("stream-capable models should use query_messages")

        async def query_messages(self, *, stream=False, **_kwargs):
            assert stream is True

            async def text_stream():
                stream_started.set()
                try:
                    await asyncio.Event().wait()
                    yield "unreachable"
                finally:
                    stream_closed.set()

            return ModelActionResult(
                stream=text_stream(), thinking_queue=asyncio.Queue()
            )

    context = PilotRunContext(
        caller=PilotCaller(agent_id="a", user_id="u", session_id="s"),
        task_id="task-cancel-stream",
        run_id="run-cancel-stream",
        skill_id="research",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
    )
    agent = Agent(
        function_model_for_action(StreamingModelAction()),
        output_type=ResearchBrief,
        retries=0,
    )

    async def cancel_run():
        task = asyncio.create_task(
            run_research_agent(agent, "Wait for provider", run_context=context)
        )
        await asyncio.wait_for(stream_started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stream_closed.is_set()

    asyncio.run(cancel_run())


def test_research_pilot_does_not_offer_source_free_conversational_output():
    class ConfiguredModelAction:
        request = None

        async def complete(self, request, *, calling_action_name=None):
            self.request = request
            output = next(
                item["function"]
                for item in request.tools
                if item["function"]["name"].startswith("final_result")
            )
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name=output["name"],
                        arguments={
                            "question": "What is the answer?",
                            "findings": [
                                {
                                    "claim": "A source-backed answer.",
                                    "source_ids": ["s1"],
                                }
                            ],
                            "limitations": [],
                        },
                    )
                ],
                finish_reason="tool_calls",
                usage=Usage(prompt_tokens=20, completion_tokens=10, total_tokens=30),
            )

    from jvagent.action.orchestrator.pilot.runtime import build_research_agent

    skill = SkillDoc(
        name="research",
        description="Research with evidence",
        body="Ground claims in sources.",
        digest="skill-sha256",
        output_contract="evidence_required",
    )
    run_context = PilotRunContext(
        caller=PilotCaller(agent_id="a", user_id="u", session_id="s"),
        task_id="task-1",
        run_id="run-1",
        skill_id="research",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
    )
    model_action = ConfiguredModelAction()
    agent = asyncio.run(
        build_research_agent(
            model_action,
            [(skill, [])],
            instructions="Use evidence.",
            run_context=run_context,
            access_check=lambda *_args: True,
        )
    )
    result = asyncio.run(agent.run("Research the topic.", deps=run_context))

    assert isinstance(result.output, ResearchBrief)
    output_schema = next(
        item["function"]
        for item in model_action.request.tools
        if item["function"]["name"].startswith("final_result")
    )["parameters"]
    assert "answer" not in output_schema["properties"]
    assert "findings" in output_schema["properties"]


def test_empty_truncated_model_response_preserves_finish_reason_and_usage():
    class TruncatedModelAction:
        async def complete(self, request, *, calling_action_name=None):
            return ModelResponse(
                finish_reason=FinishReason.LENGTH,
                raw_finish_reason="length",
                usage=Usage(completion_tokens=1024, total_tokens=1024),
            )

    async def run() -> None:
        agent = Agent(
            function_model_for_action(TruncatedModelAction()),
            output_type=ResearchBrief,
            retries=0,
        )
        with pytest.raises(PilotModelAdapterError) as caught:
            await agent.run("answer briefly")
        assert caught.value.finish_reason == FinishReason.LENGTH
        assert "completion_tokens=1024" in str(caught.value)

    asyncio.run(run())


@pytest.mark.parametrize(
    "finish_reason",
    [FinishReason.LENGTH, FinishReason.CONTENT_FILTER],
)
def test_partial_text_with_non_success_finish_reason_is_rejected(finish_reason):
    class TruncatedModelAction:
        async def complete(self, request, *, calling_action_name=None):
            return ModelResponse(
                text="incomplete answer",
                finish_reason=finish_reason,
                usage=Usage(completion_tokens=1024, total_tokens=1024),
            )

    async def run() -> None:
        agent = Agent(
            function_model_for_action(TruncatedModelAction()),
            output_type=ConversationalReply,
            retries=0,
        )
        with pytest.raises(PilotModelAdapterError) as caught:
            await agent.run("answer briefly")
        assert caught.value.finish_reason == finish_reason
        assert "incomplete answer" not in str(caught.value)

    asyncio.run(run())


@pytest.mark.parametrize("raw_arguments", ["{oops", "[]", "null", '"value"'])
def test_malformed_or_non_object_tool_arguments_fail_closed(raw_arguments):
    class MalformedToolModelAction:
        async def complete(self, request, *, calling_action_name=None):
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        id="call-malformed",
                        name="final_result",
                        arguments={},
                        raw_arguments=raw_arguments,
                    )
                ],
                finish_reason="tool_calls",
            )

    async def run() -> None:
        agent = Agent(
            function_model_for_action(MalformedToolModelAction()),
            output_type=ConversationalReply,
            retries=0,
        )
        with pytest.raises(PilotModelAdapterError, match="tool arguments"):
            await agent.run("answer briefly")

    asyncio.run(run())


def test_empty_object_tool_arguments_remain_valid():
    class EmptyResult(BaseModel):
        pass

    class EmptyArgumentsModelAction:
        async def complete(self, request, *, calling_action_name=None):
            output_name = next(
                item["function"]["name"]
                for item in request.tools
                if item["function"]["name"].startswith("final_result")
            )
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        id="call-empty",
                        name=output_name,
                        arguments={},
                        raw_arguments="{}",
                    )
                ],
                finish_reason="tool_calls",
            )

    async def run() -> None:
        agent = Agent(
            function_model_for_action(EmptyArgumentsModelAction()),
            output_type=EmptyResult,
            retries=0,
        )
        result = await agent.run("answer briefly")
        assert result.output == EmptyResult()

    asyncio.run(run())


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


def test_research_agent_uses_validated_jv_tool_timeout_and_concurrency(monkeypatch):
    import jvagent.action.orchestrator.pilot.runtime as runtime

    captured = {}

    class AgentSpy:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

    class ModelAction:
        async def complete(self, request, *, calling_action_name=None):
            raise AssertionError("agent construction must not call the provider")

    monkeypatch.setattr(runtime, "Agent", AgentSpy)
    skill = SkillDoc(
        name="research",
        description="Research",
        body="Use declared read tools and cite sources.",
        digest="skill-sha256",
    )
    context = PilotRunContext(
        caller=PilotCaller(agent_id="a", user_id="u", session_id="s"),
        task_id="task-1",
        run_id="run-1",
        skill_id="research",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
    )

    asyncio.run(
        build_research_agent(
            ModelAction(),
            [(skill, [])],
            instructions="Use evidence.",
            run_context=context,
            access_check=lambda *_args: True,
            tool_timeout_seconds=120,
            max_tool_concurrency=4,
        )
    )

    assert captured["tool_timeout"] == 120.0
    assert captured["max_concurrency"] == 4


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("nan"), True, "5"])
def test_research_agent_rejects_invalid_tool_timeout(timeout):
    class ModelAction:
        async def complete(self, request, *, calling_action_name=None):
            raise AssertionError("agent construction must not call the provider")

    skill = SkillDoc(
        name="research",
        description="Research",
        body="Use declared read tools and cite sources.",
        digest="skill-sha256",
    )
    context = PilotRunContext(
        caller=PilotCaller(agent_id="a", user_id="u", session_id="s"),
        task_id="task-1",
        run_id="run-1",
        skill_id="research",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
    )

    with pytest.raises(PilotModelAdapterError, match="tool timeout"):
        asyncio.run(
            build_research_agent(
                ModelAction(),
                [(skill, [])],
                instructions="Use evidence.",
                run_context=context,
                access_check=lambda *_args: True,
                tool_timeout_seconds=timeout,
            )
        )


@pytest.mark.parametrize("concurrency", [0, -1, 9, True, 1.5])
def test_research_agent_rejects_unbounded_or_invalid_tool_concurrency(concurrency):
    class ModelAction:
        async def complete(self, request, *, calling_action_name=None):
            raise AssertionError("agent construction must not call the provider")

    skill = SkillDoc(
        name="research",
        description="Research",
        body="Use declared read tools and cite sources.",
        digest="skill-sha256",
    )
    context = PilotRunContext(
        caller=PilotCaller(agent_id="a", user_id="u", session_id="s"),
        task_id="task-1",
        run_id="run-1",
        skill_id="research",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
    )

    with pytest.raises(PilotModelAdapterError, match="tool concurrency"):
        asyncio.run(
            build_research_agent(
                ModelAction(),
                [(skill, [])],
                instructions="Use evidence.",
                run_context=context,
                access_check=lambda *_args: True,
                max_tool_concurrency=concurrency,
            )
        )


def test_pilot_total_token_limit_uses_provider_reported_usage():
    expected = {
        "question": "the question",
        "findings": [{"claim": "Supported finding", "source_ids": ["source-1"]}],
        "limitations": [],
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


def test_invalid_source_references_are_repairable_output_validation_errors():
    context = PilotRunContext(
        caller=PilotCaller(agent_id="a", user_id="u", session_id="s"),
        task_id="task-1",
        run_id="run-1",
        skill_id="research",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
    )
    evidence = PilotEvidenceCollector()
    asyncio.run(
        evidence.observe(
            context,
            "web_search__search",
            {},
            json.dumps([{"title": "Observed", "link": "https://example.test/source"}]),
        )
    )
    from jvagent.tooling.tool_result import ToolResultText

    asyncio.run(
        evidence.observe(
            context,
            "web_fetch__fetch",
            {"url": "https://example.test/source"},
            ToolResultText(
                "# Source: https://example.test/source\n\nObserved claim.",
                {
                    "web_fetch_result": {
                        "outcome": "success",
                        "requested_url": "https://example.test/source",
                        "final_url": "https://example.test/source",
                        "content_type": "text/html",
                        "http_status": 200,
                    }
                },
            ),
        )
    )
    known_id = evidence.snapshot()[0].source_id
    calls = []

    class OutputModel:
        async def complete(self, request, *, calling_action_name=None):
            calls.append(request)
            output_name = next(
                item["function"]["name"]
                for item in request.tools
                if item["function"]["name"].startswith("final_result")
            )
            source_id = "forged-source" if len(calls) == 1 else known_id
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        id=f"output-{len(calls)}",
                        name=output_name,
                        arguments={
                            "question": "the question",
                            "findings": [
                                {
                                    "claim": "Observed claim",
                                    "source_ids": [source_id],
                                    "supporting_source_id": source_id,
                                    "supporting_quote": "Observed claim.",
                                }
                            ],
                            "limitations": [],
                        },
                    )
                ],
                usage=Usage(prompt_tokens=5, completion_tokens=5, total_tokens=10),
            )

    agent = Agent(
        function_model_for_action(OutputModel()),
        output_type=ResearchBrief,
        retries=1,
    )
    output = asyncio.run(
        run_research_agent(
            agent, "the question", run_context=context, evidence=evidence
        )
    )
    assert len(calls) == 2
    assert output.findings[0].source_ids == (known_id,)
