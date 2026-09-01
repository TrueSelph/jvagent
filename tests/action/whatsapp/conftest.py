"""Shared fixtures for WhatsApp action tests."""

import pytest

from jvagent.action.utils.meta_webhook_dedup import clear_meta_wamid_cache


@pytest.fixture(autouse=True)
def _isolate_wamid_dedup(monkeypatch):
    """Force in-process dedup and reset module cache between tests."""
    monkeypatch.setenv("WHATSAPP_META_WAMID_DEDUP_BACKEND", "memory")
    monkeypatch.delenv("JVSPATIAL_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    clear_meta_wamid_cache()
    yield
    clear_meta_wamid_cache()
