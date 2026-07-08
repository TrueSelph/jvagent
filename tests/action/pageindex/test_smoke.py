"""Minimal smoke: pageindex sub-actions import and instantiate."""

from jvagent.action.pageindex.pageindex_action.pageindex_action import PageIndexAction


def test_pageindex_action_instantiates() -> None:
    action = PageIndexAction()
    assert action.__class__.__name__ == "PageIndexAction"
