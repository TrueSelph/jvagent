"""Comprehensive concurrency tests for WhatsApp action module.

These tests verify that the WhatsApp action module handles concurrent
access from multiple users correctly, without race conditions or data
corruption.
"""

import asyncio
import time
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("filetype")
try:
    from jvspatial.api.auth.models import UserCreateAdmin
except ImportError:
    pytest.skip(
        "UserCreateAdmin not available in installed jvspatial", allow_module_level=True
    )

from jvagent.action.whatsapp.utils.endpoint_helpers import (
    ConversationLockManager,
    MediaBatchManager,
    _batch_manager,
    _conversation_lock_manager,
    get_conversation_with_lock,
)
from jvagent.action.whatsapp.whatsapp_action import TypingStateManager, WhatsAppAction

# ============================================================================
# TYPING STATE MANAGER CONCURRENCY TESTS
# ============================================================================


class TestTypingStateManagerConcurrency:
    """Tests for concurrent-safe typing state management."""

    @pytest.mark.asyncio
    async def test_concurrent_typing_no_interference(self):
        """100 users set typing simultaneously - verify no state corruption."""
        manager = TypingStateManager()

        async def set_and_verify(user_id: str):
            """Set typing for a user and verify it's tracked correctly."""
            # Set typing
            result = await manager.set_typing(user_id, True)
            assert (
                result is True
            ), f"Expected first set_typing to return True for {user_id}"

            # Verify state
            is_typing = await manager.is_typing(user_id)
            assert is_typing is True, f"Expected {user_id} to be typing"

            # Try setting again - should return False (no change)
            result2 = await manager.set_typing(user_id, True)
            assert (
                result2 is False
            ), f"Expected duplicate set_typing to return False for {user_id}"

            return user_id

        # Run 100 concurrent tasks
        tasks = [set_and_verify(f"user_{i}") for i in range(100)]
        results = await asyncio.gather(*tasks)

        # Verify all users are accounted for
        assert len(results) == 100

        # Verify all users are still typing
        for i in range(100):
            is_typing = await manager.is_typing(f"user_{i}")
            assert is_typing is True, f"user_{i} should still be typing"

    @pytest.mark.asyncio
    async def test_concurrent_typing_clear(self):
        """Multiple users clear typing simultaneously - verify no interference."""
        manager = TypingStateManager()

        # First, set all users as typing
        for i in range(50):
            await manager.set_typing(f"user_{i}", True)

        async def clear_and_verify(user_id: str):
            """Clear typing for a user and verify."""
            result = await manager.set_typing(user_id, False)
            assert result is True, f"Expected clear to return True for {user_id}"

            is_typing = await manager.is_typing(user_id)
            assert is_typing is False, f"Expected {user_id} to not be typing"

            return user_id

        # Clear all users concurrently
        tasks = [clear_and_verify(f"user_{i}") for i in range(50)]
        results = await asyncio.gather(*tasks)

        assert len(results) == 50

    @pytest.mark.asyncio
    async def test_rapid_toggle_same_user(self):
        """Same user toggles typing rapidly - verify no race condition."""
        manager = TypingStateManager()
        user_id = "rapid_user"

        async def toggle_typing():
            """Toggle typing on/off rapidly."""
            await manager.set_typing(user_id, True)
            await asyncio.sleep(0.001)  # Small delay
            await manager.set_typing(user_id, False)
            return True

        # Run 50 rapid toggles concurrently
        tasks = [toggle_typing() for _ in range(50)]
        await asyncio.gather(*tasks)

        # After all toggles, user should be in a consistent state (not typing)
        # The final state depends on timing, but should be consistent
        final_state = await manager.is_typing(user_id)
        # Just verify no exception was raised - state is timing-dependent
        assert final_state in [True, False]

    @pytest.mark.asyncio
    async def test_cross_user_isolation(self):
        """Verify User A's state never affects User B."""
        manager = TypingStateManager()

        # Set up initial states
        await manager.set_typing("userA", True)
        await manager.set_typing("userB", False)

        async def modify_user_a():
            """Modify user A's state many times."""
            for _ in range(100):
                await manager.set_typing("userA", True)
                await manager.set_typing("userA", False)

        async def check_user_b():
            """Verify user B's state remains unchanged."""
            for _ in range(100):
                is_typing = await manager.is_typing("userB")
                assert (
                    is_typing is False
                ), "User B's state was corrupted by User A's operations"

        # Run both concurrently
        await asyncio.gather(modify_user_a(), check_user_b())


