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
    
    Attributes:
        condition: Optional condition dict with 'question' and 'equals' keys
    """
    
    condition: Optional[Dict[str, Any]] = attribute(
        default=None,
        description="Condition dict for conditional traversal (e.g., {'question': 'user_type', 'equals': 'premium'})"
    )

