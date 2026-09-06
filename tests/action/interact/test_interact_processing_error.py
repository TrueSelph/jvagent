"""Server faults in the interact path are 5xx, not 422 (audit F3).

An unexpected exception while processing a turn used to surface as a
``ValidationError`` (422) with a generic message — telling the client its
request was wrong when the server had failed. It is now a typed 500 carrying
the ``request_id`` to quote; genuine bad requests (``ValueError``) and typed
API errors keep their own statuses.
"""

from __future__ import annotations

import ast
import pathlib

from jvspatial.api.exceptions import JVSpatialAPIException, ValidationError

from jvagent.action.interact.endpoints import (
    _STREAM_CLIENT_ERROR,
    InteractProcessingError,
)

ENDPOINTS = pathlib.Path("jvagent/action/interact/endpoints.py")


def test_processing_error_is_a_typed_500_with_a_client_safe_message():
    err = InteractProcessingError(details={"request_id": "req-1"})
    assert isinstance(err, JVSpatialAPIException)
    assert not isinstance(err, ValidationError)
    assert int(err.status_code) == 500
    assert err.error_code == "interact_processing_error"
    assert err.message == _STREAM_CLIENT_ERROR  # no exception text leaks
    assert err.details == {"request_id": "req-1"}


def _handlers_of(func_name: str):
    tree = ast.parse(ENDPOINTS.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == func_name:
            return [
                h for n in ast.walk(node) if isinstance(n, ast.Try) for h in n.handlers
            ]
    raise AssertionError(f"{func_name} not found")


def _raised_names(handler: ast.ExceptHandler):
    names = []
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise) and node.exc is not None:
            call = node.exc
            target = call.func if isinstance(call, ast.Call) else call
            if isinstance(target, ast.Name):
                names.append(target.id)
    return names


def test_generic_exceptions_in_the_interact_endpoint_raise_the_typed_500():
    """The catch-all handler must raise InteractProcessingError; ValueError (a
    bad request) stays a ValidationError; typed API errors pass through."""
    handlers = _handlers_of("interact_endpoint")
    by_type = {}
    for h in handlers:
        key = h.type.id if isinstance(h.type, ast.Name) else None
        by_type.setdefault(key, []).append(h)
    generic = by_type.get("Exception") or []
    assert generic, "no catch-all handler in interact_endpoint"
    assert any("InteractProcessingError" in _raised_names(h) for h in generic)
    assert not any("ValidationError" in _raised_names(h) for h in generic)
    assert any(
        "ValidationError" in _raised_names(h) for h in by_type.get("ValueError", [])
    )
    assert "JVSpatialAPIException" in by_type  # typed errors re-raised as-is
