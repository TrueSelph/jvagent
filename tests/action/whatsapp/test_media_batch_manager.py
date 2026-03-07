"""Unit tests for media batch manager mode resolution and behavior."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("filetype")
try:
    from jvagent.action.whatsapp.utils.media_batch_manager import (
        MediaBatchManager,
        _get_media_batch_mode,
    )
except ImportError as e:
    pytest.skip(
        f"Could not import media_batch_manager: {e}",
        allow_module_level=True,
    )


class TestMediaBatchManagerModeResolution:
    """Tests for _get_media_batch_mode resolver."""

    @pytest.fixture
    def mock_action(self):
        action = MagicMock()
        action.media_batch_mode = "async"
        return action

    def test_env_var_takes_priority(self, mock_action):
        """WHATSAPP_MEDIA_BATCH_MODE env var overrides action attribute."""
        with patch.dict(os.environ, {"WHATSAPP_MEDIA_BATCH_MODE": "disabled"}):
            assert _get_media_batch_mode(mock_action) == "disabled"
        with patch.dict(os.environ, {"WHATSAPP_MEDIA_BATCH_MODE": "lambda"}):
            assert _get_media_batch_mode(mock_action) == "lambda"

    def test_action_attribute_when_env_unset(self, mock_action):
        """Action media_batch_mode used when env not set or invalid."""
        with patch.dict(os.environ, {"WHATSAPP_MEDIA_BATCH_MODE": ""}):
            mock_action.media_batch_mode = "disabled"
            assert _get_media_batch_mode(mock_action) == "disabled"
            mock_action.media_batch_mode = "lambda"
            assert _get_media_batch_mode(mock_action) == "lambda"

    def test_default_async_when_no_config(self):
        """Default is async when env and action have no valid mode."""
        action = MagicMock()
        action.media_batch_mode = None
        with patch.dict(os.environ, {"WHATSAPP_MEDIA_BATCH_MODE": ""}):
            assert _get_media_batch_mode(action) == "async"

    def test_invalid_env_ignored(self, mock_action):
        """Invalid env value falls through to action or default."""
        with patch.dict(os.environ, {"WHATSAPP_MEDIA_BATCH_MODE": "invalid"}):
            mock_action.media_batch_mode = "lambda"
            assert _get_media_batch_mode(mock_action) == "lambda"


class TestMediaBatchManagerProcessSingleMediaInline:
    """Tests for process_single_media_inline method."""

    @pytest.fixture
    def batch_manager(self):
        return MediaBatchManager()

    @pytest.fixture
    def mock_process(self):
        with patch.object(
            MediaBatchManager,
            "_process_batch_internal",
            new_callable=AsyncMock,
        ) as m:
            yield m

    @pytest.mark.asyncio
    async def test_process_single_media_inline_calls_internal(
        self, batch_manager, mock_process
    ):
        """process_single_media_inline builds batch and calls _process_batch_internal."""
        await batch_manager.process_single_media_inline(
            sender="user1",
            media_url="http://example.com/image.jpg",
            utterance="caption",
            data_dict={"whatsapp_payload": {"message_type": "image"}},
            agent_id="agent1",
        )
        mock_process.assert_called_once()
        call_args = mock_process.call_args
        assert call_args[0][0] == "user1"
        batch = call_args[0][1]
        assert len(batch["media_items"]) == 1
        assert batch["media_items"][0]["url"] == "http://example.com/image.jpg"
        assert batch["media_items"][0]["utterance"] == "caption"
        assert batch["agent_id"] == "agent1"


class TestMediaBatchManagerAsyncBatching:
    """Tests for async mode batching: multiple media coalesce into one interact call."""

    @pytest.fixture
    def batch_manager(self):
        return MediaBatchManager()

    @pytest.fixture
    def mock_action(self):
        action = MagicMock()
        action.media_batch_mode = "async"
        action.media_batch_window = 0.15  # Short window for fast test
        return action

    @pytest.mark.asyncio
    async def test_multiple_media_within_window_single_process_call(
        self, batch_manager, mock_action
    ):
        """Multiple media arriving within batch window result in one _process_batch_internal call."""
        with patch.dict(os.environ, {"WHATSAPP_MEDIA_BATCH_MODE": "async"}):
            with patch.object(
                MediaBatchManager,
                "_process_batch_internal",
                new_callable=AsyncMock,
            ) as mock_process:
                # Add 3 media in quick succession (simulating rapid webhooks)
                for i in range(3):
                    await batch_manager.get_or_create_batch(
                        sender="user1",
                        media_url=f"http://example.com/image_{i}.jpg",
                        utterance=f"Image {i}",
                        data_dict={"whatsapp_payload": {"message_type": "image"}},
                        agent_id="agent1",
                        whatsapp_action=mock_action,
                    )

                # Wait for timer to fire (media_batch_window + small buffer)
                await asyncio.sleep(0.2)

                # Should have been called once with batch of 3 items
                assert mock_process.call_count == 1
                call_args = mock_process.call_args
                assert call_args[0][0] == "user1"
                batch = call_args[0][1]
                assert len(batch["media_items"]) == 3
                urls = [item["url"] for item in batch["media_items"]]
                assert urls == [
                    "http://example.com/image_0.jpg",
                    "http://example.com/image_1.jpg",
                    "http://example.com/image_2.jpg",
                ]
