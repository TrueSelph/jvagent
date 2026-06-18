"""The single ``ctx`` interface: ``say`` (user text) folds into response_directive,
``tool_response`` (control), ``valid``/``invalid`` (validators), with say gated to
reply-producing phases so a store re-run can't bleed onto the next turn."""

from __future__ import annotations

import pytest

from jvagent.action.interview.hooks import (
    ACTIVATION_PHASE,
    STORE_PHASE,
    VALIDATE_PHASE,
    HookExecutionContext,
    call_hook,
    coerce_hook_result,
)

_MARKER = "⁣"


@pytest.mark.asyncio
async def test_say_single_folds_into_response_directive():
    async def hook(ctx):
        ctx.say("What is your name?")
        return ctx.tool_response(ok=True, status="ok", extra="x")

    r = await call_hook(hook, phase=ACTIVATION_PHASE)
    assert isinstance(r, dict)
    assert r["ok"] is True and r["extra"] == "x"
    rd = r["response_directive"]
    assert rd.startswith("Tell the user: What is your name?")
    assert _MARKER in rd  # model-only guidance present after the marker


@pytest.mark.asyncio
async def test_say_list_is_sequential_followup():
    async def hook(ctx):
        ctx.say(["Here are the slots:\n- Mon 9am", "Which works?"])
        return ctx.tool_response(ok=True, status="ok")

    rd = (await call_hook(hook, phase=ACTIVATION_PHASE))["response_directive"]
    user_facing = rd.split(_MARKER, 1)[0]
    assert "Mon 9am" in user_facing and "Which works?" in user_facing


@pytest.mark.asyncio
async def test_say_hint_is_model_only_guidance():
    async def hook(ctx):
        ctx.say("Enter the code.", hint="Ask for otp_code only; do not skip.")
        return ctx.tool_response(ok=True, status="ok")

    rd = (await call_hook(hook, phase=ACTIVATION_PHASE))["response_directive"]
    user_facing, guidance = rd.split(_MARKER, 1)
    assert "Enter the code." in user_facing and "do not skip" not in user_facing
    assert "do not skip" in guidance


@pytest.mark.asyncio
async def test_say_inert_on_store_run_no_bleed():
    """The same prompt-builder re-runs while storing the answer; say must be inert
    there, or the previous prompt bleeds onto the next turn."""

    async def hook(ctx):
        ctx.say("Here are the slots: ...")
        return ctx.tool_response(ok=True, status="ok")

    r = coerce_hook_result(await call_hook(hook, phase=STORE_PHASE))
    assert "response_directive" not in r


@pytest.mark.asyncio
async def test_validator_valid_and_invalid():
    async def validator(ctx):
        if ctx.value == "bad":
            return ctx.invalid("Give a real value.", value=ctx.value)
        return ctx.valid(value=ctx.value.upper())

    bad = await call_hook(validator, value="bad", phase=VALIDATE_PHASE)
    assert bad["valid"] is False and bad["error"] == "Give a real value."
    good = await call_hook(validator, value="ab", phase=VALIDATE_PHASE)
    assert good["valid"] is True and good["value"] == "AB"


@pytest.mark.asyncio
async def test_say_continue_appends_next_prompt():
    async def hook(ctx):
        ctx.say("Thanks for the work email.", continue_=True)
        return ctx.tool_response(ok=True, status="ok")

    # No spec/session → continue falls back to the review chain, but the sidebar
    # statement is still delivered to the user.
    rd = (await call_hook(hook, phase="post"))["response_directive"]
    assert "Thanks for the work email." in rd


@pytest.mark.asyncio
async def test_control_response_directive_not_overwritten_by_empty_say():
    async def hook(ctx):
        # No say — a pure control directive must survive untouched.
        return ctx.tool_response(
            ok=True, status="ok", response_directive=ctx.call_tool("interview__review")
        )

    r = coerce_hook_result(await call_hook(hook, phase="post"))
    assert r["response_directive"] == "Call interview__review."


def test_ctx_always_constructible_and_reads_inputs():
    ctx = HookExecutionContext(
        session=None,
        spec=None,
        visitor="V",
        interview_action="A",
        value="raw",
        extracted_values={"a": 1},
        args={"k": "v"},
    )
    assert ctx.visitor == "V" and ctx.interview == "A" and ctx.value == "raw"
    assert ctx.extracted_values == {"a": 1} and ctx.args["k"] == "v"
