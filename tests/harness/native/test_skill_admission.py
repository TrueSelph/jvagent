"""Prerequisite and channel policy contract tests."""

from pathlib import Path

import pytest

from jvagent.harness.skill_admission import admit_skill


@pytest.mark.parametrize(
    "declaration",
    [
        "requires-actions: [Missing]",
        "requires-tools: [missing]",
        "output-contract: evidence_required",
        "allowed-channels: [sms]",
        "denied-channels: [web]",
        "spec: claude",
        "task-lock: true",
        "requires-tasks: [flow]",
        "extends: another_skill",
        'allowed-tools: ["lookup*"]',
    ],
)
def test_unimplemented_or_unsatisfied_policy_is_rejected(
    tmp_path: Path, declaration: str
) -> None:
    (tmp_path / "SKILL.md").write_text(
        "---\nname: test\n" + declaration + "\n---\nInstructions\n"
    )
    with pytest.raises(ValueError):
        admit_skill(tmp_path, available_actions=[], available_tools=[], channel="web")


def test_satisfied_policy_passes(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text(
        "---\nname: test\nrequires-actions: [LookupAction]\nrequires-tools: [lookup]\nallowed-tools: [lookup]\nallowed-channels: [web]\n---\nInstructions\n"
    )
    result = admit_skill(
        tmp_path,
        available_actions=["LookupAction"],
        available_tools=["lookup"],
        channel="web",
    )
    assert result["name"] == "test"
