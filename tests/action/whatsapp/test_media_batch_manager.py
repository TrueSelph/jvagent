"""Unit tests for media batch manager mode resolution and behavior."""

import asyncio
import os
import tempfile
import time as time_module
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jvspatial.db.jsondb import JsonDB
from jvspatial.runtime.serverless import reset_serverless_mode_cache

pytest.importorskip("filetype")
try:
    from jvagent.action.whatsapp.utils import (
        media_batch_manager as media_batch_manager_mod,
    )
    from jvagent.action.whatsapp.utils.media_batch_manager import (
        MediaBatchManager,
        _get_media_batch_mode,
    )
except ImportError as e:
    pytest.skip(
        f"Could not import media_batch_manager: {e}",
        allow_module_level=True,
    )


@pytest.fixture(autouse=True)
def _reset_serverless_detection_cache():
    """Clear jvspatial LRU cache so env changes are visible between tests."""
    reset_serverless_mode_cache()
    yield
    reset_serverless_mode_cache()


class TestMediaBatchManagerModeResolution:
    """Tests for _get_media_batch_mode (uses ``is_serverless_mode()`` only)."""

    def test_async_when_not_serverless(self):
        """SERVERLESS_MODE=false -> async (long-running server)."""
        with patch.dict(os.environ, {"SERVERLESS_MODE": "false"}, clear=False):
            os.environ.pop("AWS_LAMBDA_FUNCTION_NAME", None)
            os.environ.pop("AWS_LAMBDA_RUNTIME_API", None)
            assert _get_media_batch_mode() == "async"

    def test_deferred_when_serverless_explicit(self):
        """SERVERLESS_MODE=true -> deferred (no cloud-specific env required)."""
        with patch.dict(os.environ, {"SERVERLESS_MODE": "true"}, clear=False):
            os.environ.pop("AWS_LAMBDA_FUNCTION_NAME", None)
            os.environ.pop("AWS_LAMBDA_RUNTIME_API", None)
            assert _get_media_batch_mode() == "deferred"

    def test_deferred_when_platform_detects_serverless(self):
        """Auto-detect (e.g. AWS_LAMBDA_FUNCTION_NAME) -> serverless -> deferred."""
        with patch.dict(
            os.environ, {"AWS_LAMBDA_FUNCTION_NAME": "my-func"}, clear=False
        ):
            os.environ.pop("SERVERLESS_MODE", None)
            assert _get_media_batch_mode() == "deferred"


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
        action.media_batch_window = 0.15  # Short window for fast test
        return action

    @pytest.mark.asyncio
    async def test_multiple_media_within_window_single_process_call(
        self, batch_manager, mock_action
    ):
        """Multiple media arriving within batch window result in one _process_batch_internal call."""
        with patch.dict(os.environ, {"SERVERLESS_MODE": "false"}, clear=False):
            os.environ.pop("AWS_LAMBDA_FUNCTION_NAME", None)
            os.environ.pop("AWS_LAMBDA_RUNTIME_API", None)
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


