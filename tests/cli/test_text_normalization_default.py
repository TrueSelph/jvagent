"""jvagent turns off jvspatial's ASCII fold of persisted text at boot.

jvspatial's ``JVSPATIAL_TEXT_NORMALIZATION_ENABLED`` defaults to true and runs
``normalize_text_to_ascii`` on every save: accents stripped, every other
non-ASCII character stored as ``?``. Observed live on the example agent:
``café → naïve`` persisted as ``cafe ? naive`` and CJK as ``????``. For a
conversational agent that is silent data loss, so the DB env seeding step —
the one every entry point calls before the database is initialised — seeds the
key to ``false`` unless the operator set it.
"""

from __future__ import annotations

import pytest

from jvagent.cli.server_config import _set_db_env_from_config

APP_YAML = """
app: normalization_test
context:
  name: Normalization Test
  description: test
config:
  database:
    type: json
    path: ./test_jvdb
  logging:
    enabled: false
agents: []
"""

KEY = "JVSPATIAL_TEXT_NORMALIZATION_ENABLED"


@pytest.fixture
def app_root(tmp_path):
    (tmp_path / "app.yaml").write_text(APP_YAML.strip(), encoding="utf-8")
    return str(tmp_path)


def test_boot_seeds_the_fold_off_when_the_operator_said_nothing(app_root, monkeypatch):
    from jvspatial.utils.normalization import (
        is_text_normalization_enabled,
        normalize_text_to_ascii,
    )

    monkeypatch.delenv(KEY, raising=False)
    # What the store would do without the seed — the reason this test exists.
    assert normalize_text_to_ascii("café → 日本語") == "cafe ? ???"

    _set_db_env_from_config(app_root)

    import os

    assert os.environ[KEY] == "false"
    assert is_text_normalization_enabled() is False


@pytest.mark.parametrize("explicit", ["true", "false"])
def test_an_explicit_operator_value_always_wins(app_root, monkeypatch, explicit):
    import os

    monkeypatch.setenv(KEY, explicit)
    _set_db_env_from_config(app_root)
    assert os.environ[KEY] == explicit
