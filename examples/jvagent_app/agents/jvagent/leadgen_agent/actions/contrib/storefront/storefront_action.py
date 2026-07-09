"""Mock storefront action — product-FAQ lookup + product-catalog search.

Two tools, both backed by in-memory dummy data so the leadgen reference agent's
``product_faq`` and ``product_search`` skills have something real to call while
the leadgen skill captures contact details in the same conversation:

- ``storefront__faq`` — keyword search over a small FAQ knowledge base.
- ``storefront__search_products`` — keyword search over a small product catalog.

The search helpers are plain module-level functions (no I/O, no visitor) so they
are unit-testable in isolation; the ``@tool`` methods just wrap them and return
JSON strings the orchestrator loop can read.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Annotated, Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.tooling.tool_decorator import tool

logger = logging.getLogger(__name__)

# ── Dummy data ────────────────────────────────────────────────────────────

FAQ_ENTRIES: List[Dict[str, Any]] = [
    {
        "id": "trial",
        "question": "Is there a free trial?",
        "keywords": ["trial", "free", "demo", "try", "evaluate", "test", "poc"],
        "answer": (
            "Yes — every plan starts with a 14-day free trial, no credit card "
            "required. You keep any dashboards you build during the trial."
        ),
    },
    {
        "id": "pricing",
        "question": "How much does it cost / what are the pricing plans?",
        "keywords": [
            "price",
            "pricing",
            "cost",
            "plan",
            "plans",
            "much",
            "tier",
            "billing",
        ],
        "answer": (
            "Three tiers: Starter $49/mo, Growth $199/mo, and Enterprise "
            "(custom). Annual billing saves 20%. All tiers include the core "
            "analytics platform; higher tiers add seats, connectors, and support."
        ),
    },
    {
        "id": "onboarding",
        "question": "How long does onboarding / setup take?",
        "keywords": [
            "onboarding",
            "setup",
            "install",
            "delivery",
            "long",
            "time",
            "implement",
            "deploy",
        ],
        "answer": (
            "Standard onboarding is 3-5 business days. Enterprise white-glove "
            "onboarding with a solutions engineer typically takes about 2 weeks."
        ),
    },
    {
        "id": "support",
        "question": "What support do you offer?",
        "keywords": ["support", "help", "sla", "csm", "success", "contact", "response"],
        "answer": (
            "Email and chat support on every plan. Growth adds priority support; "
            "Enterprise includes a dedicated customer success manager and a 99.9% "
            "uptime SLA."
        ),
    },
    {
        "id": "integrations",
        "question": "What integrations and data sources are supported?",
        "keywords": [
            "integration",
            "integrations",
            "connector",
            "connect",
            "source",
            "sources",
            "api",
            "webhook",
            "snowflake",
            "bigquery",
            "salesforce",
            "postgres",
            "segment",
        ],
        "answer": (
            "Native connectors for Snowflake, BigQuery, Postgres, Salesforce, and "
            "Segment, plus a REST API and outbound webhooks for anything custom."
        ),
    },
    {
        "id": "security",
        "question": "Is my data secure? What about compliance?",
        "keywords": [
            "security",
            "secure",
            "compliance",
            "gdpr",
            "soc",
            "soc2",
            "encryption",
            "privacy",
            "data",
        ],
        "answer": (
            "We're SOC 2 Type II certified and GDPR-compliant. Data is encrypted "
            "in transit (TLS 1.2+) and at rest (AES-256), with role-based access "
            "control on every plan."
        ),
    },
    {
        "id": "cancel",
        "question": "Can I cancel anytime? Do you offer refunds?",
        "keywords": [
            "cancel",
            "cancellation",
            "refund",
            "money",
            "back",
            "guarantee",
            "contract",
            "lock",
        ],
        "answer": (
            "Yes — cancel anytime, no lock-in. Annual plans come with a 30-day "
            "money-back guarantee."
        ),
    },
]

PRODUCTS: List[Dict[str, Any]] = [
    {
        "id": "analytics-platform",
        "name": "Analytics Platform",
        "category": "analytics",
        "price": "$199/mo",
        "description": "Real-time dashboards, funnels, and cohort analysis for product and growth teams.",
        "keywords": [
            "analytics",
            "dashboard",
            "dashboards",
            "realtime",
            "real-time",
            "funnel",
            "cohort",
            "reporting",
            "metrics",
            "product",
        ],
    },
    {
        "id": "dashboard-builder",
        "name": "Dashboard Builder",
        "category": "analytics",
        "price": "$49/mo",
        "description": "Drag-and-drop dashboard builder with 40+ chart types and no-code sharing.",
        "keywords": [
            "dashboard",
            "builder",
            "chart",
            "charts",
            "nocode",
            "no-code",
            "visualization",
            "visualize",
            "report",
            "drag",
        ],
    },
    {
        "id": "customer-insights",
        "name": "Customer Insights",
        "category": "analytics",
        "price": "$249/mo",
        "description": "ML-driven churn prediction, segmentation, and lifetime-value modeling.",
        "keywords": [
            "customer",
            "insights",
            "churn",
            "segmentation",
            "segment",
            "ml",
            "machine",
            "learning",
            "ltv",
            "lifetime",
            "predict",
            "prediction",
        ],
    },
    {
        "id": "data-warehouse",
        "name": "Data Warehouse",
        "category": "infrastructure",
        "price": "$499/mo",
        "description": "Managed columnar warehouse with auto-scaling storage and fast SQL.",
        "keywords": [
            "warehouse",
            "storage",
            "sql",
            "columnar",
            "scale",
            "database",
            "infrastructure",
            "data",
            "query",
        ],
    },
    {
        "id": "etl-pipeline",
        "name": "ETL Pipeline",
        "category": "infrastructure",
        "price": "$149/mo",
        "description": "Prebuilt connectors and transformation pipelines to move data reliably.",
        "keywords": [
            "etl",
            "pipeline",
            "ingestion",
            "ingest",
            "connector",
            "connectors",
            "transform",
            "transformation",
            "sync",
            "integration",
            "infrastructure",
        ],
    },
    {
        "id": "alerting-monitoring",
        "name": "Alerting & Monitoring",
        "category": "ops",
        "price": "$79/mo",
        "description": "Threshold and anomaly alerts delivered to Slack, email, and PagerDuty.",
        "keywords": [
            "alert",
            "alerts",
            "alerting",
            "monitor",
            "monitoring",
            "anomaly",
            "threshold",
            "slack",
            "pagerduty",
            "notify",
            "ops",
            "oncall",
        ],
    },
]

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Arg-name aliases: models don't always honor the declared `query` param name
# (gpt-4.1 tends to pass `question` to an FAQ tool). Coalesce the common ones so
# the tool never fails on a reasonable synonym.
_QUERY_ALIASES = ("question", "q", "text", "search", "term", "product", "topic")


def _coalesce_query(query: str, kwargs: Dict[str, Any]) -> str:
    if query:
        return query
    for alias in _QUERY_ALIASES:
        val = kwargs.get(alias)
        if val:
            return str(val)
    return ""


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall((text or "").lower())


def search_faq(query: str, limit: int = 3) -> List[Dict[str, Any]]:
    """Rank FAQ entries by token overlap with the query. Highest score first."""
    tokens = set(_tokenize(query))
    scored: List[tuple] = []
    for entry in FAQ_ENTRIES:
        haystack = set(entry["keywords"]) | set(_tokenize(entry["question"]))
        score = len(tokens & haystack)
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {"id": e["id"], "question": e["question"], "answer": e["answer"]}
        for _score, e in scored[: max(1, limit)]
    ]


def search_products(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Rank catalog products by token overlap with the query.

    An empty/whitespace query returns the full catalog (a plain listing).
    """

    def _public(p: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "name": p["name"],
            "category": p["category"],
            "price": p["price"],
            "description": p["description"],
        }

    tokens = set(_tokenize(query))
    if not tokens:
        return [_public(p) for p in PRODUCTS[: max(1, limit)]]

    scored: List[tuple] = []
    for product in PRODUCTS:
        haystack = (
            set(product["keywords"])
            | set(_tokenize(product["name"]))
            | {product["category"]}
        )
        score = len(tokens & haystack)
        if score:
            scored.append((score, product))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [_public(p) for _score, p in scored[: max(1, limit)]]


