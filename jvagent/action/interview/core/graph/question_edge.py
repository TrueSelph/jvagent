"""QuestionEdge for conditional question traversal.

This module provides QuestionEdge, a specialized edge that stores condition
metadata for conditional branching in interview question trees.
"""

from typing import Any, Dict, Optional

from jvspatial.core import Edge
from jvspatial.core.annotations import attribute


class QuestionEdge(Edge):
    """Edge connecting QuestionNodes with optional condition metadata.
    
    QuestionEdge extends Edge to store condition information that determines
    when this edge should be traversed based on previous question responses.
    The question is implicit from the branch context - conditions evaluate
    against the question that owns the branch.
    
    Attributes:
        condition: Optional condition dict with 'op' and optional 'value' keys
    """
    
    condition: Optional[Dict[str, Any]] = attribute(
        default=None,
        description="Condition dict for conditional traversal (e.g., {'op': 'equals', 'value': 'premium'})"
    )

