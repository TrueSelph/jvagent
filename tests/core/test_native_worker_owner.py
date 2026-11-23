"""Recovery may not infer process death from a changed worker epoch alone."""

from typing import Any

import pytest

from jvagent.core import execution_owner as owner


def test_worker_identity_is_stable_and_returned_as_independent_facts() -> None:
    original = owner.native_worker_metadata()
    assert original == owner.native_worker_metadata()
    assert original["jv_worker_pid"].isdecimal()
    original["jv_worker_epoch"] = "changed"
    assert original != owner.native_worker_metadata()


@pytest.mark.parametrize(
    "case",
    ["same-epoch", "live-pid", "permission", "foreign", "missing", "bad-pid", "dead"],
)
def test_dead_owner_gate_requires_local_identity_and_no_live_process(
    monkeypatch: Any, case: str
) -> None:
    current = {
        "jv_worker_epoch": "1" * 32,
        "jv_worker_host": "sha256:host",
        "jv_worker_pid": "100",
    }
    metadata = {**current, "jv_worker_epoch": "2" * 32, "jv_worker_pid": "101"}
    monkeypatch.setattr(owner, "native_worker_metadata", lambda: current)
    monkeypatch.setattr(owner, "require_native_local_ownership", lambda: None)
    if case == "same-epoch":
        metadata["jv_worker_epoch"] = current["jv_worker_epoch"]
    if case == "foreign":
        metadata["jv_worker_host"] = "sha256:foreign"
    if case == "missing":
        metadata = {}
    if case == "bad-pid":
        metadata["jv_worker_pid"] = "0"

    def probe(pid: int, signal: int) -> None:
        assert pid == 101 and signal == 0
        if case == "permission":
            raise PermissionError()
        if case == "dead":
            raise ProcessLookupError()

    monkeypatch.setattr(owner.os, "kill", probe)
    if case == "dead":
        owner.require_dead_native_owner(metadata)
    else:
        with pytest.raises(ValueError):
            owner.require_dead_native_owner(metadata)


def test_worker_host_identity_survives_a_fresh_process() -> None:
    import json
    import subprocess
    import sys

    current = owner.native_worker_metadata()
    if not current["jv_worker_host"].startswith("sha256:"):
        pytest.skip("OS boot identity is unavailable; recovery is refused")
    other = json.loads(
        subprocess.check_output(
            [
                sys.executable,
                "-c",
                "import json; from jvagent.core.execution_owner import native_worker_metadata; print(json.dumps(native_worker_metadata()))",
            ],
            text=True,
        )
    )
    assert current["jv_worker_host"] == other["jv_worker_host"]
    assert current["jv_worker_epoch"] != other["jv_worker_epoch"]
