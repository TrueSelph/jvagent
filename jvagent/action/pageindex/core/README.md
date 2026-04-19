# Contrib Source — Do Not Modify

This directory contains third-party contrib source code for the PageIndex library.

**Do not modify files in this directory.** All adaptations, patches, and
overrides must be implemented outside this folder — typically in the parent
`jvagent/action/pageindex/` package (e.g., `llm_bridge.py`, `retrieval.py`,
`llm_override.py`).

The monkey-patch mechanism in `llm_override.py` exists specifically to inject
jvagent-specific behavior without touching contrib code. If you need to change
how a function from `core/utils.py` behaves, override it in `retrieval.py` or
another non-contrib module instead.

If an upstream update to PageIndex is needed, submit changes to the upstream
project and re-vendor the update here.