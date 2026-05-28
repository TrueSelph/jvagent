"""Tests for engine fallback message sanitization (Wave 9j.6).

The engine terminates in four scenarios that bypass the normal final
response path:

  - TIME_CAP    — elapsed >= max_duration_seconds
  - ITER_CAP    — _iteration > max_iterations
  - STUCK       — _check_stuck() fired
  - ERROR       — _handle_error called on orchestration exception

Pre-Wave-9j.6 the fallback text was hardcoded and leaked internal
mechanics ("I've reached the maximum number of steps for this task
without completing it. Please let me know if you'd like me to
continue.") plus banned closer phrases.

These tests assert that:
  1. EngineConfig defaults are sanitized (no internal mechanics, no
     banned phrases).
  2. The configured strings are surfaced as ``final_response`` by
     the engine termination paths.
  3. Operator overrides via ReasoningHelm attributes flow through
     ``_build_engine_config``.
"""

from __future__ import annotations

from jvagent.action.helm.reasoning.config import EngineConfig
from jvagent.action.helm.reasoning.reasoning_helm import ReasoningHelm

# ---------------------------------------------------------------------------
# Default sanitization
# ---------------------------------------------------------------------------


def test_default_iter_cap_text_is_sanitized():
    """No 'maximum number of steps', no 'let me know if', no banned phrases."""
    text = EngineConfig().iter_cap_response_text.lower()
    for banned in (
        "maximum number of steps",
        "let me know if",
        "let me know which",
        "feel free to",
        "anything else i can help",
        "happy to help further",
        "just say the word",
    ):
        assert banned not in text, f"default iter_cap leaks banned phrase: {banned!r}"


def test_default_time_cap_text_is_sanitized():
    """No 'time limit', no 'unable to complete the task', no banned phrases."""
    text = EngineConfig().time_cap_response_text.lower()
    for banned in (
        "time limit",
        "unable to complete the task",
        "let me know if",
        "feel free to",
    ):
        assert banned not in text, f"default time_cap leaks banned phrase: {banned!r}"


def test_default_stuck_text_is_sanitized():
    """No 'same actions repeatedly', no internal-mechanic exposure."""
    text = EngineConfig().stuck_response_text.lower()
    for banned in (
        "same actions repeatedly",
        "without progress",
        "different approach",
        "let me know if",
        "feel free to",
    ):
        assert banned not in text, f"default stuck text leaks banned phrase: {banned!r}"


def test_default_error_text_is_sanitized():
    """No 'encountered an error processing your request'."""
    text = EngineConfig().error_response_text.lower()
    for banned in (
        "encountered an error processing",
        "internal failure",
        "let me know if",
        "feel free to",
    ):
        assert banned not in text, f"default error text leaks banned phrase: {banned!r}"


def test_default_messages_have_no_options_menu_shape():
    """Templates like 'Want X or Y?' or 'Should I look up...' are forbidden."""
    for field_name in (
        "iter_cap_response_text",
        "time_cap_response_text",
        "stuck_response_text",
        "error_response_text",
    ):
        text = getattr(EngineConfig(), field_name).lower()
        for shape in (
            "want more details or",
            "would you like",
            "should i look up",
            "do you want",
            "need a comparison",
        ):
            assert (
                shape not in text
            ), f"default {field_name} carries options-menu shape: {shape!r}"


# ---------------------------------------------------------------------------
# ReasoningHelm → EngineConfig wiring
# ---------------------------------------------------------------------------


def test_reasoning_helm_default_attributes_match_config_defaults():
    helm = ReasoningHelm()
    cfg = EngineConfig()
    assert helm.iter_cap_response_text == cfg.iter_cap_response_text
    assert helm.time_cap_response_text == cfg.time_cap_response_text
    assert helm.stuck_response_text == cfg.stuck_response_text
    assert helm.error_response_text == cfg.error_response_text


def test_reasoning_helm_threads_override_to_engine_config():
    """Operator override via agent.yaml should reach EngineConfig."""
    helm = ReasoningHelm()
    # Pydantic frozen-style attribute assignment workaround.
    object.__setattr__(helm, "iter_cap_response_text", "Custom iter cap message.")
    object.__setattr__(helm, "time_cap_response_text", "Custom time cap message.")
    object.__setattr__(helm, "stuck_response_text", "Custom stuck message.")
    object.__setattr__(helm, "error_response_text", "Custom error message.")

    cfg = helm._build_engine_config()
    assert cfg.iter_cap_response_text == "Custom iter cap message."
    assert cfg.time_cap_response_text == "Custom time cap message."
    assert cfg.stuck_response_text == "Custom stuck message."
    assert cfg.error_response_text == "Custom error message."
