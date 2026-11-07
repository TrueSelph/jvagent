"""Transitional import alias for the shared host-context service.

Remove with the legacy Orchestrator once all extension imports are migrated.
The module alias preserves existing signer imports and monkeypatch behavior.
"""

import sys
from importlib import import_module

sys.modules[__name__] = import_module("jvagent.core.host_context")