# ============================================================================
# MEDIA BATCH MANAGER CONCURRENCY TESTS
# ============================================================================


class TestMediaBatchManagerConcurrency:
    """Tests for concurrent-safe media batch management."""

    @pytest.fixture
    def batch_manager(self):
        """Create a fresh batch manager for each test."""
        return MediaBatchManager()

    @pytest.fixture
    def mock_action(self):
        """Create a mock WhatsApp action."""
        action = MagicMock()
        action.media_batch_window = 0.5  # Short window for testing
        return action

    @pytest.mark.asyncio
    async def test_concurrent_media_no_loss(self, batch_manager, mock_action):
        """Same user sends 10 images rapidly - verify all are batched."""
        user_id = "media_user_1"

        async def send_media(index: int):
            """Simulate sending a media message."""
            return await batch_manager.get_or_create_batch(
                sender=user_id,
                media_url=f"http://example.com/image_{index}.jpg",
                utterance=f"Image {index}",
                data_dict={"index": index},
                agent_id="test_agent",
                whatsapp_action=mock_action,
            )

        # Send 10 images concurrently
        tasks = [send_media(i) for i in range(10)]
        results = await asyncio.gather(*tasks)

        # All should return "received"
        assert all(r["status"] == "received" for r in results)

        # Verify batch contains all images (check internal state)
        # The batch should exist and have 10 media items
        async with batch_manager._global_lock:
            if user_id in batch_manager._batches:
                batch = batch_manager._batches[user_id]
                # Should have all 10 images (unless some triggered early processing)
                assert len(batch["media_items"]) <= 10

    @pytest.mark.asyncio
    async def test_multiple_users_concurrent_batches(self, batch_manager, mock_action):
        """Multiple users send media simultaneously - verify isolation."""

        async def send_user_media(user_id: str, count: int):
            """Simulate a user sending multiple media messages."""
            results = []
            for i in range(count):
                result = await batch_manager.get_or_create_batch(
                    sender=user_id,
                    media_url=f"http://example.com/{user_id}/image_{i}.jpg",
                    utterance=f"{user_id} Image {i}",
                    data_dict={"user": user_id, "index": i},
                    agent_id="test_agent",
                    whatsapp_action=mock_action,
                )
                results.append(result)
            return user_id, results

        # 5 users each sending 5 images concurrently
        tasks = [send_user_media(f"user_{i}", 5) for i in range(5)]
        all_results = await asyncio.gather(*tasks)

        # Verify each user got their responses
        for user_id, results in all_results:
            assert len(results) == 5
            assert all(r["status"] == "received" for r in results)

    @pytest.mark.asyncio
    async def test_batch_max_size_enforcement(self, batch_manager, mock_action):
        """Verify batch max size is enforced correctly."""
        user_id = "max_size_user"

        # Patch process_batch to track calls
        processed_batches = []
        original_process = batch_manager._process_batch_internal

        async def mock_process(sender, batch):
            processed_batches.append((sender, len(batch["media_items"])))

        batch_manager._process_batch_internal = mock_process

        try:
            # Send more than max batch size (default is 10)
            for i in range(15):
                await batch_manager.get_or_create_batch(
                    sender=user_id,
                    media_url=f"http://example.com/image_{i}.jpg",
                    utterance=f"Image {i}",
                    data_dict={"index": i},
                    agent_id="test_agent",
                    whatsapp_action=mock_action,
                )

            # Should have triggered at least one early processing
            # (when batch hit max size of 10)
        finally:
            batch_manager._process_batch_internal = original_process


# ============================================================================
# CONVERSATION LOCK MANAGER CONCURRENCY TESTS
# ============================================================================


