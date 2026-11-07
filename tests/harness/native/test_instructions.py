"""Channel instruction selection never changes execution authority."""

from types import SimpleNamespace
from typing import Any

import pytest

from jvagent.harness.instructions import effective_instructions


@pytest.mark.parametrize(
    "channel,expected",
    [
        ("default", "base\n\ndefault only"),
        ("voice", "base\n\nSpeak briefly."),
        ("voice_extra", "base"),
        ("web", "base"),
    ],
)
def test_exact_channel_selection(channel: str, expected: str) -> None:
    host = SimpleNamespace(
        instructions="base",
        channel_instructions={"default": "default only", "voice": "Speak briefly."},
    )
    assert effective_instructions(host, channel) == expected
    assert set(vars(host)) == {"instructions", "channel_instructions"}


@pytest.mark.parametrize(
    "profiles",
    [
        None,
        [],
        {"voice": True},
        {"voice": {}},
        {" voice": "private policy"},
        {"": "private policy"},
        {2: "private policy"},
    ],
)
def test_unmatched_invalid_profiles_fail_without_echo(profiles: Any) -> None:
    with pytest.raises(ValueError) as caught:
        effective_instructions(
            SimpleNamespace(instructions="base", channel_instructions=profiles), "web"
        )
    assert "private policy" not in str(caught.value)


def test_missing_profiles_preserve_existing_instructions() -> None:
    assert effective_instructions(SimpleNamespace(instructions="base"), "web") == "base"


@pytest.mark.parametrize("base", [None, 1, {}, []])
def test_invalid_base_fails(base: Any) -> None:
    with pytest.raises(ValueError):
        effective_instructions(SimpleNamespace(instructions=base), "web")


def test_first_user_presentation_is_selected_only_for_new_user() -> None:
    host = SimpleNamespace(
        instructions="Base",
        channel_instructions={"web": "Web"},
        first_user_instructions="Introduce yourself",
    )
    assert (
        effective_instructions(host, "web", new_user=True)
        == "Base\n\nWeb\n\nIntroduce yourself"
    )
    assert effective_instructions(host, "web") == "Base\n\nWeb"
    host.first_user_instructions = {"invalid": "value"}
    with pytest.raises(ValueError, match="literal text"):
        effective_instructions(host, "web")
