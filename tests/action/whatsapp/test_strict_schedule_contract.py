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


class _RejectingClient:
    """An async invoke that is ACCEPTED at the transport but rejected by Lambda.

    The failure mode with no exception attached: `invoke` returns normally with
    a non-2xx StatusCode or a FunctionError. jvspatial catches this; the
    webhook must still get to release its wamid.
    """

    def invoke(self, **kwargs):
        return {"StatusCode": 500, "FunctionError": "Unhandled"}


def test_strict_schedule_raises_on_invoke_failure():
    sched = AwsLambdaDeferredTaskScheduler(
        function_name="fn", lambda_client=_FailingClient()
    )
    # Assert the CONTRACT (strict surfaces the failure), not a specific
    # exception class: jvspatial wraps provider errors in its own typed
    # exceptions, and pinning the raw type here would make an upstream
    # improvement look like a jvagent regression.
    with pytest.raises(Exception) as exc_info:
        sched.schedule("jvagent.whatsapp.interact", {"agent_id": "a"}, strict=True)
    assert not isinstance(exc_info.value, AssertionError)


def test_strict_schedule_raises_when_function_name_unset(monkeypatch):
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    sched = AwsLambdaDeferredTaskScheduler(function_name="")
    with pytest.raises(Exception) as exc_info:
        sched.schedule("jvagent.whatsapp.interact", {"agent_id": "a"}, strict=True)
    assert not isinstance(exc_info.value, AssertionError)


def test_strict_schedule_raises_when_lambda_rejects_the_invoke():
    """A rejection is not an exception — an async invoke answers 202 on
    acceptance, so a non-2xx StatusCode/FunctionError is the quiet failure.
    Covered by jvspatial as of the strict-dispatch hardening."""
    sched = AwsLambdaDeferredTaskScheduler(
        function_name="fn", lambda_client=_RejectingClient()
    )
    with pytest.raises(Exception) as exc_info:
        sched.schedule("jvagent.whatsapp.interact", {"agent_id": "a"}, strict=True)
    assert not isinstance(exc_info.value, AssertionError)


def test_non_strict_still_fire_and_forget():
    """The other direction: every existing caller keeps its semantics."""
    sched = AwsLambdaDeferredTaskScheduler(
        function_name="fn", lambda_client=_FailingClient()
    )
    ref = sched.schedule("jvagent.whatsapp.interact", {"agent_id": "a"})
    assert isinstance(ref, str) and ref


def test_dispatch_deferred_task_threads_strict_through():
    """The factory wrapper the webhook path actually goes through."""
    from jvspatial.serverless.factory import dispatch_deferred_task

    sched = AwsLambdaDeferredTaskScheduler(
        function_name="fn", lambda_client=_FailingClient()
    )
    with pytest.raises(Exception) as exc_info:
        dispatch_deferred_task(
            "jvagent.whatsapp.interact",
            {"agent_id": "a"},
            override=sched,
            strict=True,
        )
    assert not isinstance(exc_info.value, AssertionError)