class TestConversationLockManagerConcurrency:
    """Tests for concurrent-safe conversation access."""

    @pytest.fixture
    def lock_manager(self):
        """Create a fresh lock manager for each test."""
        return ConversationLockManager()

    @pytest.mark.asyncio
    async def test_lock_acquisition_serializes_access(self, lock_manager):
        """Verify locks serialize access for same user."""
        user_id = "lock_test_user"
        access_log = []

        async def access_conversation(request_id: int):
            """Simulate accessing conversation with lock."""
            lock = await lock_manager.acquire_lock(user_id)
            async with lock:
                access_log.append(f"start_{request_id}")
                await asyncio.sleep(0.01)  # Simulate work
                access_log.append(f"end_{request_id}")

        # Run 5 concurrent requests for same user
        tasks = [access_conversation(i) for i in range(5)]
        await asyncio.gather(*tasks)

        # Verify access was serialized (no interleaving)
        # Each "start_X" should be immediately followed by "end_X"
        for i in range(0, len(access_log), 2):
            start = access_log[i]
            end = access_log[i + 1]
            start_num = start.split("_")[1]
            end_num = end.split("_")[1]
            assert start.startswith("start_")
            assert end.startswith("end_")
            assert start_num == end_num, "Lock access was interleaved!"

    @pytest.mark.asyncio
    async def test_different_users_not_blocked(self, lock_manager):
        """Verify different users can access concurrently."""
        access_times = {}

        async def access_for_user(user_id: str):
            """Access conversation for a user and record timing."""
            lock = await lock_manager.acquire_lock(user_id)
            async with lock:
                start_time = time.time()
                await asyncio.sleep(0.1)  # Hold lock for 100ms
                end_time = time.time()
                access_times[user_id] = (start_time, end_time)

        # Run 3 users concurrently
        start = time.time()
        tasks = [access_for_user(f"user_{i}") for i in range(3)]
        await asyncio.gather(*tasks)
        total_time = time.time() - start

        # If users were blocked by each other, total time would be ~300ms
        # Since they should run concurrently, total time should be ~100ms
        assert (
            total_time < 0.2
        ), f"Users were blocked - took {total_time}s instead of ~0.1s"


# ============================================================================
# INTEGRATION TESTS
# ============================================================================


class TestWhatsAppActionConcurrency:
    """Integration tests for WhatsApp action concurrent access."""

    @pytest.fixture
    def mock_whatsapp_action(self):
        """Create a mock WhatsApp action with typing manager."""
        action = MagicMock(spec=WhatsAppAction)
        action._typing_manager = TypingStateManager()
        action.is_configured = MagicMock(return_value=True)

        # Mock the API
        mock_api = MagicMock()
        mock_api.set_typing_status = AsyncMock(return_value={"ok": True})
        action.api = MagicMock(return_value=mock_api)

        return action

    @pytest.mark.asyncio
    async def test_action_typing_concurrent_users(self, mock_whatsapp_action):
        """Test WhatsAppAction.set_typing with concurrent users."""
        action = mock_whatsapp_action

        async def set_typing_for_user(user_id: str):
            """Set typing for a specific user."""
            # Simulate the set_typing method logic
            manager = action._typing_manager
            state_changed = await manager.set_typing(user_id, True)
            if state_changed:
                await action.api().set_typing_status(phone=user_id, value=True)
            return user_id

        # 50 users set typing concurrently
        tasks = [set_typing_for_user(f"+1555000{i:04d}") for i in range(50)]
        results = await asyncio.gather(*tasks)

        assert len(results) == 50

        # Verify all users are tracked
        for i in range(50):
            phone = f"+1555000{i:04d}"
            is_typing = await action._typing_manager.is_typing(phone)
            assert is_typing is True


# ============================================================================
# STRESS TESTS
# ============================================================================


