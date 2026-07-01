"""Tests for derived Meta webhook verify token."""

from jvagent.action.whatsapp.utils.meta_verify_token import derive_meta_verify_token


class TestDeriveMetaVerifyToken:
    def test_stable_for_same_inputs(self):
        a = derive_meta_verify_token("n.Agent.abc123", "my-secret")
        b = derive_meta_verify_token("n.Agent.abc123", "my-secret")
        assert a == b
        assert len(a) == 32

    def test_differs_by_agent_id(self):
        a = derive_meta_verify_token("n.Agent.one", "secret")
        b = derive_meta_verify_token("n.Agent.two", "secret")
        assert a != b

    def test_differs_by_app_secret(self):
        a = derive_meta_verify_token("n.Agent.one", "secret-a")
        b = derive_meta_verify_token("n.Agent.one", "secret-b")
        assert a != b

    def test_empty_inputs_return_empty(self):
        assert derive_meta_verify_token("", "secret") == ""
        assert derive_meta_verify_token("n.Agent.x", "") == ""
