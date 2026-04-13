"""Env coercion helpers and file-storage precedence."""

import os

import pytest

from jvagent.core.config import (
    get_config_value,
    get_file_storage_config,
    get_performance_config_value,
    parse_env_bool,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("off", False),
        ("maybe", None),
        ("", None),
    ],
)
def test_parse_env_bool(raw, expected):
    assert parse_env_bool(raw) == expected


def test_parse_env_bool_none():
    assert parse_env_bool(None) is None


def test_get_config_value_empty_env_falls_through(monkeypatch):
    monkeypatch.setenv("JVAGENT_PORT", "   ")
    assert get_config_value({}, "server.port", "JVAGENT_PORT", 8000) == 8000


def test_get_config_value_bool_default_int_coercion(monkeypatch):
    monkeypatch.setenv("JVAGENT_PORT", "9000")
    assert get_config_value({}, "server.port", "JVAGENT_PORT", 8000) == 9000


def test_get_performance_config_value_bool_invalid_returns_default(monkeypatch):
    monkeypatch.setenv("JVAGENT_ENABLE_PROFILING", "maybe")
    v = get_performance_config_value(
        {}, "enable_profiling", "JVAGENT_ENABLE_PROFILING", False, config_type=bool
    )
    assert v is False


def test_get_file_storage_config_uses_storage_provider(monkeypatch):
    monkeypatch.setenv("JVSPATIAL_FILE_STORAGE_PROVIDER", "s3")
    monkeypatch.delenv("JVSPATIAL_FILE_INTERFACE", raising=False)
    cfg = get_file_storage_config(os.getcwd(), {})
    assert cfg["provider"] == "s3"