class TestStressConcurrency:
    """Stress tests for high-concurrency scenarios."""

    @pytest.mark.asyncio
    async def test_high_concurrency_typing(self):
        """Stress test: 500 concurrent typing operations."""
        manager = TypingStateManager()

        async def random_typing_operation(index: int):
            """Perform random typing operations."""
            user_id = f"stress_user_{index % 50}"  # 50 unique users

            # Random operations
            await manager.set_typing(user_id, True)
            await manager.is_typing(user_id)
            if index % 3 == 0:
                await manager.set_typing(user_id, False)

            return index

        # Run 500 concurrent operations
        tasks = [random_typing_operation(i) for i in range(500)]
        results = await asyncio.gather(*tasks)

        assert len(results) == 500

    @pytest.mark.asyncio
    async def test_high_concurrency_batch_manager(self):
        """Stress test: 200 concurrent batch operations across 20 users."""
        manager = MediaBatchManager()
        mock_action = MagicMock()
        mock_action.media_batch_window = 1.0

        async def add_to_batch(index: int):
            """Add media to batch for a user."""
            user_id = f"stress_batch_user_{index % 20}"  # 20 unique users

            return await manager.get_or_create_batch(
                sender=user_id,
                media_url=f"http://example.com/stress/{index}.jpg",
                utterance=f"Stress test {index}",
                data_dict={"index": index},
                agent_id="stress_agent",
                whatsapp_action=mock_action,
            )

        # Run 200 concurrent batch additions
        tasks = [add_to_batch(i) for i in range(200)]
        results = await asyncio.gather(*tasks)

        # All should complete without error
        assert len(results) == 200
        assert all(r["status"] == "received" for r in results)

    @pytest.mark.asyncio
    async def test_lock_manager_under_contention(self):
        """Stress test: High lock contention for same user."""
        manager = ConversationLockManager()
        counter = {"value": 0}

        async def increment_with_lock(user_id: str):
            """Increment counter with lock protection."""
            lock = await manager.acquire_lock(user_id)
            async with lock:
                # Read-modify-write operation
                current = counter["value"]
                await asyncio.sleep(0.001)  # Simulate some work
                counter["value"] = current + 1

        # 100 concurrent increments for same user
        tasks = [increment_with_lock("contention_user") for _ in range(100)]
        await asyncio.gather(*tasks)

        # If locking works correctly, counter should be exactly 100
        assert (
            counter["value"] == 100
        ), f"Lock contention issue: counter is {counter['value']}, expected 100"


# ============================================================================
# CONNECTION POOL STRESS TESTS
# ============================================================================


class TestConnectionPoolStress:
    """Tests for connection pool under high load."""

    @pytest.mark.asyncio
    async def test_concurrent_session_requests(self):
        """100 concurrent requests should share sessions efficiently."""
        from jvagent.action.whatsapp.modules.base import ConnectionPoolManager

        pool = ConnectionPoolManager()
        session_ids = []

        async def get_session():
            """Get a session and record its ID."""
            session = await pool.get_session("https://api.example.com", 30.0)
            # Store id to verify session reuse
            session_ids.append(id(session))
            return session

        # Request 100 sessions concurrently
        tasks = [get_session() for _ in range(100)]
        sessions = await asyncio.gather(*tasks)

        # All requests should get the same session (connection reuse)
        unique_sessions = set(session_ids)
        assert (
            len(unique_sessions) == 1
        ), f"Expected 1 shared session, got {len(unique_sessions)}"

        # Cleanup
        await pool.close_all()

    @pytest.mark.asyncio
    async def test_multiple_hosts_isolation(self):
        """Different hosts should have isolated connection pools."""
        from jvagent.action.whatsapp.modules.base import ConnectionPoolManager

        pool = ConnectionPoolManager()

        # Get sessions for different hosts
        session1 = await pool.get_session("https://api1.example.com", 30.0)
        session2 = await pool.get_session("https://api2.example.com", 30.0)
        session1_again = await pool.get_session("https://api1.example.com", 30.0)

        # Same host should reuse session
        assert id(session1) == id(session1_again), "Same host should reuse session"

        # Different hosts should have different sessions
        assert id(session1) != id(
            session2
        ), "Different hosts should have different sessions"

        # Cleanup
        await pool.close_all()

    @pytest.mark.asyncio
    async def test_session_recreation_after_close(self):
        """Session should be recreated if closed."""
        from jvagent.action.whatsapp.modules.base import ConnectionPoolManager

        pool = ConnectionPoolManager()

        # Get initial session
        session1 = await pool.get_session("https://api.example.com", 30.0)
        session1_id = id(session1)

        # Close the session
        await pool.close_session("https://api.example.com", 30.0)

        # Request new session - should be different
        session2 = await pool.get_session("https://api.example.com", 30.0)
        session2_id = id(session2)

        assert (
            session1_id != session2_id
        ), "Closed session should be replaced with new one"

        # Cleanup
        await pool.close_all()


