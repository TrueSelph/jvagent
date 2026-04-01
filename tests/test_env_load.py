"""Tests for canonical jvspatial env helper usage in jvagent."""


def test_env_single_key_read(monkeypatch):
    from jvspatial.env import env

    monkeypatch.setenv("PRIMARY_KEY", "primary")
    assert env("PRIMARY_KEY") == "primary"


def test_env_returns_default_for_blank(monkeypatch):
    from jvspatial.env import env

    monkeypatch.setenv("PRIMARY_KEY", "   ")
    assert env("PRIMARY_KEY", default="fallback") == "fallback"


def test_env_bool_parsing(monkeypatch):
    from jvspatial.env import env, parse_bool

    monkeypatch.setenv("FEATURE_FLAG", "on")
    assert env("FEATURE_FLAG", default=False, parse=parse_bool) is True

    monkeypatch.setenv("FEATURE_FLAG", "invalid")
    assert env("FEATURE_FLAG", default=False, parse=parse_bool) is False


def test_env_int_parsing(monkeypatch):
    from jvspatial.env import env

    monkeypatch.setenv("RETENTION_DAYS", "30")
    assert env("RETENTION_DAYS", parse=int) == 30

    monkeypatch.setenv("RETENTION_DAYS", "-1")
    assert env("RETENTION_DAYS", parse=int) == -1


def test_s3_env_accessors(monkeypatch):
    from jvspatial.env import env

    monkeypatch.setenv("JVSPATIAL_S3_BUCKET_NAME", "b")
    monkeypatch.setenv("JVSPATIAL_S3_REGION", "eu-west-1")
    monkeypatch.setenv("JVSPATIAL_S3_ACCESS_KEY", "ak")
    monkeypatch.setenv("JVSPATIAL_S3_SECRET_KEY", "sk")
    assert env("JVSPATIAL_S3_BUCKET_NAME") == "b"
    assert env("JVSPATIAL_S3_REGION", default="us-east-1") == "eu-west-1"
    assert env("JVSPATIAL_S3_ACCESS_KEY") == "ak"
    assert env("JVSPATIAL_S3_SECRET_KEY") == "sk"


def test_env_file_interface_defaults_to_local(monkeypatch):
    from jvspatial.env import env

    monkeypatch.delenv("JVSPATIAL_FILE_STORAGE_PROVIDER", raising=False)
    assert env("JVSPATIAL_FILE_STORAGE_PROVIDER", default="local") == "local"
