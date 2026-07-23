"""Test that all JVAGENT_* environment keys used in code are documented."""

import re
from pathlib import Path


def test_env_keys_documented():
    """All JVAGENT_* keys in source code must appear in docs."""
    repo_root = Path(__file__).parent.parent
    source_dir = repo_root / "jvagent"
    doc_file = repo_root / "docs" / "environment-keys-reference.md"

    # Find all JVAGENT_* keys in source code
    keys_in_code = set()
    for py_file in source_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8", errors="ignore")
        # Match JVAGENT_<uppercase_with_underscores>
        matches = re.findall(r"JVAGENT_[A-Z_]+", content)
        keys_in_code.update(matches)

    # Read documentation
    doc_content = doc_file.read_text(encoding="utf-8")

    # Allowlist for keys that don't need documentation (false positives, templates, etc.)
    allowlist = {
        "JVAGENT_",  # Bare prefix
        "JVAGENT_SCAFFOLD_AGENT_DESCRIPTION__",  # Template placeholder
        "JVAGENT_SCAFFOLD_AGENT_TITLE__",  # Template placeholder
        "JVAGENT_ADMIN_",  # Partial match
        "JVAGENT_STRESS_SEED_INTERACTIONS_PER_USER_MEMORY_NODE",  # Test-only
        "JVAGENT_STRESS_SEED_USER_MEMORY_NODES",  # Test-only
        "JVAGENT_SENTDM_WEBHOOK_PATH_PREFIXES",  # Legacy/internal
        "JVAGENT_WARN_EMPTY_PLACEHOLDERS",  # Internal dev warning
        "JVAGENT_EMBED_ENDPOINTS_DISABLED",  # Internal flag
        "JVAGENT_MAX_PROFILES",  # Internal limit
        "JVAGENT_PROFILE_TTL",  # Internal cache
        "JVAGENT_CORE_ACTION_PATH",  # Internal path config
        "JVAGENT_INTERNAL_BASE_URL",  # Internal routing
        "JVAGENT_STRICT_USER_MEMORY_ID",  # Internal validation
        "JVAGENT_REPAIR_SCHEDULE_CRON",  # Internal scheduling
        "JVAGENT_PAGEINDEX_DB_ENDPOINT_URL",  # Covered by PageIndex section
        "JVAGENT_API_KEY",  # Covered by API_KEY_* docs
        "JVAGENT_BASE_PATH",  # Internal routing
    }

    undocumented = []
    for key in sorted(keys_in_code):
        if key in allowlist:
            continue
        # Check if key appears in documentation (case-sensitive)
        if key not in doc_content:
            undocumented.append(key)

    if undocumented:
        msg = (
            f"Found {len(undocumented)} JVAGENT_* keys used in code but not documented:\n"
            + "\n".join(f"  - {key}" for key in undocumented)
            + "\n\nAdd them to docs/environment-keys-reference.md or add to allowlist if appropriate."
        )
        raise AssertionError(msg)
