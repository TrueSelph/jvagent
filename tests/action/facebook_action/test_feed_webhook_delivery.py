"""At-least-once delivery handling for Facebook Page feed webhooks.

Meta delivers webhooks at-least-once and retries for days. Two properties keep
that from turning into duplicate **public** comments under a brand's post, and
both are invisible until it happens in production:

- a redelivered event is recognised and dropped (the Messenger path already
  does this for ``mid``)
- the agent turn is backgrounded, so the webhook answers immediately instead of
  holding the connection open long enough for Meta to time out and retry

The second causes the first: a slow turn *creates* the redelivery.
"""

from __future__ import annotations

import pytest

from jvagent.action.utils.meta_webhook_dedup import remember_meta_wamid


@pytest.fixture(autouse=True)
def _isolate_dedup_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dedup state is process-global; keep cases independent."""
    from jvagent.action.utils import meta_webhook_dedup as dedup

    monkeypatch.setattr(dedup, "_seen_wamids", type(dedup._seen_wamids)())
    monkeypatch.setattr(dedup, "_redis_client", None)
    monkeypatch.setattr(dedup, "_redis_init_attempted", True)


class TestDedupKeys:
    """The keys the endpoint builds, exercised through the real dedup helper."""

    def test_same_comment_id_is_seen_once(self) -> None:
        key = "fbcomment:c_1"
        assert remember_meta_wamid(key) is True
        assert remember_meta_wamid(key) is False

    def test_distinct_comments_both_pass(self) -> None:
        assert remember_meta_wamid("fbcomment:c_1") is True
        assert remember_meta_wamid("fbcomment:c_2") is True

    def test_reaction_retry_is_suppressed(self) -> None:
        """Identical event incl. timestamp = a redelivery."""
        key = ":".join(("fbreaction", "p_1", "c_1", "u_1", "LIKE", "1700000000"))
        assert remember_meta_wamid(key) is True
        assert remember_meta_wamid(key) is False

    def test_reacting_again_later_is_still_heard(self) -> None:
        """Remove-then-re-add produces a new timestamp and must not be eaten.

        This is why the reaction key includes the timestamp rather than being
        keyed on (post, comment, sender, type) alone.
        """
        base = ("fbreaction", "p_1", "c_1", "u_1", "LIKE")
        assert remember_meta_wamid(":".join((*base, "1700000000"))) is True
        assert remember_meta_wamid(":".join((*base, "1700000900"))) is True

    def test_comment_and_reaction_namespaces_do_not_collide(self) -> None:
        assert remember_meta_wamid("fbcomment:x") is True
        assert remember_meta_wamid("fbreaction:x") is True


class TestEndpointWiring:
    """Guard the shape of the handler itself.

    These assert on source structure rather than behaviour because driving the
    full webhook needs a live graph, an agent and a model; the properties worth
    protecting here are "the guard is present" and "the turn is not awaited
    inline", both of which a refactor can silently undo.
    """

    @staticmethod
    def _source() -> str:
        from pathlib import Path

        import jvagent.action.facebook_action.endpoints as endpoints_module

        return Path(endpoints_module.__file__).read_text(encoding="utf-8")

    def test_feed_paths_dedupe_before_dispatch(self) -> None:
        source = self._source()
        assert "fbcomment:" in source, "comment path lost its dedup key"
        assert "fbreaction" in source, "reaction path lost its dedup key"

    def test_feed_turns_are_backgrounded(self) -> None:
        """A bare ``await process_feed_*`` blocks the webhook response.

        Asserted via the task names, which exist only on the ``create_task``
        call — matching the call's own formatting would break on a reformat.
        """
        source = self._source()
        assert "create_task" in source
        for task_name in (
            "facebook_comment_interaction_",
            "facebook_reaction_interaction_",
        ):
            assert task_name in source, (
                f"expected a backgrounded task named {task_name!r} so the "
                "webhook answers before Meta times out and retries"
            )

    def test_long_comment_is_truncated_not_dropped(self) -> None:
        """Dropping left the commenter with no reply and no log line."""
        source = self._source()
        assert "comment_text[: FEED_COMMENT_UTTERANCE_MAX - 3]" in source
