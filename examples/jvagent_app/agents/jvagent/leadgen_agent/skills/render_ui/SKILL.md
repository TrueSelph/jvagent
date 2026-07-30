---
name: render_ui
description: >-
  Render a card or a set of selectable choices in the chat instead of
  describing them in prose. Use when the visitor asks to see options, plans,
  products, or a specific record (an order, a booking) and a structured,
  tappable layout reads better than a paragraph.
spec: jv
requires-actions:
  - UiAction
allowed-tools:
  - ui__render
always-active: true
tags:
  - ui
  - presentation
---

# Render UI — Standard Operating Procedure

The messenger owns the component catalog. You pick a component and supply its
data; you never write markup or layout.

## When to reach for a component

- **choices** — the visitor is picking from a small, known set (plans, times,
  categories). Tapping one sends its label as their next message.
- **card** — one concrete record worth laying out (an order, a plan, a product).

Do **not** render a component for ordinary prose, for a single fact, or to
restate something you already said. At most one per turn.

## How to call it

`ui__render` takes `component`, `fallback`, and `props`. **`props` is where the
content goes** — a call with empty `props` is rejected, because a component with
no data renders as plain text and is no better than replying normally.

`fallback` is a one-line plain-text version. It is what non-web channels, the
downloaded transcript, and screen readers receive, so write it as a real
sentence, not a placeholder.

### choices

```json
{
  "component": "choices",
  "fallback": "Plans: Starter $49/mo, Growth $199/mo, Enterprise custom.",
  "props": {
    "prompt": "Which plan fits best?",
    "options": [
      {"label": "Starter — $49/mo", "value": "Tell me about Starter",
       "description": "Core analytics"},
      {"label": "Growth — $199/mo", "value": "Tell me about Growth",
       "description": "More seats and connectors"},
      {"label": "Enterprise", "value": "Tell me about Enterprise",
       "description": "Custom pricing"}
    ]
  }
}
```

`value` is what gets sent when the visitor taps, so phrase it as something they
would plausibly say. Omit `value` and the label is sent verbatim.

### card

```json
{
  "component": "card",
  "fallback": "Growth plan — $199/mo, 20% off annually.",
  "props": {
    "title": "Growth",
    "subtitle": "$199 / month",
    "body": "Everything in Starter, plus more seats and connectors.",
    "fields": [{"label": "Annual", "value": "Save 20%"}],
    "actions": [
      {"label": "Start a trial", "kind": "send", "value": "I'd like to start a trial"},
      {"label": "See full pricing", "kind": "link", "href": "https://example.com/pricing"}
    ]
  }
}
```

`kind: "send"` sends `value` as the visitor's next message; `kind: "link"` opens
`href` (https, mailto or tel only).

## After rendering

Add **one short framing sentence** and stop — do not list the component's
contents again in your reply. The visitor can already see them.

If the tool returns `not_rendered`, read the reason: it names what was missing
(usually `props.options` or a card content key). Fix that and call once more; if
it still fails, just answer in text.
