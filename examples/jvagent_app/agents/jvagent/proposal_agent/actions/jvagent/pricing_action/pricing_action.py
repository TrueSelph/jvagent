"""PricingAction — manages pricing rubrics as graph nodes with CRUD + assessment.

Rubrics define rate cards, effort templates, value-based adjustment factors,
and markup chains. The `assess()` method applies a rubric to extracted scope
parameters to produce a PricingAssessment (line items, totals, narrative).
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from jvspatial.core import Node
from jvspatial.core.annotations import attribute

from jvagent.action.base import Action

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Data Models
# ──────────────────────────────────────────────


class EffortTemplate:
    """Template for a pricing line-item activity."""

    activity: str
    typical_hours: Optional[float] = None
    senior_pct: float = 0.2
    multiplier: Optional[float] = None

    def __init__(
        self,
        activity: str,
        typical_hours: Optional[float] = None,
        senior_pct: float = 0.2,
        multiplier: Optional[float] = None,
    ):
        self.activity = activity
        self.typical_hours = typical_hours
        self.senior_pct = senior_pct
        self.multiplier = multiplier


class PriceLineItem:
    """A single line item in a pricing assessment."""

    activity: str
    hours: float
    rate: float
    total: float
    notes: str = ""

    def __init__(
        self,
        activity: str,
        hours: float,
        rate: float,
        total: float,
        notes: str = "",
    ):
        self.activity = activity
        self.hours = hours
        self.rate = rate
        self.total = total
        self.notes = notes

    def to_dict(self) -> Dict[str, Any]:
        return {
            "activity": self.activity,
            "hours": self.hours,
            "rate": self.rate,
            "total": self.total,
            "notes": self.notes,
        }


class PricingAssessment:
    """The result of applying a pricing rubric to scope parameters."""

    total_engineering_hours: float
    blended_rate: float
    line_items: List[PriceLineItem]
    total: float
    narrative: str
    assumptions: List[str]
    valid_until: str

    def __init__(
        self,
        total_engineering_hours: float = 0.0,
        blended_rate: float = 0.0,
        line_items: Optional[List[PriceLineItem]] = None,
        total: float = 0.0,
        narrative: str = "",
        assumptions: Optional[List[str]] = None,
        valid_until: str = "",
    ):
        self.total_engineering_hours = total_engineering_hours
        self.blended_rate = blended_rate
        self.line_items = line_items or []
        self.total = total
        self.narrative = narrative
        self.assumptions = assumptions or []
        self.valid_until = valid_until

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_engineering_hours": self.total_engineering_hours,
            "blended_rate": self.blended_rate,
            "line_items": [li.to_dict() for li in self.line_items],
            "total": round(self.total, 2),
            "narrative": self.narrative,
            "assumptions": self.assumptions,
            "valid_until": self.valid_until,
        }


# ──────────────────────────────────────────────
# PricingRubricNode (Graph Node)
# ──────────────────────────────────────────────


class PricingRubricNode(Node):
    """A pricing rubric stored as a graph node, CRUD-managed by PricingAction."""

    name: str = attribute(
        indexed=True,
        default="standard",
        description="Rubric identifier used to look up from skills/tools",
    )
    version: str = attribute(default="1.0", description="Semantic version of this rubric")
    description: str = attribute(default="Standard rate card", description="Human-readable description")

    base_rates: Dict[str, float] = attribute(
        default_factory=lambda: {
            "senior_engineer": 250,
            "engineer": 175,
            "junior": 95,
            "pm": 200,
            "designer": 225,
            "strategist": 300,
        },
        description="Rate card: role -> hourly rate in USD",
    )

    effort_templates: List[Dict[str, Any]] = attribute(
        default_factory=list,
        description="List of effort estimation templates",
    )

    value_based_factors: Dict[str, float] = attribute(
        default_factory=lambda: {
            "strategic_value_multiplier": 1.0,
            "competitive_pressure": 0.0,
            "relationship_discount": 0.0,
        },
        description="Value-based pricing adjustment factors",
    )

    markup: Dict[str, float] = attribute(
        default_factory=lambda: {
            "overhead": 1.15,
            "margin": 1.25,
            "contingency": 1.10,
        },
        description="Markup chain: overhead, margin, contingency",
    )

    tags: List[str] = attribute(
        default_factory=list,
        description="Tags for categorization (e.g., retail, startup, healthcare)",
    )

    active: bool = attribute(default=True, description="Whether this rubric is active")

    async def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id) if self.id else None,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "base_rates": self.base_rates,
            "effort_templates": self.effort_templates,
            "value_based_factors": self.value_based_factors,
            "markup": self.markup,
            "tags": self.tags,
            "active": self.active,
        }


# ──────────────────────────────────────────────
# PricingAction
# ──────────────────────────────────────────────


DEFAULT_EFFORT_TEMPLATES: List[Dict[str, Any]] = [
    {"activity": "Discovery & Requirements", "typical_hours": 40, "senior_pct": 0.3},
    {"activity": "Architecture & Design", "typical_hours": 60, "senior_pct": 0.5},
    {"activity": "Development", "typical_hours": None, "senior_pct": 0.2},
    {"activity": "Testing & QA", "typical_hours": None, "senior_pct": 0.1},
    {
        "activity": "Project Management",
        "typical_hours": None,
        "senior_pct": 0.0,
        "multiplier": 0.15,
    },
]


class PricingAction(Action):
    """Manages pricing rubrics as graph nodes with CRUD + assessment.

    Provides:
    - CRUD operations for PricingRubricNode instances
    - The assess() method that applies a rubric to scope parameters
    - A seed default rubric created on first registration
    - REST API endpoints for runtime configuration
    """

    rubric_namespace: str = attribute(
        default="proposals",
        description="Graph namespace under which rubric nodes are stored",
    )
    active_rubric: str = attribute(
        default="standard",
        description="Name of the currently active rubric",
    )

    # ── Lifecycle ──────────────────────────────────────

    async def on_register(self) -> None:
        """Seed a default rubric if none exist."""
        existing = await self._find_rubric("standard")
        if existing:
            return
        await self._create_rubric_node(
            name="standard",
            version="1.0",
            description="Default rate card for technology services",
            base_rates={
                "senior_engineer": 250,
                "engineer": 175,
                "junior": 95,
                "pm": 200,
                "designer": 225,
                "strategist": 300,
            },
            effort_templates=DEFAULT_EFFORT_TEMPLATES,
            value_based_factors={
                "strategic_value_multiplier": 1.0,
                "competitive_pressure": 0.0,
                "relationship_discount": 0.0,
            },
            markup={"overhead": 1.15, "margin": 1.25, "contingency": 1.10},
            tags=["default"],
            active=True,
        )

    # ── Core CRUD ─────────────────────────────────────

    async def get_rubric(self, name: str) -> Optional[PricingRubricNode]:
        """Resolve a rubric by name from the graph."""
        return await self._find_rubric(name)

    async def list_rubrics(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """List all rubrics (or only active ones) as dicts."""
        rubric_nodes = await PricingRubricNode.find(
            condition={"active": True} if active_only else {}
        )
        results = []
        for node in rubric_nodes:
            d = await node.to_dict()
            results.append(d)
        return results

    async def create_rubric(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new rubric from validated data."""
        existing = await self._find_rubric(data.get("name", ""))
        if existing:
            raise ValueError(f"Rubric '{data.get('name')}' already exists")
        node = await self._create_rubric_node(
            name=data.get("name", "unnamed"),
            version=data.get("version", "1.0"),
            description=data.get("description", ""),
            base_rates=data.get("base_rates", {}),
            effort_templates=data.get("effort_templates", []),
            value_based_factors=data.get("value_based_factors", {}),
            markup=data.get("markup", {}),
            tags=data.get("tags", []),
            active=data.get("active", True),
        )
        return await node.to_dict()

    async def update_rubric(
        self, name: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update an existing rubric (partial merge)."""
        node = await self._find_rubric(name)
        if not node:
            return None

        # Partial update: only set provided fields
        for field in (
            "version",
            "description",
            "base_rates",
            "effort_templates",
            "value_based_factors",
            "markup",
            "tags",
            "active",
        ):
            if field in data:
                setattr(node, field, data[field])

        await node.save()
        return await node.to_dict()

    async def delete_rubric(self, name: str) -> bool:
        """Soft-delete a rubric by marking it inactive."""
        node = await self._find_rubric(name)
        if not node:
            return False
        node.active = False
        await node.save()
        return True

    # ── Assessment ────────────────────────────────────

    async def assess(
        self,
        rubric_name: str,
        scope_params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Apply a rubric to scope parameters and compute a PricingAssessment.

        ``scope_params`` should contain:
            - estimated_engineering_hours (float)
            - team_composition (dict, optional): senior/engineer/junior counts
            - timeline_months (float, optional)
            - strategic_value (str, optional): "low"|"medium"|"high"
            - competitive_pressure (float, optional): 0.0-0.5
            - relationship_stage (str, optional): "new"|"existing"|"strategic"
        """
        rubric = await self._find_rubric(rubric_name)
        if not rubric:
            raise ValueError(f"Rubric '{rubric_name}' not found")

        params = scope_params or {}
        total_hours = float(params.get("estimated_engineering_hours", 0))
        templates = rubric.effort_templates or DEFAULT_EFFORT_TEMPLATES

        line_items: List[PriceLineItem] = []
        total_from_items = 0.0

        for tmpl in templates:
            activity = tmpl.get("activity", "Unnamed")
            typical = tmpl.get("typical_hours")
            senior_pct = float(tmpl.get("senior_pct", 0.2))
            multiplier = tmpl.get("multiplier")

            if typical is not None:
                hours = float(typical)
            elif multiplier is not None:
                # Multiplier-based: percentage of subtotal (applied after other items)
                continue
            else:
                # Proportional split of estimated hours among non-fixed, non-multiplier items
                variable_templates = [
                    t
                    for t in templates
                    if t.get("typical_hours") is None and t.get("multiplier") is None
                ]
                var_count = max(len(variable_templates), 1)
                hours = total_hours / var_count if total_hours > 0 else 0

            # Compute blended rate for this line
            senior_rate = float(
                rubric.base_rates.get("senior_engineer", 250)
            )
            engineer_rate = float(rubric.base_rates.get("engineer", 175))
            blended_rate = (senior_rate * senior_pct) + (
                engineer_rate * (1 - senior_pct)
            )

            total_item = round(hours * blended_rate, 2)
            total_from_items += total_item

            line_items.append(
                PriceLineItem(
                    activity=activity,
                    hours=round(hours, 1),
                    rate=round(blended_rate, 2),
                    total=total_item,
                )
            )

        # Apply multiplier-based items
        for tmpl in templates:
            multiplier = tmpl.get("multiplier")
            if multiplier is not None:
                activity = tmpl.get("activity", "Unnamed")
                mult_total = round(total_from_items * float(multiplier), 2)
                total_from_items += mult_total
                line_items.append(
                    PriceLineItem(
                        activity=activity,
                        hours=0.0,
                        rate=0.0,
                        total=mult_total,
                        notes=f"{float(multiplier)*100:.0f}% of engineering subtotal",
                    )
                )

        # Apply markup chain
        subtotal = total_from_items
        contingency = rubric.markup.get("contingency", 1.10)
        overhead = rubric.markup.get("overhead", 1.15)
        margin = rubric.markup.get("margin", 1.25)

        with_contingency = subtotal * float(contingency)
        with_overhead = with_contingency * float(overhead)
        total = with_overhead * float(margin)

        # Apply value-based adjustments
        strategic_map = {"low": 0.0, "medium": 0.1, "high": 0.25}
        strategic_premium = strategic_map.get(
            params.get("strategic_value", "medium"), 0.1
        )
        competitive_discount = float(params.get("competitive_pressure", 0.0))
        relationship_discount = float(params.get("relationship_discount", 0.0))

        total *= 1.0 + strategic_premium
        total *= 1.0 - competitive_discount
        total *= 1.0 - relationship_discount
        total = round(max(total, 0), 2)

        # Blended rate
        blended_rate = round(total / total_hours, 2) if total_hours > 0 else 0.0

        # Assumptions
        assumptions = [
            f"Based on {total_hours} estimated engineering hours",
            f"Team blended rate: ${blended_rate}/hr",
            f"Timeline: {params.get('timeline_months', 'TBD')} months",
            "Valid for 30 days from date of assessment",
        ]
        if competitive_discount > 0:
            assumptions.append(
                f"Competitive adjustment applied: -{competitive_discount*100:.0f}%"
            )

        valid_until = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

        assessment = PricingAssessment(
            total_engineering_hours=total_hours,
            blended_rate=blended_rate,
            line_items=line_items,
            total=total,
            narrative="",  # filled by LLM in the skill layer
            assumptions=assumptions,
            valid_until=valid_until,
        )

        return assessment.to_dict()

    # ── Internal Helpers ──────────────────────────────

    async def _find_rubric(self, name: str) -> Optional[PricingRubricNode]:
        """Find a rubric node by name (only active ones)."""
        results = await PricingRubricNode.find(
            condition={"name": name, "active": True}
        )
        if results:
            return results[0]
        # Fallback: try without active filter
        results = await PricingRubricNode.find(
            condition={"name": name}
        )
        return results[0] if results else None

    async def _create_rubric_node(
        self,
        name: str,
        version: str = "1.0",
        description: str = "",
        base_rates: Optional[Dict[str, float]] = None,
        effort_templates: Optional[List[Dict[str, Any]]] = None,
        value_based_factors: Optional[Dict[str, float]] = None,
        markup: Optional[Dict[str, float]] = None,
        tags: Optional[List[str]] = None,
        active: bool = True,
    ) -> PricingRubricNode:
        """Create and persist a PricingRubricNode."""
        node = PricingRubricNode(
            name=name,
            version=version,
            description=description,
            base_rates=base_rates or {},
            effort_templates=effort_templates or [],
            value_based_factors=value_based_factors or {},
            markup=markup or {},
            tags=tags or [],
            active=active,
        )
        await node.save()
        return node
