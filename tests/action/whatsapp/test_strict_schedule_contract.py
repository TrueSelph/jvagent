"""Cross-repo contract: strict scheduling must surface real dispatch failures.

The webhook's failure handling — 503 to the origin plus releasing the wamid
claim — only ever runs if ``create_task(..., strict=True)`` raises. jvspatial's
AWS schedulers historically raised for exactly one case (the noop scheduler
resolving in serverless mode) and swallowed the real ones: a failed Lambda
``invoke``, an unset ``AWS_LAMBDA_FUNCTION_NAME``. Real dispatch failures
therefore returned 200 upstream with the wamid claimed, and Meta's retry was
dedup-blocked — silent message loss the smoke test could not see, because it
simulated a raising scheduler, the one shape AWS schedulers never produced.

These tests pin the contract against the INSTALLED jvspatial. If a deployment
pairs this jvagent with a jvspatial that predates strict dispatch semantics,
they skip loudly rather than pass vacuously — the skip message is the deploy
warning.
"""

import inspect

import pytest
from jvspatial.serverless.tasks.aws_lambda import AwsLambdaDeferredTaskScheduler

_HAS_STRICT = (
    "strict" in inspect.signature(AwsLambdaDeferredTaskScheduler.schedule).parameters
)

pytestmark = pytest.mark.skipif(
    not _HAS_STRICT,
    reason=(
        "installed jvspatial predates strict dispatch semantics "
        "(TaskScheduler.schedule has no `strict` parameter): real Lambda "
        "dispatch failures will be SWALLOWED and deferred WhatsApp messages "
        "silently lost — upgrade jvspatial before deploying serverless "
        "WhatsApp on this pairing"
    ),
)


class _FailingClient:
    def invoke(self, **kwargs):
        raise ConnectionError("lambda unreachable")


def test_strict_schedule_raises_on_invoke_failure():
    sched = AwsLambdaDeferredTaskScheduler(
        function_name="fn", lambda_client=_FailingClient()
    )
    with pytest.raises(ConnectionError):
        sched.schedule("jvagent.whatsapp.interact", {"agent_id": "a"}, strict=True)


def test_strict_schedule_raises_when_function_name_unset(monkeypatch):
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    sched = AwsLambdaDeferredTaskScheduler(function_name="")
    with pytest.raises(RuntimeError, match="AWS_LAMBDA_FUNCTION_NAME"):
        sched.schedule("jvagent.whatsapp.interact", {"agent_id": "a"}, strict=True)


def test_dispatch_deferred_task_threads_strict_through():
    """The factory wrapper the webhook path actually goes through."""
    from jvspatial.serverless.factory import dispatch_deferred_task

    sched = AwsLambdaDeferredTaskScheduler(
        function_name="fn", lambda_client=_FailingClient()
    )
    with pytest.raises(ConnectionError):
        dispatch_deferred_task(
            "jvagent.whatsapp.interact",
            {"agent_id": "a"},
            override=sched,
            strict=True,
        )
