"""Processing domain.

Handles response processing and directive generation.
"""

from .directive_builder import DirectiveBuilder
from .target_resolver import TargetResolver

__all__ = ["DirectiveBuilder", "TargetResolver"]
