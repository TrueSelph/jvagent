"""Tests for YAML ${VAR} placeholder resolution."""


def test_resolve_required_placeholder_warns_when_unset(caplog):
    import os

    from jvagent.core import env_resolver as er

    caplog.set_level("WARNING", logger=er.__name__)
    name = "JVAGENT_TEST_REQUIRED_PLACEHOLDER_XYZ"
    os.environ.pop(name, None)
    try:
        assert er.resolve_env_placeholders(f"${{{name}:?}}") == ""
    finally:
        os.environ.pop(name, None)

    assert any(
        "JVAGENT_TEST_REQUIRED_PLACEHOLDER_XYZ" in r.message for r in caplog.records
    )


def test_resolve_warn_all_empty_placeholders(monkeypatch, caplog):
    import os

    from jvagent.core import env_resolver as er

    monkeypatch.setenv("JVAGENT_WARN_EMPTY_PLACEHOLDERS", "true")
    caplog.set_level("WARNING", logger=er.__name__)
    name = "JVAGENT_TEST_OPTIONAL_EMPTY_ABC"
    os.environ.pop(name, None)
    try:
        assert er.resolve_env_placeholders(f"${{{name}}}") == ""
    finally:
        os.environ.pop(name, None)

    assert any(name in rec.message for rec in caplog.records)
