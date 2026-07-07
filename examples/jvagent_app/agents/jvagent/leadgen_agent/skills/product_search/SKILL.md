---
name: product_search
description: >-
  Find, compare, or browse products in the storefront catalog by name,
  category, or need (analytics, dashboards, data warehouse, ETL, alerting,
  churn/customer insights). Use when the visitor wants to discover which
  product fits, compare options, or see what's available.
spec: jv
requires-actions:
  - StorefrontAction
allowed-tools:
  - storefront__search_products
tags:
  - catalog
  - sales
---

# Product Search — Standard Operating Procedure

Use this when the visitor wants to find a product, compare options, or browse
what's available ("what do you have for churn?", "show me your dashboard tools",
"which product fits an 8-person data team?").

1. Call `storefront__search_products` with a `query` built from the need the
   visitor described (a product name, a category, or the problem they're solving).
   Leave `query` empty to list the full catalog.
2. Present the returned `products` — name, price, and a one-line reason it fits.
   Recommend the top 1-2 rather than dumping the whole list unless they asked to
   see everything. Do not invent products, prices, or features not in the result.
3. If `status` is `no_match`, say so plainly and offer the available `categories`
   as directions, or ask a clarifying question about their use case.
4. Tie the recommendation back to what the visitor said they need. A natural next
   step is offering more detail or a demo — but keep it light, never pushy.

This skill recommends products; it does not collect contact details. If the
visitor shares their name, email, company, or interest while browsing, the
leadgen capability captures it in the same turn — you don't manage storage here.
