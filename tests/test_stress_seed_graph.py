"""Unit tests for the pure helpers in ``jvagent.stress_seed_graph``.

Smoke coverage for argument parsing, agent matching, and config building —
the parts that don't need a live App / DB.
"""

from __future__ import annotations

from typing import List
from unittest.mock import MagicMock

import pytest

from jvagent.stress_seed_graph import (
    StressSeedConfig,
    _format_agent_choices,
    _match_agent,
    _parse_agent_arg,
    _select_default_agent,
    parse_stress_seed_for_run,
)


def _agent(namespace: str, name: str) -> MagicMock:
    a = MagicMock()
    a.namespace = namespace
    a.name = name
    return a


# ---------------------------------------------------------------------------
# _parse_agent_arg
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, (None, None)),
        ("", (None, None)),
        ("  ", (None, None)),
        ("ns/name", ("ns", "name")),
        ("name_only", (None, "name_only")),
        ("ns/", (None, "ns/")),  # missing name → no slash split
    ],
)
def test_parse_agent_arg(raw, expected) -> None:
    assert _parse_agent_arg(raw) == expected


# ---------------------------------------------------------------------------
# _match_agent
# ---------------------------------------------------------------------------


def test_match_agent_exact_namespace_and_name() -> None:
    agents = [_agent("jv", "alpha"), _agent("jv", "beta"), _agent("acme", "alpha")]
    matched = _match_agent(agents, "jv", "alpha")
    assert matched.namespace == "jv" and matched.name == "alpha"


def test_match_agent_case_insensitive_within_namespace() -> None:
    agents = [_agent("jv", "Alpha")]
    matched = _match_agent(agents, "jv", "alpha")
    assert matched.name == "Alpha"


def test_match_agent_no_namespace_uses_first_name_match() -> None:
    agents = [_agent("acme", "support"), _agent("jv", "support")]
    matched = _match_agent(agents, None, "support")
    assert matched is agents[0]


def test_match_agent_raises_when_empty_name() -> None:
    with pytest.raises(SystemExit):
        _match_agent([_agent("jv", "alpha")], "jv", None)


# ---------------------------------------------------------------------------
# _select_default_agent
# ---------------------------------------------------------------------------


def test_select_default_agent_returns_lone_candidate() -> None:
    only = _agent("jv", "solo")
    assert _select_default_agent([only]) is only


def test_select_default_agent_errors_on_zero_candidates() -> None:
    with pytest.raises(SystemExit):
        _select_default_agent([])


# ---------------------------------------------------------------------------
# _format_agent_choices
# ---------------------------------------------------------------------------


def test_format_agent_choices_sorts_stable() -> None:
    agents = [_agent("jv", "beta"), _agent("acme", "alpha"), _agent("jv", "alpha")]
    formatted = _format_agent_choices(agents)
    assert formatted == "acme/alpha, jv/alpha, jv/beta"


# ---------------------------------------------------------------------------
# parse_stress_seed_for_run
# ---------------------------------------------------------------------------


def test_parse_stress_seed_returns_none_when_flag_absent() -> None:
    args: List[str] = ["--debug", "/path/to/app"]
    cfg, residual = parse_stress_seed_for_run(args, allow_env_defaults=False)
    assert cfg is None
    assert residual == args


def test_parse_stress_seed_strips_recognised_flags() -> None:
    args = [
        "--stress-seed",
        "--user-memory-nodes",
        "5",
        "--interactions-per-user-memory-node",
        "3",
        "--agent",
        "jv/test",
        "--debug",
    ]
    cfg, residual = parse_stress_seed_for_run(args, allow_env_defaults=False)
    assert isinstance(cfg, StressSeedConfig)
    assert cfg.user_memory_nodes == 5
    assert cfg.interactions_per_user_memory_node == 3
    assert cfg.agent == "jv/test"
    # ``--debug`` is unrelated to the seeder and should pass through.
    assert residual == ["--debug"]


def test_parse_stress_seed_errors_when_n_m_missing(monkeypatch) -> None:
    monkeypatch.delenv("JVAGENT_STRESS_SEED_USER_MEMORY_NODES", raising=False)
    monkeypatch.delenv(
        "JVAGENT_STRESS_SEED_INTERACTIONS_PER_USER_MEMORY_NODE", raising=False
    )
    with pytest.raises(SystemExit):
        parse_stress_seed_for_run(["--stress-seed"], allow_env_defaults=True)


def test_parse_stress_seed_picks_up_env_defaults(monkeypatch) -> None:
    monkeypatch.setenv("JVAGENT_STRESS_SEED_USER_MEMORY_NODES", "7")
    monkeypatch.setenv(
        "JVAGENT_STRESS_SEED_INTERACTIONS_PER_USER_MEMORY_NODE", "11"
    )
    cfg, _residual = parse_stress_seed_for_run(
        ["--stress-seed"], allow_env_defaults=True
    )
    assert cfg is not None
    assert cfg.user_memory_nodes == 7
    assert cfg.interactions_per_user_memory_node == 11
