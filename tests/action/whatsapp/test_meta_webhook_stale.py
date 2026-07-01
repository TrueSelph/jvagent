"""Tests for Meta webhook stale callback detection."""

from jvagent.action.whatsapp.utils.meta_webhook_verify import (
    agent_id_from_callback_url,
    dashboard_action_for_stale,
    extract_callback_urls_from_graph,
    find_stale_callbacks,
    normalize_callback_url,
)


def test_agent_id_from_callback_url():
    url = (
        "https://desk8800.example.net/api/whatsapp/interact/webhook/"
        "n.Agent.69a75d4a0c084fedb48f2553"
    )
    assert agent_id_from_callback_url(url) == "n.Agent.69a75d4a0c084fedb48f2553"


def test_normalize_callback_url_strips_query():
    assert normalize_callback_url("https://x/callback?api_key=secret") == "https://x/callback"


def test_extract_callback_urls_from_phone_graph():
    graph = {
        "phone": {
            "webhook_configuration": {
                "application": (
                    "https://desk8800.example.net/api/whatsapp/interact/webhook/"
                    "n.Agent.3d3d6a76old"
                ),
                "whatsapp_business_account": (
                    "https://desk8800.example.net/api/whatsapp/interact/webhook/"
                    "n.Agent.a9c05d51old"
                ),
            }
        }
    }
    urls = extract_callback_urls_from_graph(graph)
    sources = {u["source"] for u in urls}
    assert "phone.webhook_configuration.application" in sources
    assert "phone.webhook_configuration.whatsapp_business_account" in sources
    assert len(urls) == 2


def test_find_stale_callbacks_flags_mismatched_agent_ids():
    expected = (
        "https://desk8800.example.net/api/whatsapp/interact/webhook/"
        "n.Agent.69a75d4a0c084fedb48f2553"
    )
    graph = {
        "phone": {
            "webhook_configuration": {
                "application": (
                    "https://desk8800.example.net/api/whatsapp/interact/webhook/"
                    "n.Agent.3d3d6a76old"
                ),
                "whatsapp_business_account": expected,
            }
        }
    }
    stale = find_stale_callbacks(
        graph, expected, "n.Agent.69a75d4a0c084fedb48f2553"
    )
    assert len(stale) == 1
    assert stale[0]["source"] == "phone.webhook_configuration.application"
    assert stale[0]["agent_id"] == "n.Agent.3d3d6a76old"
    assert "agent_id_mismatch" in stale[0]["reason"]


def test_dashboard_action_for_stale_application_url():
    stale = [
        {
            "source": "phone.webhook_configuration.application",
            "url": "https://x/old",
            "agent_id": "n.Agent.old",
        }
    ]
    action = dashboard_action_for_stale(stale)
    assert "App Dashboard" in action
    assert "application" not in action.lower() or "dashboard" in action.lower()


def test_find_stale_callbacks_empty_when_all_match():
    expected = "https://desk8800.example.net/api/whatsapp/interact/webhook/n.Agent.current"
    graph = {
        "waba": {
            "data": [
                {
                    "override_callback_uri": expected,
                }
            ]
        },
        "phone": {
            "webhook_configuration": {
                "application": expected,
                "whatsapp_business_account": expected,
            }
        },
    }
    stale = find_stale_callbacks(graph, expected, "n.Agent.current")
    assert stale == []
