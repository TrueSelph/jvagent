"""Tests for jvagent bundle artifact generation."""

from jvagent.bundle.bundler import Bundler


def test_refuses_overwrite_without_force(tmp_path):
    app_root = tmp_path / "app"
    app_root.mkdir()
    (app_root / "app.yaml").write_text("app:\n  id: test\n")
    (app_root / "Dockerfile").write_text("FROM scratch\n")

    bundler = Bundler(str(app_root))
    assert bundler.generate_dockerfile(force=False) is False
    assert (app_root / "Dockerfile").read_text() == "FROM scratch\n"


def test_force_overwrites_and_creates_backup(tmp_path):
    app_root = tmp_path / "app"
    app_root.mkdir()
    (app_root / "app.yaml").write_text("app:\n  id: test\n")
    (app_root / "Dockerfile").write_text("FROM scratch\n")

    bundler = Bundler(str(app_root))
    assert bundler.generate_dockerfile(force=True) is True
    assert "FROM public.ecr.aws" in (app_root / "Dockerfile").read_text()
    assert (app_root / "Dockerfile.bak").read_text() == "FROM scratch\n"
    assert (app_root / ".dockerignore").exists()
