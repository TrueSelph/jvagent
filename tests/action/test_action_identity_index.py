"""Action unique-index shape must match canonical identity (AUDIT-core C3).

The uniqueness constraint has to be keyed on the full identity tuple
(agent_id, namespace, label). Keying on (agent_id, label) alone rejects a
second action that legitimately reuses a label across namespaces with E11000,
and register_action then silently drops it."""

from __future__ import annotations

from jvagent.action.base import Action
from jvagent.core.index_bootstrap import DEPRECATED_INDEXES


def _unique_compound_indexes():
    out = []
    for idx in Action.get_indexes():
        if isinstance(idx, dict) and idx.get("unique") and "fields" in idx:
            out.append(idx)
    return out


def test_action_unique_index_keyed_on_full_identity():
    uniques = _unique_compound_indexes()
    assert uniques, "Action must declare a unique compound index"

    fields_sets = [[f for f, _ in idx["fields"]] for idx in uniques]
    assert [
        "context.agent_id",
        "context.namespace",
        "context.label",
    ] in fields_sets, f"expected full-identity unique key, got {fields_sets}"


def test_action_unique_index_partial_filter_requires_all_three():
    uniques = _unique_compound_indexes()
    target = next(
        idx
        for idx in uniques
        if [f for f, _ in idx["fields"]]
        == ["context.agent_id", "context.namespace", "context.label"]
    )
    pfe = target.get("partialFilterExpression") or {}
    for field in ("context.agent_id", "context.namespace", "context.label"):
        assert field in pfe, f"partial filter must scope on {field}"


def test_old_agent_label_index_is_deprecated():
    # The old (agent_id, label) index must be dropped on migration so the new
    # full-identity unique index can be created without a wrong-shape leftover.
    assert "agent_label" in DEPRECATED_INDEXES.get("node", [])


def test_no_label_only_unique_index_remains():
    for idx in _unique_compound_indexes():
        fields = [f for f, _ in idx["fields"]]
        assert fields != [
            "context.agent_id",
            "context.label",
        ], "the namespace-less unique key must be gone"
