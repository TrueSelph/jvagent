"""HTTP endpoints for ``ReflexHelm``.

No HTTP endpoints needed: per-helm observability (classification
duration, ``detected_language``, verb chosen) is exposed via the
standard ``GET /logs/agents/{id}`` endpoint reading
``Interaction.observability_metrics`` and
``Interaction.parameters['bridge_observability']``. Stub kept so the
four-file action layout holds and ``from . import endpoints`` does
not fail.
"""