class TestProcessPersistentBatchJsonDB:
    """Deferred path: claim + delete via prime DB compound ops (no Mongo-only gate)."""

    @pytest.mark.asyncio
    async def test_process_persistent_batch_claims_and_deletes_jsondb(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = JsonDB(base_path=tmp)
            await db.save(
                media_batch_manager_mod.MEDIA_BATCHES_COLLECTION,
                {
                    "_id": "sender1",
                    "id": "sender1",
                    "media_items": [
                        {
                            "url": "http://example.com/a.jpg",
                            "utterance": None,
                            "message_type": "image",
                            "mime_type": "image/jpeg",
                        }
                    ],
                    "data": {"whatsapp_payload": {}},
                    "agent_id": "agent1",
                },
            )
            with patch.object(
                media_batch_manager_mod,
                "get_prime_database",
                return_value=db,
            ), patch.object(
                media_batch_manager_mod,
                "is_serverless_mode",
                return_value=True,
            ), patch.object(
                MediaBatchManager,
                "_process_batch_internal",
                new_callable=AsyncMock,
            ):
                ok = await media_batch_manager_mod.process_persistent_batch(
                    "sender1",
                    0.0,
                    process_at=None,
                )
            assert ok is True
            assert (
                await db.get(
                    media_batch_manager_mod.MEDIA_BATCHES_COLLECTION, "sender1"
                )
                is None
            )


class TestDeferredMediaBatchCoalescing:
    """Serverless persistent batching: slow multi-webhook albums must not split."""

    @pytest.mark.asyncio
    async def test_two_pushes_after_window_stay_one_batch_without_flush(self):
        """Simulates two image webhooks spaced beyond media_batch_window.

        Without an interposed flush_pending_batch_if_stale (removed from the media
        webhook path), both items remain in one document and process once together.
        """
        mock_action = MagicMock()
        mock_action.media_batch_window = 0.5
        clock = {"t": 1_000_000.0}

        def fake_time():
            return clock["t"]

        manager = MediaBatchManager()
        payload = {"whatsapp_payload": {"message_type": "image"}}

        with tempfile.TemporaryDirectory() as tmp:
            db = JsonDB(base_path=tmp)
            with (
                patch.object(
                    media_batch_manager_mod,
                    "get_prime_database",
                    return_value=db,
                ),
                patch.object(
                    media_batch_manager_mod,
                    "is_serverless_mode",
                    return_value=True,
                ),
                patch.object(
                    media_batch_manager_mod,
                    "create_task",
                    new_callable=AsyncMock,
                ),
                patch.object(
                    media_batch_manager_mod.time,
                    "time",
                    side_effect=fake_time,
                ),
                patch.object(
                    MediaBatchManager,
                    "_process_batch_internal",
                    new_callable=AsyncMock,
                ) as mock_process,
            ):
                await manager.get_or_create_batch(
                    sender="sender_album",
                    media_url="http://example.com/0.jpg",
                    utterance=None,
                    data_dict=payload,
                    agent_id="agent1",
                    whatsapp_action=mock_action,
                )
                clock["t"] += 0.6
                await manager.get_or_create_batch(
                    sender="sender_album",
                    media_url="http://example.com/1.jpg",
                    utterance=None,
                    data_dict=payload,
                    agent_id="agent1",
                    whatsapp_action=mock_action,
                )

                stored = await db.get(
                    media_batch_manager_mod.MEDIA_BATCHES_COLLECTION,
                    "sender_album",
                )
                assert stored is not None
                assert len(stored.get("media_items", [])) == 2

                await media_batch_manager_mod.process_persistent_batch(
                    "sender_album",
                    0.0,
                    process_at=clock["t"],
                )

            mock_process.assert_called_once()
            batch = mock_process.call_args[0][1]
            assert len(batch["media_items"]) == 2
            assert batch["media_items"][0]["url"] == "http://example.com/0.jpg"
            assert batch["media_items"][1]["url"] == "http://example.com/1.jpg"

    @pytest.mark.asyncio
    async def test_flush_between_p_splits_batch(self):
        """Stale flush between webhooks processes the first item only (old failure mode)."""
        mock_action = MagicMock()
        mock_action.media_batch_window = 0.5
        clock = {"t": 1_000_000.0}

        def fake_time():
            return clock["t"]

        manager = MediaBatchManager()
        payload = {"whatsapp_payload": {"message_type": "image"}}

        with tempfile.TemporaryDirectory() as tmp:
            db = JsonDB(base_path=tmp)
            with (
                patch.object(
                    media_batch_manager_mod,
                    "get_prime_database",
                    return_value=db,
                ),
                patch.object(
                    media_batch_manager_mod,
                    "is_serverless_mode",
                    return_value=True,
                ),
                patch.object(
                    media_batch_manager_mod,
                    "create_task",
                    new_callable=AsyncMock,
                ),
                patch.object(
                    media_batch_manager_mod.time,
                    "time",
                    side_effect=fake_time,
                ),
                patch.object(
                    MediaBatchManager,
                    "_process_batch_internal",
                    new_callable=AsyncMock,
                ) as mock_process,
            ):
                await manager.get_or_create_batch(
                    sender="sender_split",
                    media_url="http://example.com/a.jpg",
                    utterance=None,
                    data_dict=payload,
                    agent_id="agent1",
                    whatsapp_action=mock_action,
                )
                clock["t"] += 0.6
                await manager.flush_pending_batch_if_stale(
                    "sender_split",
                    mock_action.media_batch_window,
                    mock_action,
                )
                await manager.get_or_create_batch(
                    sender="sender_split",
                    media_url="http://example.com/b.jpg",
                    utterance=None,
                    data_dict=payload,
                    agent_id="agent1",
                    whatsapp_action=mock_action,
                )

            assert mock_process.call_count == 1
            first = mock_process.call_args[0][1]
            assert len(first["media_items"]) == 1
            assert first["media_items"][0]["url"] == "http://example.com/a.jpg"

            with (
                patch.object(
                    media_batch_manager_mod,
                    "get_prime_database",
                    return_value=db,
                ),
                patch.object(
                    media_batch_manager_mod,
                    "is_serverless_mode",
                    return_value=True,
                ),
                patch.object(
                    MediaBatchManager,
                    "_process_batch_internal",
                    new_callable=AsyncMock,
                ) as mock_process2,
            ):
                await media_batch_manager_mod.process_persistent_batch(
                    "sender_split",
                    0.0,
                    process_at=time_module.time(),
                )

            mock_process2.assert_called_once()
            second = mock_process2.call_args[0][1]
            assert len(second["media_items"]) == 1
            assert second["media_items"][0]["url"] == "http://example.com/b.jpg"
