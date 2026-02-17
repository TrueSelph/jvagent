from jvspatial.core import Node
from typing import List, Dict, Any
from jvspatial.core.annotations import attribute

class RankProfile(Node):
    """Rank profile node."""
    
    ident_code: str = attribute(
        default_factory=str,
        description="Ident code of the rank",
    )
    regimental_number: str = attribute(
        default_factory=str,
        description="Regimental number of the rank",
    )
    unit: Dict[str, Any] = attribute(
        default_factory=dict,
        description="Unit of the rank",
    )
    sub_unit: Dict[str, Any] = attribute(
        default_factory=dict,
        description="Sub unit of the rank",
    )
    supervisor: Dict[str, Any] = attribute(
        default_factory=dict,
        description="Supervisor of the rank",
    )
    first_name: str = attribute(
        default_factory=str,
        description="First name of the rank",
    )
    last_name: str = attribute(
        default_factory=str,
        description="Last name of the rank",
    )
    is_first_time: bool = attribute(
        default_factory=bool,
        description="Whether the rank is a first time user",
    )
    is_security_question_set: bool = attribute(
        default_factory=bool,
        description="Whether the rank has set a security question",
    )
    is_pin_set: bool = attribute(
        default_factory=bool,
        description="Whether the rank has set a PIN",
    )
    rank: Dict[str, Any] = attribute(
        default_factory=dict,
        description="Rank of the rank",
    )