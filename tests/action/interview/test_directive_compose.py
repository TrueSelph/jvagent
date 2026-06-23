"""Unit tests for interview directive merge helpers."""

from __future__ import annotations

from jvagent.action.interview.directive_compose import (
    batch_failure_directive,
    batch_failure_status,
    compose_directives,
)


def test_batch_failure_status_partial_success():
    failures = [{"error_code": "VALIDATION_FAILED", "field": "email"}]
    assert batch_failure_status(failures, stored_any=True) == "partial_success"


def test_batch_failure_status_validation_failed():
    failures = [
        {"error_code": "VALIDATION_FAILED", "field": "email"},
        {"error_code": "VALIDATION_FAILED", "field": "phone"},
    ]
    assert batch_failure_status(failures, stored_any=False) == "validation_failed"


def test_batch_failure_directive_humanizes_fields():
    directive = batch_failure_directive(
        [
            {"error_code": "VALIDATION_FAILED", "field": "user_name"},
            {"error_code": "VALIDATION_FAILED", "field": "email_address"},
        ]
    )
    assert "user name" in directive.lower()
    assert "email address" in directive.lower()


def test_compose_directives_merges_user_parts_and_chains_calls():
    queue = [
        {
            "directive": "Tell the user: Thanks.",
            "stage": "post",
            "source": "hook",
            "field": None,
        },
        {
            "directive": "Call interview__next_field",
            "stage": "post",
            "source": "hook",
            "field": None,
        },
    ]
    merged = compose_directives(queue, fallback="fallback")
    assert merged.startswith("Tell the user: Thanks.")
    assert "interview__next_field" in merged
