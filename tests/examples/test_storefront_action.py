"""Unit tests for the mock storefront action's search helpers.

The action ships with the leadgen reference agent under examples/, so it is not
importable as a normal package here — we load the module directly from its file
path and exercise the pure search functions.
"""

import importlib.util
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "jvagent_app"
    / "agents"
    / "jvagent"
    / "leadgen_agent"
    / "actions"
    / "contrib"
    / "storefront"
    / "storefront_action.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("_storefront_action", _MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


storefront = _load_module()


# ── FAQ search ────────────────────────────────────────────────────────────


def test_faq_free_trial_ranks_trial_first():
    matches = storefront.search_faq("do you offer a free trial?")
    assert matches
    assert matches[0]["id"] == "trial"


def test_faq_pricing_question():
    matches = storefront.search_faq("how much does it cost?")
    assert any(m["id"] == "pricing" for m in matches)


def test_faq_no_match_returns_empty():
    assert storefront.search_faq("zxqw nonsense token") == []


def test_faq_limit_respected():
    matches = storefront.search_faq("pricing support trial onboarding", limit=2)
    assert len(matches) <= 2


# ── Product search ────────────────────────────────────────────────────────


def test_products_churn_returns_customer_insights():
    results = storefront.search_products("churn prediction")
    assert results
    assert results[0]["name"] == "Customer Insights"


def test_products_empty_query_lists_catalog():
    results = storefront.search_products("")
    assert len(results) >= 1
    # every listed product exposes the public shape (no internal keywords)
    for p in results:
        assert set(p.keys()) == {"name", "category", "price", "description"}


def test_products_no_match_returns_empty():
    assert storefront.search_products("zxqw nonsense token") == []


def test_products_limit_respected():
    results = storefront.search_products("data", limit=2)
    assert len(results) <= 2


def test_products_public_shape_hides_keywords():
    results = storefront.search_products("dashboards")
    assert results
    assert "keywords" not in results[0]


# ── Tool wrappers return valid JSON envelopes ─────────────────────────────


@pytest.mark.asyncio
async def test_faq_tool_returns_ok_json():
    import json

    action = storefront.StorefrontAction()
    out = json.loads(await action.faq(query="free trial"))
    assert out["status"] == "ok"
    assert out["matches"]


@pytest.mark.asyncio
async def test_faq_tool_no_match_lists_topics():
    import json

    action = storefront.StorefrontAction()
    out = json.loads(await action.faq(query="zxqw nonsense"))
    assert out["status"] == "no_match"
    assert out["available_topics"]


@pytest.mark.asyncio
async def test_search_products_tool_returns_ok_json():
    import json

    action = storefront.StorefrontAction()
    out = json.loads(await action.search_products_tool(query="etl"))
    assert out["status"] == "ok"
    assert out["products"]


@pytest.mark.asyncio
async def test_faq_tool_accepts_question_alias():
    """Models often pass `question` to an FAQ tool instead of the declared `query`."""
    import json

    action = storefront.StorefrontAction()
    out = json.loads(await action.faq(question="do you have a free trial?"))
    assert out["status"] == "ok"
    assert out["matches"][0]["id"] == "trial"


@pytest.mark.asyncio
async def test_search_products_tool_accepts_alias():
    import json

    action = storefront.StorefrontAction()
    out = json.loads(await action.search_products_tool(search="churn"))
    assert out["status"] == "ok"
    assert out["products"][0]["name"] == "Customer Insights"


def test_coalesce_query_prefers_explicit():
    assert storefront._coalesce_query("real", {"question": "alias"}) == "real"
    assert storefront._coalesce_query("", {"question": "alias"}) == "alias"
    assert storefront._coalesce_query("", {}) == ""
