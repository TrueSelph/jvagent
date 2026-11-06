"""Source coverage must include app-local, ignored-but-tracked assets."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.harness_asset_inventory import scan

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/harness_asset_inventory.py"
SNAPSHOT = (
    SCRIPT.parent.parent / ".planning/plans/2026-11-06-harness-tracked-assets.json"
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def track(repo: Path, name: str, text: str) -> Path:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", "-f", name], check=True)
    return path


def test_ignored_tracked_app_skill_and_companions_are_not_omitted(repo: Path) -> None:
    (repo / ".gitignore").write_text("examples/\n")
    name = "examples/app/skills/reading/SKILL.md"
    track(repo, name, "---\nname: reading\n---\nRead stored files.\n")
    script = track(
        repo,
        "examples/app/skills/reading/scripts/custom_tools.py",
        "raise AssertionError('Inventory must never import this module')\n",
    )
    before = scan(repo)
    asset = before["assets"]["skills"][0]
    assert asset["path"] == name and asset["scope"] == "app-example"
    assert asset["tracked_directory_files"] == 1
    script.write_text("raise AssertionError('A different extension')\n")
    assert (
        scan(repo)["assets"]["skills"][0]["tracked_directory_digest"]
        != asset["tracked_directory_digest"]
    )


def test_bom_aliases_are_recorded_without_runtime_normalization(repo: Path) -> None:
    track(
        repo,
        "tests/fixture/SKILL.md",
        "\ufeff---\nname: fixture_name\nallowed_tools: [read]\n---\nFixture.\n",
    )
    value = scan(repo)["assets"]["skills"][0]
    assert value["byte_order_mark"] is True
    assert value["policy_fields"] == {"allowed_tools": ["read"]}
    assert value["name"] == "fixture_name" and value["scope"] == "test-fixture"


def test_invalid_policy_shape_is_not_normalized_into_a_valid_list(repo: Path) -> None:
    track(
        repo,
        "skills/bad/SKILL.md",
        "---\nname: bad\nallowed-tools: read\n---\nRead.\n",
    )
    assert scan(repo)["assets"]["skills"][0]["policy_fields"] == {"allowed-tools": None}


def test_repository_root_skill_binds_its_tracked_companions(repo: Path) -> None:
    track(repo, "SKILL.md", "---\nname: root\n---\nRoot.\n")
    track(repo, "scripts/tool.py", "raise AssertionError('Do not import')\n")
    assert scan(repo)["assets"]["skills"][0]["tracked_directory_files"] == 1


def test_agent_report_contains_names_without_resolving_or_exposing_credentials(
    repo: Path,
) -> None:
    track(
        repo,
        "examples/app/agent.yaml",
        "agent: demo/bot\nactions:\n  - action: jvagent/orchestrator\n"
        "    context:\n      api_key: private-example-value\n"
        "      skills: [answer]\n  - action: contrib/storefront\n",
    )
    result = scan(repo)
    assert "private-example-value" not in json.dumps(result)
    value = result["assets"]["agent_declarations"][0]
    assert value["action_names"] == ["jvagent/orchestrator", "contrib/storefront"]
    assert value["execution_declarations"][0]["skill_names"] == ["answer"]
    assert value["execution_declarations"][0]["setting_names"] == ["api_key", "skills"]


def test_malformed_asset_is_named_instead_of_silently_skipped(repo: Path) -> None:
    track(repo, "skills/broken/SKILL.md", "No frontmatter.\n")
    result = scan(repo)
    assert result["totals"]["skills"] == 1
    assert result["assets"]["skills"][0]["metadata_error"]


def test_invalid_action_name_shape_remains_a_named_declaration(repo: Path) -> None:
    track(
        repo, "app/agent.yaml", "agent: demo/bot\nactions:\n  - action: {bad: shape}\n"
    )
    asset = scan(repo)["assets"]["agent_declarations"][0]
    assert asset["action_names"] == [None]
    assert asset["execution_declarations"] == []


def test_symlink_escape_refuses_scan_before_reading_target(repo: Path) -> None:
    outside = repo.parent / "outside-inventory-skill.md"
    outside.write_text("private-example-value")
    path = repo / "skills/escape/SKILL.md"
    path.parent.mkdir(parents=True)
    path.symlink_to(outside)
    subprocess.run(["git", "-C", str(repo), "add", str(path)], check=True)
    with pytest.raises(ValueError, match="ordinary contained file"):
        scan(repo)


def test_subdirectory_scan_still_covers_repository_root(repo: Path) -> None:
    track(repo, "skills/one/SKILL.md", "---\nname: one\n---\nOne.\n")
    track(repo, "other/info.yaml", "package:\n  name: demo/other\n")
    assert scan(repo / "skills") == scan(repo)


def test_check_refuses_asset_edits_and_newly_tracked_assets(repo: Path) -> None:
    skill = track(repo, "skills/one/SKILL.md", "---\nname: one\n---\nOne.\n")
    snapshot = repo / "reviewed.json"
    snapshot.write_text(json.dumps(scan(repo)))
    command = [
        sys.executable,
        str(SCRIPT),
        "--repo",
        str(repo),
        "--check",
        str(snapshot),
    ]
    assert subprocess.run(command, capture_output=True).returncode == 0
    skill.write_text("---\nname: one\n---\nChanged SOP.\n")
    assert subprocess.run(command, capture_output=True).returncode == 1
    snapshot.write_text(json.dumps(scan(repo)))
    track(repo, "new/info.yaml", "package:\n  name: demo/new\n")
    assert subprocess.run(command, capture_output=True).returncode == 1


def test_reviewed_inventory_covers_current_tracked_source() -> None:
    assert json.loads(SNAPSHOT.read_text()) == scan(SCRIPT.parent.parent)
