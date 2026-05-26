# Bridge smoke baselines

This directory archives JSON dumps of `tests/action/bridge/smoke_bridge.py`
runs against `bridge_agent`. Each file is named `<short_sha>.json` (the
short SHA of the working-tree commit at run time) and captures:

- The full per-utterance metrics row (duration, model calls, prompt
  tokens, completion tokens, response chars, response preview, tasks).
- The structured parity report vs cockpit baseline `7d95904` with
  per-metric drift fractions and breach flags.
- A wall-clock unix timestamp.

Schema (top level):

```json
{
  "commit": "abc1234",
  "agent": "bridge_agent",
  "rows": [{ "label": "greeting", "duration_s": 2.85, ... }, ...],
  "parity": {
    "greeting": {
      "duration_s": {"observed": 2.85, "baseline": 2.93,
                     "drift": -0.027, "within_tol": true},
      "model_calls": {...},
      "prompt_tokens": {...},
      "response_chars": {...}
    },
    ...,
    "_summary": {
      "tolerance": 0.05,
      "green": true,
      "breaches": [],
      "baseline_commit": "7d95904"
    }
  },
  "wall_clock_unix": 1769424000.0
}
```

## Usage

```bash
# Run + archive (parity check enabled by default)
.venv/bin/python tests/action/bridge/smoke_bridge.py

# Custom utterance — skips parity, archives in stub form
.venv/bin/python tests/action/bridge/smoke_bridge.py \
    --utterance "Search for the Python release notes" --json

# Loosen tolerance to 10% for exploratory runs
.venv/bin/python tests/action/bridge/smoke_bridge.py --tolerance 0.10
```

## Trend tracking

The harness writes one file per short SHA. Re-running on the same commit
overwrites; bump the commit (any non-doc change) to retain prior numbers
in the trend. A nightly CI job (post-K) will commit a fresh JSON per
green run for long-term trend visualisation.

## Baseline source

The cockpit baseline at commit `7d95904` is reproduced inline in
`smoke_bridge.py::BASELINE_7D95904`. To re-derive numbers, run
`tests/action/cockpit/smoke_real_lm.py` against the cockpit example with
the same env / network / model config.
