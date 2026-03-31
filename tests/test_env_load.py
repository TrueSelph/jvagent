"""jvagent.env.load_env canonical JVSPATIAL_* mapping."""


def test_load_env_s3_canonical_keys(monkeypatch):
    from jvagent.env import load_env

    monkeypatch.setenv("JVSPATIAL_S3_BUCKET_NAME", "b")
    monkeypatch.setenv("JVSPATIAL_S3_REGION", "eu-west-1")
    monkeypatch.setenv("JVSPATIAL_S3_ACCESS_KEY", "ak")
    monkeypatch.setenv("JVSPATIAL_S3_SECRET_KEY", "sk")
    monkeypatch.delenv("JVSPATIAL_S3_REGION_NAME", raising=False)
    monkeypatch.delenv("JVSPATIAL_S3_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("JVSPATIAL_S3_SECRET_ACCESS_KEY", raising=False)
    e = load_env()
    assert e.s3_bucket_name == "b"
    assert e.s3_region_name == "eu-west-1"
    assert e.s3_access_key_id == "ak"
    assert e.s3_secret_access_key == "sk"


def test_load_env_file_interface_uses_storage_provider(monkeypatch):
    from jvagent.env import load_env

    monkeypatch.setenv("JVSPATIAL_FILE_STORAGE_PROVIDER", "s3")
    monkeypatch.delenv("JVSPATIAL_FILE_INTERFACE", raising=False)
    e = load_env()
    assert e.file_interface == "s3"


def test_load_env_file_interface_defaults_local(monkeypatch):
    from jvagent.env import load_env

    monkeypatch.delenv("JVSPATIAL_FILE_STORAGE_PROVIDER", raising=False)
    monkeypatch.delenv("JVSPATIAL_FILE_INTERFACE", raising=False)
    e = load_env()
    assert e.file_interface == "local"
