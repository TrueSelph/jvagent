"""Memory admin payload shape for durable user tags."""

from types import SimpleNamespace

from jvagent.memory.endpoints import _user_memory_payload


def test_user_memory_payload_includes_tag_values_not_keys_only():
    user = SimpleNamespace(
        memory={"note": "hello"},
        memory_tags={"topic": ["note"], "priority": ["note"]},
    )
    payload = _user_memory_payload(user)
    assert payload["_memory_tags"] == {"topic": ["note"], "priority": ["note"]}
