"""Plain-text shaping for Facebook Page comments.

The filter (bus pipeline) and the adapter (delivery) must apply the *same*
transformation — two copies drift, and the failure is raw markdown in a public
comment under a brand's post.
"""

from __future__ import annotations

from jvagent.action.facebook_action.facebook_comment_adapter import (
    FacebookCommentAdapter,
)
from jvagent.action.facebook_action.facebook_comment_text import (
    FACEBOOK_COMMENT_MAX_LENGTH,
    to_facebook_comment_text,
)


class TestMarkdownStripping:
    def test_bold_italic_and_headings(self) -> None:
        out = to_facebook_comment_text("## Title\n**bold** and *italic*")
        assert out == "Title\nbold and italic"

    def test_html_tags_become_plain_text(self) -> None:
        out = to_facebook_comment_text("a<br>b<b>c</b><i>d</i>")
        assert out == "a\nbcd"

    def test_link_keeps_its_url(self) -> None:
        """A comment reader cannot hover or recover a dropped URL."""
        out = to_facebook_comment_text("See [our pricing](https://x.test/pricing).")
        assert out == "See our pricing (https://x.test/pricing)."

    def test_link_whose_label_is_the_url_is_not_repeated(self) -> None:
        out = to_facebook_comment_text("[https://x.test](https://x.test)")
        assert out == "https://x.test"

    def test_truncates_to_the_facebook_limit(self) -> None:
        out = to_facebook_comment_text("x" * (FACEBOOK_COMMENT_MAX_LENGTH + 500))
        assert len(out) <= FACEBOOK_COMMENT_MAX_LENGTH
        assert out.endswith("...")

    def test_empty_input_is_safe(self) -> None:
        assert to_facebook_comment_text("") == ""


class TestSingleImplementation:
    def test_adapter_and_helper_agree(self) -> None:
        """The adapter must not carry its own copy of the transformation."""
        sample = "**Hi** — see [docs](https://x.test/d) <br> now"
        assert FacebookCommentAdapter._strip_markdown(sample) == (
            to_facebook_comment_text(sample)
        )

    async def test_filter_and_helper_agree(self) -> None:
        from jvagent.action.facebook_action.facebook_comment_filter import (
            FacebookCommentFilter,
        )
        from jvagent.action.response.message import ResponseMessage

        sample = "## Heading\n**bold** [link](https://x.test)"
        message = ResponseMessage(
            session_id="s", user_id="u", content=sample, channel="facebook_comment"
        )
        await FacebookCommentFilter().filter(message)
        assert message.content == to_facebook_comment_text(sample)
