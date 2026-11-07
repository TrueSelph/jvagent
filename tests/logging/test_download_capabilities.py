"""Download credentials and duplicate SDK checkpoints do not belong in logs."""

import json
import logging

from jvagent.action.interact.webhook_pipeline import build_interaction_log_data
from jvagent.memory.interaction import Interaction

TOKEN = "test_artifact_capability_12345678"
LINK = f"https://files.example/api/actions/action/downloads/{TOKEN}"


def test_interaction_log_omits_sdk_state_and_masks_download_credentials() -> None:
    interaction = Interaction(
        user_id="caller", session_id="session", utterance="Share file"
    )
    interaction.response = f"Download [file]({LINK})"
    object.__setattr__(interaction, "native_steps", {"snapshots": [{"private": LINK}]})
    data, message = build_interaction_log_data(interaction, "app", "agent")
    encoded = json.dumps(data, default=str)
    assert TOKEN not in encoded + message
    assert "native_steps" not in encoded
    assert interaction.native_steps["snapshots"][0]["private"] == LINK


def test_proxy_and_access_log_filters_mask_credentials() -> None:
    from jvagent.logging.capabilities import install_download_log_filters

    install_download_log_filters()
    for name, message in [
        ("jvspatial.storage.managers.proxy", f"Created URL proxy: code={TOKEN}"),
        ("uvicorn.access", f'127.0.0.1 - "GET {LINK} HTTP/1.1" 200'),
    ]:
        record = logging.LogRecord(
            name, logging.INFO, __file__, 1, "%s", (message,), None
        )
        assert logging.getLogger(name).filter(record)
        assert TOKEN not in record.getMessage()