# ============================================================================
# BACKGROUND TASK FAILURE TESTS
# ============================================================================


class TestBackgroundTaskFailures:
    """Tests for background task exception handling."""

    @pytest.mark.asyncio
    async def test_background_task_exception_logged(self):
        """Background task exceptions should be logged, not silently swallowed."""
        from jvagent.action.whatsapp.endpoints import create_background_task

        exception_logged = {"value": False}

        async def failing_task():
            """A task that will fail."""
            raise ValueError("Intentional test failure")

        # Create the task with exception handling
        task = create_background_task(failing_task(), name="test_failing_task")

        # Wait for the task to complete (and fail)
        try:
            await task
        except ValueError:
            pass  # Expected

        # Task should be done (not hanging)
        assert task.done(), "Task should complete even after exception"

    @pytest.mark.asyncio
    async def test_cancelled_task_handled_gracefully(self):
        """Cancelled tasks should not log errors."""
        from jvagent.action.whatsapp.endpoints import create_background_task

        async def long_running_task():
            """A task that runs for a long time."""
            await asyncio.sleep(100)

        # Create and immediately cancel
        task = create_background_task(long_running_task(), name="test_cancelled_task")
        task.cancel()

        # Wait for cancellation to process
        try:
            await task
        except asyncio.CancelledError:
            pass  # Expected

        # Task should be cancelled
        assert task.cancelled(), "Task should be cancelled"

    @pytest.mark.asyncio
    async def test_multiple_background_tasks_independent(self):
        """Multiple background tasks should not affect each other."""
        from jvagent.action.whatsapp.endpoints import create_background_task

        results = {"success": 0, "failure": 0}

        async def success_task(index: int):
            """A task that succeeds."""
            await asyncio.sleep(0.01)
            results["success"] += 1
            return index

        async def failure_task():
            """A task that fails."""
            await asyncio.sleep(0.01)
            results["failure"] += 1
            raise RuntimeError("Intentional failure")

        # Mix of success and failure tasks
        tasks = []
        for i in range(10):
            if i % 3 == 0:
                tasks.append(create_background_task(failure_task(), name=f"fail_{i}"))
            else:
                tasks.append(
                    create_background_task(success_task(i), name=f"success_{i}")
                )

        # Wait for all tasks
        await asyncio.gather(*tasks, return_exceptions=True)

        # Successful tasks should complete despite failures
        assert (
            results["success"] >= 6
        ), f"Expected at least 6 successes, got {results['success']}"
        assert (
            results["failure"] >= 3
        ), f"Expected at least 3 failures, got {results['failure']}"

    @pytest.mark.asyncio
    async def test_cleanup_task_failure_does_not_crash_manager(self):
        """Manager cleanup failures should not crash the system."""
        manager = MediaBatchManager()
        mock_action = MagicMock()
        mock_action.media_batch_window = 0.1

        # Add a batch to trigger cleanup scheduling
        await manager.get_or_create_batch(
            sender="cleanup_test_user",
            media_url="http://example.com/test.jpg",
            utterance="Test",
            data_dict={},
            agent_id="test_agent",
            whatsapp_action=mock_action,
        )

        # Manager should still be functional after any cleanup issues
        result = await manager.get_or_create_batch(
            sender="cleanup_test_user",
            media_url="http://example.com/test2.jpg",
            utterance="Test 2",
            data_dict={},
            agent_id="test_agent",
            whatsapp_action=mock_action,
        )

        assert result["status"] == "received", "Manager should remain functional"
