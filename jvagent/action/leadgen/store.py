"""LeadRecord persistence — flat YAML on anchor node + narrative section children."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, ClassVar, Dict, List, Optional

from jvspatial.core import Node
from jvspatial.core.annotations import attribute, compound_index

if TYPE_CHECKING:
    from jvagent.memory.user import User

logger = logging.getLogger(__name__)

DEFAULT_REQUIRED_FIELDS: List[str] = [
    "name",
    "organization",
    "email",
    "phone",
]

DEFAULT_OPTIONAL_FIELDS: List[str] = [
    "project_description",
    "project_location",
    "role",
    "industry",
    "team_size",
    "budget",
    "timeline",
    "pain_points",
    "current_tools",
    "communication_preference",
    "interested_products",
    "requested_items",
    "feedback",
]


@compound_index(
    [("user_node_id", 1), ("category", 1)],
    name="user_node_category",
    unique=True,
    partial_filter_expression={
        "context.user_node_id": {"$gt": ""},
        "context.category": {"$gt": ""},
    },
)
class LeadRecordNode(Node):
    """Narrative section (conversation summaries, notes, etc.)."""

    __entity_name__: ClassVar[Optional[str]] = "LeadProfileNode"

    user_node_id: str = attribute(indexed=True, default="")
    user_id: str = attribute(indexed=True, default="")
    category: str = attribute(indexed=True, default="")
    title: str = attribute(default="")
    content: str = attribute(default="")
    updated_at: Optional[datetime] = attribute(default=None)
    created_at: datetime = attribute(default_factory=lambda: datetime.now(timezone.utc))

    def is_empty(self) -> bool:
        return not self.content or not self.content.strip()

    async def update_content(self, new_content: str) -> bool:
        new_content = new_content.strip()
        if new_content == (self.content or "").strip():
            return False
        self.content = new_content
        self.updated_at = datetime.now(timezone.utc)
        await self.save()
        return True


class LeadRecord(Node):
    """Lead anchor: structured fields in yaml_frontmatter, narrative in child nodes."""

    __entity_name__: ClassVar[Optional[str]] = "LeadProfile"

    user_node_id: str = attribute(
        indexed=True,
        index_unique=True,
        index_partial_filter_expression={"context.user_node_id": {"$gt": ""}},
        default="",
    )
    user_id: str = attribute(indexed=True, default="")
    status: str = attribute(default="active")
    enrichment_status: str = attribute(default="none")
    yaml_frontmatter: str = attribute(default="{}")
    required_fields: str = attribute(default="")
    missing_fields: str = attribute(default="")
    score: Optional[int] = attribute(default=None)
    last_updated: Optional[datetime] = attribute(default=None)
    created_at: datetime = attribute(default_factory=lambda: datetime.now(timezone.utc))

    def get_yaml(self) -> Dict[str, Any]:
        try:
            return json.loads(self.yaml_frontmatter or "{}")
        except json.JSONDecodeError:
            return {}

    async def set_yaml(self, data: Dict[str, Any]) -> None:
        self.yaml_frontmatter = json.dumps(data, indent=2, default=str)
        self.last_updated = datetime.now(timezone.utc)
        await self.save()

    def _get_list_field(self, attr_name: str) -> List[str]:
        raw = getattr(self, attr_name, "") or ""
        if not raw:
            return []
        return [s.strip() for s in raw.split(",") if s.strip()]

    def get_required_fields(self) -> List[str]:
        fields = self._get_list_field("required_fields")
        return fields or list(DEFAULT_REQUIRED_FIELDS)

    def get_missing_fields(self) -> List[str]:
        required = self.get_required_fields()
        data = self.get_yaml()
        missing: List[str] = []
        for field in required:
            if field not in data:
                missing.append(field)
                continue
            val = data.get(field)
            if val is None or (isinstance(val, str) and not str(val).strip()):
                missing.append(field)
        return missing

    async def update_yaml(self, updates: Dict[str, Any]) -> bool:
        current = self.get_yaml()
        changed = False
        for key, value in updates.items():
            if key.startswith("_"):
                current[key] = value
                changed = True
                continue
            if current.get(key) != value:
                current[key] = value
                changed = True
        if changed:
            await self.set_yaml(current)
            missing = self.get_missing_fields()
            self.missing_fields = ", ".join(missing)
            total = len(self.get_required_fields())
            self.score = int(((total - len(missing)) / total) * 100) if total else 0
            if not missing:
                self.enrichment_status = "complete"
            elif len(missing) < total:
                self.enrichment_status = "partial"
            else:
                self.enrichment_status = "none"
            await self.save()
        return changed

    async def get_section(self, category: str) -> Optional[LeadRecordNode]:
        if self.user_node_id:
            return await LeadRecordNode.find_one(
                user_node_id=self.user_node_id, category=category
            )
        return await LeadRecordNode.find_one(user_id=self.user_id, category=category)

    async def get_or_create_section(
        self, category: str, title: Optional[str] = None
    ) -> LeadRecordNode:
        if not self.user_node_id:
            from jvagent.memory.user import User as UserNode

            owner = await self.node(direction="in", node=UserNode)
            if owner:
                self.user_node_id = owner.id
                await self.save()

        existing = await self.get_section(category)
        if existing:
            return existing

        resolved_title = title or category.replace("_", " ").title()
        node = await LeadRecordNode.create(
            user_node_id=self.user_node_id or "",
            user_id=self.user_id,
            category=category,
            title=resolved_title,
            content="",
            created_at=datetime.now(timezone.utc),
        )
        await self.connect(node)
        return node

    async def append_to_section(self, category: str, text: str) -> bool:
        node = await self.get_or_create_section(category)
        existing = node.content or ""
        separator = "\n\n" if existing else ""
        return await node.update_content(existing + separator + text.strip())

    async def log_conversation(self, entry: str) -> bool:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        return await self.append_to_section(
            "conversation_log", f"[{now}] {entry.strip()}"
        )

    @classmethod
    async def get_for_user(cls, user: "User") -> Optional["LeadRecord"]:
        return await user.node(node=LeadRecord, direction="out")

    @classmethod
    async def get_or_create_for_user(
        cls, user: "User", required_fields: Optional[List[str]] = None
    ) -> "LeadRecord":
        existing = await cls.get_for_user(user)
        field_list = required_fields or list(DEFAULT_REQUIRED_FIELDS)
        fields_csv = ", ".join(field_list)

        if existing:
            if not existing.user_node_id:
                existing.user_node_id = user.id
                await existing.save()
            current = existing._get_list_field("required_fields")
            if required_fields and current != field_list:
                existing.required_fields = fields_csv
                missing = existing.get_missing_fields()
                existing.missing_fields = ", ".join(missing)
                total = len(field_list)
                existing.score = (
                    int(((total - len(missing)) / total) * 100) if total else 0
                )
                await existing.save()
            return existing

        race = await cls.find_one(user_node_id=user.id)
        if race:
            return race

        record = await cls.create(
            user_node_id=user.id,
            user_id=user.user_id,
            required_fields=fields_csv,
            missing_fields=fields_csv,
            created_at=datetime.now(timezone.utc),
        )
        try:
            await user.connect(record)
        except Exception as exc:
            logger.warning("LeadRecord connect race for %s: %s", user.user_id, exc)
            found = await cls.find_one(user_node_id=user.id)
            if found:
                return found
        return record


# Backward-compatible aliases for existing graph rows
LeadProfile = LeadRecord
LeadProfileNode = LeadRecordNode