class StorefrontAction(Action):
    """Mock storefront: product-FAQ lookup + product-catalog search (dummy data)."""

    description: str = (
        "Mock storefront action exposing a product-FAQ lookup and a "
        "product-catalog search over in-memory dummy data."
    )

    @tool(name="storefront__faq")
    async def faq(
        self,
        query: Annotated[
            str, "The user's question, e.g. 'do you offer a free trial?'"
        ] = "",
        **kwargs: Any,
    ) -> str:
        """Answer FAQ questions about how the product is priced, bought, and supported.

        Covers pricing plans, free trial, onboarding time, support, integrations,
        security/compliance, and cancellation/refunds. Do NOT use this to find,
        recommend, or compare specific products — use storefront__search_products
        for "what do you have for X" / "which product does Y" requests.
        """
        query = _coalesce_query(query, kwargs)
        matches = search_faq(query)
        if not matches:
            topics = [e["question"] for e in FAQ_ENTRIES]
            return json.dumps(
                {
                    "status": "no_match",
                    "matches": [],
                    "available_topics": topics,
                    "hint": (
                        "No FAQ match. If the user is asking which product fits "
                        "a need, call storefront__search_products instead."
                    ),
                }
            )
        return json.dumps({"status": "ok", "matches": matches})

    @tool(name="storefront__search_products")
    async def search_products_tool(
        self,
        query: Annotated[
            str,
            "What the user is looking for — a product name, category, or need "
            "(e.g. 'churn prediction', 'dashboards', 'etl'). Empty lists all.",
        ] = "",
        limit: Annotated[Optional[int], "Max products to return (default 5)."] = None,
        **kwargs: Any,
    ) -> str:
        """Find, recommend, compare, or browse specific products in the catalog.

        Use for ANY request to discover which product fits a need — e.g. "what do
        you have for churn?", "which product does dashboards?", "show me your
        analytics tools", "compare X and Y", or just "what do you sell". Returns
        matching products with name, category, price, and description. Leave query
        empty to list the whole catalog.
        """
        query = _coalesce_query(query, kwargs)
        results = search_products(query, limit=limit or 5)
        if not results:
            categories = sorted({p["category"] for p in PRODUCTS})
            return json.dumps(
                {"status": "no_match", "products": [], "categories": categories}
            )
        return json.dumps({"status": "ok", "products": results})
