"""HTTP endpoints for ``BridgeInteractAction``.

No HTTP endpoints registered: Bridge observability (gear_trace,
helm_timings_seconds, helm_step_counts, helm_shift events) is exposed
via the standard ``GET /logs/agents/{id}`` endpoint reading
``Interaction.observability_metrics`` and
``Interaction.parameters['bridge_observability']``. This module exists
so the package follows the four-file action layout and
``from . import endpoints`` in ``__init__.py`` does not fail.
"""
