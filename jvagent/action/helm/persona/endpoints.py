"""HTTP endpoints for ``PersonaHelm``.

No HTTP endpoints needed: per-helm timing and step counts are exposed
via the standard ``GET /logs/agents/{id}`` endpoint reading
``Interaction.parameters['bridge_observability']``. Stub kept so the
four-file action layout holds and ``from . import endpoints`` does
not fail.
"""
