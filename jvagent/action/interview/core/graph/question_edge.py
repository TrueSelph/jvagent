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
        branch_index: Position in the source question's branches list; -1 for default/sequential edges
        is_default: True for default_next or sequential fallback edges (no condition)
    """

    condition: Optional[Dict[str, Any]] = attribute(
        default=None,
        description="Condition dict for conditional traversal (e.g., {'op': 'equals', 'value': 'premium'})"
    )

    branch_index: Optional[int] = attribute(
        default=None,
        description="Position in source question's branches list; -1 for default"
    )

    is_default: bool = attribute(
        default=False,
        description="True for default_next / sequential fallback edges"
    )

