"""Tests for Postiz endpoint per-action scoping (AUDIT-actions XC-12).

The previous implementation used ``PostizAction.find_one({"context.enabled":
True})`` which returned the first enabled PostizAction across ALL agents —
a cross-tenant leak. The fix resolves by explicit ``action_id``.
"""

import inspect

from jvagent.action.postiz_action.endpoints import (
    get_postiz_auth_url,
    get_postiz_providers,
)


def test_get_postiz_auth_url_requires_action_id():
    sig = inspect.signature(get_postiz_auth_url)
    assert "action_id" in sig.parameters
    assert "provider" in sig.parameters


def test_get_postiz_providers_requires_action_id():
    sig = inspect.signature(get_postiz_providers)
    assert "action_id" in sig.parameters


def test_endpoint_paths_under_actions_prefix():
    """SPEC §11 invariant 8: action endpoints live under /actions/{action_id}/.

    Both endpoints' registered path must include the action_id placeholder so
    the action's deregister flow can discover + unregister them.
    """
    cfg = getattr(get_postiz_auth_url, "_jvspatial_endpoint_config", {})
    assert "/actions/{action_id}/" in cfg.get("path", ""), cfg
    cfg2 = getattr(get_postiz_providers, "_jvspatial_endpoint_config", {})
    assert "/actions/{action_id}/" in cfg2.get("path", ""), cfg2
