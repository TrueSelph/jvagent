import hashlib
import hmac
import json

from jvagent.action.orchestrator.host_context import (
    allows_empty_host_utterance,
    verified_host_system_context,
)


def _envelope(context="host policy", run_id="run-1", secret="test-secret"):
    body = json.dumps(
        {"version": 1, "run_id": run_id, "context": context},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return {"body": body, "signature": signature}


def test_host_system_context_requires_valid_signature_and_run_binding(monkeypatch):
    from jvagent.action.interact import session_token

    monkeypatch.setattr(session_token, "_secret", lambda: "test-secret")
    data = {"run_id": "run-1", "host_system_context": _envelope()}

    assert verified_host_system_context(data) == "host policy"
    assert allows_empty_host_utterance(data)

    data["run_id"] = "another-run"
    assert verified_host_system_context(data) is None
    assert not allows_empty_host_utterance(data)


def test_host_system_context_rejects_tampering_and_missing_secret(monkeypatch):
    from jvagent.action.interact import session_token

    monkeypatch.setattr(session_token, "_secret", lambda: "test-secret")
    envelope = _envelope()
    envelope["body"] = envelope["body"].replace("host policy", "forged policy")
    assert (
        verified_host_system_context(
            {"run_id": "run-1", "host_system_context": envelope}
        )
        is None
    )

    monkeypatch.setattr(session_token, "_secret", lambda: None)
    assert (
        verified_host_system_context(
            {"run_id": "run-1", "host_system_context": _envelope()}
        )
        is None
    )
