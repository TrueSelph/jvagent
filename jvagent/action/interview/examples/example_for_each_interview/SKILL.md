---
name: example_for_each_interview
description: >-
  Reference interview demonstrating for_each per-item subpart fields. Collects
  comma-separated item IDs, then asks title and quantity for each item.
spec: jv
task-lock: true
requires-actions:
  - InterviewAction
extends: action:jvagent/interview
tags:
  - example
  - interview
  - for_each
interview:
  title: Item registration
  summary: Reference for_each expansion — parent item_ids expands into per-item subparts.
  confirm: manual
  fields:
    - key: item_ids
      prompt: What item IDs would you like to register? (comma-separated)
      required: true
      guidance: One or more short alphanumeric IDs separated by commas.
      validator: validate_item_ids
      post_processor: expand_item_ids
      for_each_prefix: item_id_prefix
      for_each:
        fields:
          - key: title
            prompt: What is the title for this item?
            required: true
            validator: text
            validator_args:
              min_length: 2
              max_length: 200
          - key: quantity
            prompt: How many units?
            required: true
            validator: validate_quantity
          - key: notes
            prompt: Any optional notes?
            required: false
            validator: text
            validator_args:
              min_length: 2
              max_length: 500
  handlers:
    review: for_each_review
    complete: for_each_complete
---

> **Note:** Reference package under `interview/examples/` (not auto-discovered).

## Custom instructions

Collect item IDs, then walk each item's subpart fields in order. Read
`session.context["for_each"]["item_ids"]["records"]` in the completion handler.
