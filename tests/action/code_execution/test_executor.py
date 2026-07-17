"""SubprocessExecutor: success, nonzero exit, cwd confinement, timeout/kill,
output capping, env scrubbing. Runs real /bin/bash children in a tmp dir."""

from __future__ import annotations

import os

from jvagent.action.code_execution.executor import ExecRequest, SubprocessExecutor


async def _run(tmp_path, command, **kw):
    req = ExecRequest(command=command, cwd=str(tmp_path), **kw)
    return await SubprocessExecutor().run(req)


async def test_stdout_and_exit_zero(tmp_path):
    res = await _run(tmp_path, "echo hello")
    assert res.exit_code == 0
    assert res.stdout.strip() == "hello"
    assert res.timed_out is False


async def test_nonzero_exit_and_stderr(tmp_path):
    res = await _run(tmp_path, "echo oops >&2; exit 3")
    assert res.exit_code == 3
    assert "oops" in res.stderr


async def test_cwd_is_user_slice(tmp_path):
    res = await _run(tmp_path, "pwd")
    assert os.path.realpath(res.stdout.strip()) == os.path.realpath(str(tmp_path))


async def test_writes_land_in_cwd(tmp_path):
    res = await _run(tmp_path, "echo data > out.txt")
    assert res.exit_code == 0
    assert (tmp_path / "out.txt").read_text().strip() == "data"


async def test_timeout_kills(tmp_path):
    res = await _run(tmp_path, "sleep 5", timeout=0.4)
    assert res.timed_out is True


async def test_output_truncated_at_cap(tmp_path):
    res = await _run(
        tmp_path, "for i in $(seq 1 5000); do echo line$i; done", max_output_bytes=200
    )
    assert res.truncated is True
    assert len(res.stdout.encode()) <= 200


async def test_env_is_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPER_SECRET_TOKEN", "abc123")
    res = await _run(tmp_path, 'echo "${SUPER_SECRET_TOKEN:-MISSING}"')
    assert res.stdout.strip() == "MISSING"  # host secret not inherited


async def test_home_and_tmpdir_inside_cwd(tmp_path):
    res = await _run(tmp_path, "echo $HOME")
    assert os.path.realpath(res.stdout.strip()) == os.path.realpath(str(tmp_path))
