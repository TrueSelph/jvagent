"""The offline command fails clearly when the target library is unavailable."""

import builtins
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from jvagent.cli.harness_migrate import handle_harness_migrate


def test_missing_harness_extra_refuses_candidate_without_echoing_import_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    agent = tmp_path / "agents/demo/bot"
    directory = agent / "skills/answer"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "---\nname: answer\ndescription: Answer\n---\nAnswer.\n"
    )
    (agent / "agent.yaml").write_text(
        yaml.safe_dump(
            {
                "agent": "demo/bot",
                "actions": [
                    {
                        "action": "jvagent/orchestrator",
                        "context": {
                            "model": "gpt-4.1",
                            "model_action_type": "OpenAILanguageModelAction",
                        },
                    }
                ],
            }
        )
    )
    original = builtins.__import__

    def unavailable(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "pydantic_ai_harness":
            raise ImportError("private-import-diagnostic")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable)
    output = tmp_path / "candidate.yaml"
    assert (
        handle_harness_migrate(
            ["--agent", "demo/bot", "--skill", "answer", "--output", str(output)],
            str(tmp_path),
        )
        == 2
    )
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "blocked" and not output.exists()
    assert "private-import-diagnostic" not in json.dumps(report)
