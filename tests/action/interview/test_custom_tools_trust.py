"""custom_tools.py is exec'd only for bundled or explicitly trusted skills."""

from __future__ import annotations

from pathlib import Path

import pytest

from jvagent.action.interview.hooks import load_hook_function
from jvagent.action.interview.spec import parse_interview_spec


def test_untrusted_skill_custom_tools_not_loaded(tmp_path: Path):
    skill_dir = tmp_path / "evil_interview"
    scripts = skill_dir / "scripts"
    scripts.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: evil_interview\nspec: jv\n---\n",
        encoding="utf-8",
    )
    (scripts / "custom_tools.py").write_text(
        "def validate_evil(ctx):\n    return ctx.valid()\n",
        encoding="utf-8",
    )
    spec = parse_interview_spec(
        {"name": "evil_interview", "fields": []},
        source_dir=str(skill_dir),
        default_name="evil_interview",
    )

    assert load_hook_function(spec, "validate_evil") is None


def test_trusted_skill_custom_tools_loaded(tmp_path: Path):
    skill_dir = tmp_path / "trusted_interview"
    scripts = skill_dir / "scripts"
    scripts.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: trusted_interview\nspec: jv\ntrust_tier: trusted\n---\n",
        encoding="utf-8",
    )
    (scripts / "custom_tools.py").write_text(
        "def validate_ok(ctx):\n    return ctx.valid()\n",
        encoding="utf-8",
    )
    spec = parse_interview_spec(
        {"name": "trusted_interview", "fields": []},
        source_dir=str(skill_dir),
        default_name="trusted_interview",
    )

    func = load_hook_function(spec, "validate_ok")
    assert callable(func)
