"""HTTP endpoints for ``ReasoningHelm``.

No HTTP endpoints needed: per-helm timing, shift-log, and engine
observability are exposed via the standard ``GET /logs/agents/{id}``
endpoint reading ``Interaction.observability_metrics`` and
``Interaction.parameters['bridge_observability']``. This module exists
so the package follows the four-file action layout and
``from . import endpoints`` does not fail.
"""
