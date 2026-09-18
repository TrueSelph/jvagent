"""HP-12: contract versions and deployment matrix have no blank cells."""

from __future__ import annotations

import pytest

from jvagent.harness.contracts import CONTRACT_VERSION
from jvagent.harness.release import DEPLOYMENT_MATRIX, cell, release_record

pytestmark = pytest.mark.harness_conformance

BACKENDS = ("json", "sqlite", "mongodb", "dynamodb", "postgres")
MODES = ("local", "single-worker", "active-active")
ALLOWED = frozenset({"guaranteed", "degraded", "unsupported"})


def test_contract_version_is_tagged():
    assert CONTRACT_VERSION == "1.0.0"


def test_matrix_has_no_blank_cells():
    for backend in BACKENDS:
        for mode in MODES:
            value = cell(backend, mode)
            assert value in ALLOWED, f"{backend}/{mode}={value}"
    assert DEPLOYMENT_MATRIX["json"]["active-active"] == "unsupported"
    assert DEPLOYMENT_MATRIX["sqlite"]["active-active"] == "unsupported"


def test_release_record_names_limitations_and_rollback():
    rec = release_record(digest="deadbeef", topology="single-worker")
    assert rec["artifact_digest"] == "deadbeef"
    assert rec["contract_versions"]["native_caller"] == CONTRACT_VERSION
    assert rec["limitations"]
    assert rec["rollback"]
