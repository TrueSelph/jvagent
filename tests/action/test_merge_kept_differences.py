"""Merge-mode sync says what it did NOT apply.

Live finding (2026-09-06): the example's model slots were changed in
agent.yaml and synced with ``--update`` (merge); the new action registered but
the orchestrator kept ``model_action_type: OpenAILanguageModelAction``, and
four "LiteLLM" turns ran on the old wire before anyone noticed. Merge keeps
persisted values by design — but it should say so, per key.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from jvagent.action.actions import Actions


def _existing(**kw):
    base = dict(id="n.A.1", label="orchestrator", namespace="jvagent", enabled=True)
    base.update(kw)
    return SimpleNamespace(**base)


def _source(**kw):
    base = dict(id=None, label="orchestrator", namespace="jvagent", enabled=True)
    base.update(kw)
    return SimpleNamespace(**base)


def test_only_declared_keys_that_differ_are_reported():
    existing = _existing(
        model="gpt-4.1", model_action_type="OpenAILanguageModelAction", temperature=0.2
    )
    source = _source(
        model="openai/gpt-4.1",
        model_action_type="LiteLLMLanguageModelAction",
        temperature=0.2,
    )
    declared = {"model", "model_action_type", "temperature"}
    assert Actions._merge_kept_differences(existing, source, declared) == [
        "model",
        "model_action_type",
    ]


def test_undeclared_and_reserved_keys_never_count_as_drift():
    existing = _existing(model="gpt-4.1", enabled=False, _private="x")
    source = _source(model="openai/gpt-4.1", enabled=True, _private="y")
    # Not declared in YAML → class default, not drift.
    assert Actions._merge_kept_differences(existing, source, set()) == []
    # Declared but reserved / private → skipped.
    assert (
        Actions._merge_kept_differences(existing, source, {"enabled", "_private", "id"})
        == []
    )
    assert Actions._merge_kept_differences(existing, source, None) == []


def test_missing_attributes_are_ignored():
    existing = _existing(model="gpt-4.1")
    source = _source(model="gpt-4.1", brand_new_key=1)
    assert (
        Actions._merge_kept_differences(existing, source, {"model", "brand_new_key"})
        == []
    )
