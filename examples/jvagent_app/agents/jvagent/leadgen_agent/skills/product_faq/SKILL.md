---
name: product_faq
description: >-
  Answer common product questions — pricing, free trial, onboarding time,
  support, integrations, security/compliance, cancellation/refunds — from the
  storefront FAQ knowledge base. Use whenever the visitor asks a factual
  question about how the product works, costs, or is bought.
spec: jv
requires-actions:
  - StorefrontAction
allowed-tools:
  - storefront__faq
tags:
  - faq
  - sales
---

# Product FAQ — Standard Operating Procedure

Use this when the visitor asks a common question about the product — pricing,
free trial, onboarding/setup time, support, integrations, security/compliance,
or cancellation/refunds.

1. Call `storefront__faq` with the visitor's question as `query`.
2. Answer **from the returned `matches`** — quote the facts in the `answer`
   field, rephrased naturally. Do not invent pricing, features, or policies that
   aren't in the result.
3. If `status` is `no_match`, tell the visitor what you *can* help with using the
   returned `available_topics`, and offer to connect them with the team for
   anything else.
4. Keep it short and conversational. After answering, it's natural to ask a light
   follow-up ("want me to pull up the plans that fit your team?") — but never
   pressure.

This skill answers questions; it does not collect contact details. If the
visitor volunteers their name, email, company, or interest while asking, the
leadgen capability handles that in the same turn — you don't manage storage here.
