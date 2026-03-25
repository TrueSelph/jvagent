"""Tests for channel normalization."""

import pytest

from jvagent.core.channel import normalize_channel


class TestNormalizeChannel:
    """Tests for normalize_channel."""

    def test_none_returns_default(self):
        assert normalize_channel(None) == "default"

    def test_empty_string_returns_default(self):
        assert normalize_channel("") == "default"

    def test_whitespace_returns_default(self):
        assert normalize_channel("   ") == "default"

    def test_web_returns_default(self):
        assert normalize_channel("web") == "default"
        assert normalize_channel("WEB") == "default"
        assert normalize_channel("  web  ") == "default"

    def test_default_passthrough(self):
        assert normalize_channel("default") == "default"

    def test_whatsapp_passthrough(self):
        assert normalize_channel("whatsapp") == "whatsapp"

    def test_messenger_passthrough(self):
        assert normalize_channel("messenger") == "messenger"

    def test_other_channels_passthrough(self):
        assert normalize_channel("sms") == "sms"
        assert normalize_channel("voice") == "voice"

    def test_strips_whitespace(self):
        assert normalize_channel("  whatsapp  ") == "whatsapp"
