"""Redesigned LeadProfile node for fast, structured lead storage.

Lead profiles are stored as a flat YAML frontmatter on the LeadProfile anchor node
plus optional markdown section nodes for rich narrative content.

The redesign focuses on:
  - Speed: no graph traversal for field lookups (all in YAML)
  - Active gap-filling: required_fields list drives conversational prompting
  - Incremental extraction: fast LLM calls extract only new fields

Graph structure:
    User --> LeadProfile (YAML frontmatter + required_fields + missing_fields)
                  --> LeadProfileNode (conversation_summaries)
                  --> LeadProfileNode (notes)
                  --> LeadProfileNode (<custom>)
"""

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core import Node
from jvspatial.core.annotations import attribute, compound_index

if TYPE_CHECKING:
    from jvagent.memory.user import User

logger = logging.getLogger(__name__)

# Default required fields for a qualified lead
DEFAULT_REQUIRED_FIELDS: List[str] = [
    "name",
    "organization",
    "email",
    "phone",
    "interested_products",
]

# Extended optional fields that enrich the profile
OPTIONAL_FIELDS: List[str] = [
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
    "past_interests",
    "requested_items",
    "feedback",
]

# All known fields (required + optional)
ALL_FIELDS: List[str] = DEFAULT_REQUIRED_FIELDS + OPTIONAL_FIELDS

# Human-readable labels for all fields (used in gap-filling prompts)
FIELD_LABELS: Dict[str, str] = {
    "name": "Full Name",
    "organization": "Organization / Company",
    "project_description": "Project Description",
    "project_location": "Project Location",
    "email": "Email Address",
    "phone": "Phone Number",
    "interested_products": "Products & Services Interested In",
    "role": "Role / Title",
    "industry": "Industry",
    "team_size": "Team Size",
    "budget": "Budget",
    "timeline": "Timeline",
    "pain_points": "Pain Points",
    "current_tools": "Current Tools / Systems",
    "communication_preference": "Communication Preference",
    "past_interests": "Past Interests",
    "requested_items": "Requested Items",
    "feedback": "Feedback",
}


@compound_index(
    [("user_node_id", 1), ("category", 1)],
    name="user_node_category",
    unique=True,
    partial_filter_expression={
        "context.user_node_id": {"$gt": ""},
        "context.category": {"$gt": ""},
    },
)
class LeadProfileNode(Node):
    """A narrative section node (conversation summaries, notes, etc.)."""

    user_node_id: str = attribute(
        indexed=True,
        default="",
        description="Graph id of the owning User node",
    )
    user_id: str = attribute(
        indexed=True,
        default="",
        description="Owner's user_id",
    )
    category: str = attribute(
        indexed=True,
        default="",
        description="Section key (e.g., 'conversation_summaries')",
    )
    title: str = attribute(
        default="",
        description="Human-readable title",
    )
    content: str = attribute(
        default="",
        description="Markdown-formatted content",
    )
    updated_at: Optional[datetime] = attribute(
        default=None,
        description="Timestamp of last content update",
    )
    created_at: datetime = attribute(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of node creation",
    )

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


class LeadProfile(Node):
    """Fast lead-profile anchor: all structured data lives in YAML frontmatter.

    The graph structure is intentionally shallow:
        User --> LeadProfile (YAML + required_fields + missing_fields)
                      --> LeadProfileNode (conversation_summaries)
                      --> LeadProfileNode (notes)

    Key design decisions:
      1. Field lookups are O(1) dict reads — no graph traversal needed.
      2. required_fields is configurable per agent; missing_fields is computed
         by comparing required_fields against the current YAML data.
      3. Background extraction writes flat key-value pairs directly into YAML.
      4. Conversation summaries and freeform notes live in LeadProfileNode
         children so they can grow without bloating the anchor.
    """

    user_node_id: str = attribute(
        indexed=True,
        index_unique=True,
        index_partial_filter_expression={"context.user_node_id": {"$gt": ""}},
        default="",
        description="Graph id of the owning User node",
    )
    user_id: str = attribute(
        indexed=True,
        default="",
        description="Owner's user_id",
    )
    status: str = attribute(
        default="active",
        description="Lead status: active, qualified, disqualified, nurturing, closed_won, closed_lost",
    )
    enrichment_status: str = attribute(
        default="none",
        description="Enrichment status: none, partial, complete",
    )

    # --- The flat data store ---
    yaml_frontmatter: str = attribute(
        default="{}",
        description="JSON-serialized dict of all known lead fields",
    )

    # --- Gap-filling configuration ---
    required_fields: str = attribute(
        default="",
        description="Comma-separated list of fields this agent must collect",
    )

    # --- Derived (updated after every extraction) ---
    missing_fields: str = attribute(
        default="",
        description="Comma-separated list of required fields not yet known",
    )

    score: Optional[int] = attribute(
        default=None,
        description="Lead score (0-100) derived from completeness",
    )

    last_updated: Optional[datetime] = attribute(
        default=None,
        description="Timestamp of last update",
    )
    created_at: datetime = attribute(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of node creation",
    )
    pageindex_digest: Optional[str] = attribute(
        default=None,
        description="SHA-256 hex digest of markdown last indexed to PageIndex",
    )
    last_sheet_sync_digest: Optional[str] = attribute(
        default=None,
        description="SHA-256 hex digest of the last row synced to the Google Sheet",
    )

    # -------------------------------------------------------------------------
    # YAML helpers (fast, O(1))
    # -------------------------------------------------------------------------

    def get_yaml(self) -> Dict[str, Any]:
        """Parse yaml_frontmatter into a dict."""
        import json

        try:
            return json.loads(self.yaml_frontmatter or "{}")
        except json.JSONDecodeError:
            return {}

    async def set_yaml(self, data: Dict[str, Any]) -> None:
        """Serialize a dict into yaml_frontmatter and save."""
        import json

        self.yaml_frontmatter = json.dumps(data, indent=2, default=str)
        self.last_updated = datetime.now(timezone.utc)
        await self.save()

    def _get_list_field(self, attr_name: str) -> List[str]:
        """Parse a comma-separated string attribute into a clean list."""
        raw = getattr(self, attr_name, "") or ""
        if not raw:
            return []
        return [s.strip() for s in raw.split(",") if s.strip()]

    def get_required_fields(self) -> List[str]:
        """Return the list of required fields this agent must collect."""
        fields = self._get_list_field("required_fields")
        if not fields:
            # Fallback to sensible defaults
            return list(DEFAULT_REQUIRED_FIELDS)
        return fields

    def get_missing_fields(self) -> List[str]:
        """Return required fields that are still missing from the YAML data."""
        required = self.get_required_fields()
        known = set(self.get_yaml().keys())
        return [f for f in required if f not in known]

    def get_field_label(self, field: str) -> str:
        """Human-readable label for a field (for gap-filling prompts)."""
        return FIELD_LABELS.get(field, field.replace("_", " ").title())

    async def update_yaml(self, updates: Dict[str, Any]) -> bool:
        """Merge flat key-value updates into YAML frontmatter.

        Automatically recalculates missing_fields and score after update.

        Returns:
            True if any field was changed.
        """
        current = self.get_yaml()
        changed = False
        for key, value in updates.items():
            if key.startswith("_"):
                continue
            if current.get(key) != value:
                current[key] = value
                changed = True
        if changed:
            await self.set_yaml(current)
            # Recalculate derived state
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

    # -------------------------------------------------------------------------
    # Markdown rendering (for directive injection)
    # -------------------------------------------------------------------------

    async def as_markdown(self, include_empty: bool = False) -> str:
        """Render the profile as a compact markdown block.

        Output format:
            ---
            name: Jane Doe
            organization: TechCorp
            ...
            ---

            ## Conversation Summaries
            - 2026-01-15: First contact via web widget.

            ## Notes
            - Prefers email over phone.
        """
        import json

        yaml_data = self.get_yaml()
        yaml_data["status"] = self.status
        yaml_data["enrichment_status"] = self.enrichment_status

        # Compute derived values dynamically (works even if score not set)
        missing = self.get_missing_fields()
        yaml_data["missing_fields"] = missing
        total = len(self.get_required_fields())
        score = int(((total - len(missing)) / total) * 100) if total else 0
        yaml_data["score"] = score

        lines: List[str] = ["---"]
        for key, value in sorted(yaml_data.items()):
            if isinstance(value, list):
                val_str = json.dumps(value)
            elif isinstance(value, bool):
                val_str = "true" if value else "false"
            elif isinstance(value, (int, float)):
                val_str = str(value)
            else:
                val_str = str(value) if value is not None else ""
            if val_str:
                lines.append(f"{key}: {val_str}")
        lines.append("---")
        lines.append("")

        # Append narrative sections
        for node in await self.get_all_sections():
            if not include_empty and node.is_empty():
                continue
            lines.append(f"## {node.title}")
            content = node.content.strip()
            if node.category in ("conversation_log", "conversation_summaries"):
                content_lines = [log.strip() for log in content.splitlines() if log.strip()]
                if len(content_lines) > 25:
                    content = "... (older entries truncated) ...\n" + "\n".join(
                        content_lines[-25:]
                    )
            lines.append(content)
            lines.append("")

        return "\n".join(lines).strip()

    # -------------------------------------------------------------------------
    # Section node access (for conversation summaries, notes)
    # -------------------------------------------------------------------------

    async def get_section(self, category: str) -> Optional[LeadProfileNode]:
        if self.user_node_id:
            return await LeadProfileNode.find_one(
                user_node_id=self.user_node_id,
                category=category,
            )
        return await LeadProfileNode.find_one(
            user_id=self.user_id,
            category=category,
        )

    async def get_or_create_section(
        self, category: str, title: Optional[str] = None
    ) -> LeadProfileNode:
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
        node = await LeadProfileNode.create(
            user_node_id=self.user_node_id or "",
            user_id=self.user_id,
            category=category,
            title=resolved_title,
            content="",
            created_at=datetime.now(timezone.utc),
        )
        await self.connect(node)
        return node

    async def get_all_sections(self) -> List[LeadProfileNode]:
        return await self.nodes(node=LeadProfileNode, direction="out")

    async def append_to_section(self, category: str, text: str) -> bool:
        """Append text to a narrative section (conversation_summaries, notes, etc.)."""
        node = await self.get_or_create_section(category)
        existing = node.content or ""
        separator = "\n\n" if existing else ""
        return await node.update_content(existing + separator + text.strip())

    async def log_conversation(self, entry: str) -> bool:
        """Append a dated entry to the conversation_log section."""
        from datetime import timedelta

        # Use UTC-4 (Guyana Time) for all timestamps
        tz_guyana = timezone(timedelta(hours=-4))
        now = datetime.now(tz_guyana).strftime("%Y-%m-%d %H:%M")
        formatted = f"[{now}] {entry.strip()}"
        return await self.append_to_section("conversation_log", formatted)

    @staticmethod
    def _deduplicate_items(raw: str) -> str:
        """Deduplicate a comma-separated items string.

        Normalises case so 'Pink Fly Knit Steel Toe' and
        'Pink Fly Knit Steel Toe shoe' are treated as the same item.
        When duplicates are found the longest (most descriptive) version wins.
        Returns a cleaned, comma-separated string.
        """
        if not raw or not raw.strip():
            return raw

        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if len(parts) <= 1:
            return raw

        # Build (lower, original) pairs and deduplicate: keep the longest
        # entry whose lowercase form contains another as a substring.
        kept: List[str] = []
        for candidate in parts:
            cl = candidate.lower()
            absorbed = False
            for i, existing in enumerate(kept):
                el = existing.lower()
                if el == cl:
                    # Exact duplicate – keep whichever is longer/more specific
                    if len(candidate) > len(existing):
                        kept[i] = candidate
                    absorbed = True
                    break
                # Subset: if one string fully contains the other, keep longer
                if el in cl:
                    # candidate is more specific; replace existing
                    kept[i] = candidate
                    absorbed = True
                    break
                if cl in el:
                    # existing is already more specific; drop candidate
                    absorbed = True
                    break
            if not absorbed:
                kept.append(candidate)

        return ", ".join(kept)

    async def generate_and_save_session_summary(self, conversation_id: str) -> None:
        """Generate a single living session summary that is updated on each call.

        Unlike the previous multi-session approach, this maintains ONE summary
        that reflects the current state of the lead profile. Old summaries are
        not preserved — only the latest snapshot is kept.

        Summary rules:
        - When specific requested_items exist, show ONLY those (not interested_products).
        - interested_products is shown only when no requested_items exist.
        - requested_items is deduplicated/consolidated before rendering.
        """
        try:
            node = await self.get_section("conversation_summaries")
            if not node or node.is_empty():
                return
            content = node.content or ""
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            if not lines:
                return

            profile_data = self.get_yaml() or {}

            name = profile_data.get("name", "")
            org = profile_data.get("organization", "")
            project = (
                profile_data.get("project_description")
                or profile_data.get("project_name")
                or ""
            ).strip()
            location = profile_data.get("project_location", "")
            interested = profile_data.get("interested_products", "")
            requested_raw = profile_data.get("requested_items", "")
            feedback = profile_data.get("feedback", "")
            phone = profile_data.get("phone", "")
            email = profile_data.get("email", "")

            # Deduplicate the requested_items list so identical/subsumed entries collapse
            requested = (
                self._deduplicate_items(str(requested_raw).strip())
                if requested_raw
                else ""
            )

            # Build identity tokens (name, org, project, location) — joined with spaces
            identity_parts: List[str] = []
            if name:
                identity_parts.append(name)
            if org:
                identity_parts.append(f"from {org}")
            if project:
                identity_parts.append(f"working on {project}")
            if location:
                identity_parts.append(f"in {location}")

            actions: List[str] = []
            # Prefer requested_items; only fall back to interested_products when absent
            if requested:
                actions.append(f"requested {requested}")
            elif interested:
                actions.append(
                    f"interested in {self._deduplicate_items(str(interested).strip())}"
                )
            if feedback:
                actions.append(f"gave feedback: {feedback}")

            contact_info: List[str] = []
            if phone:
                contact_info.append(f"phone {phone}")
            if email:
                contact_info.append(f"email {email}")

            summary = " ".join(identity_parts)
            if actions:
                summary += " — " + "; ".join(actions)
            if contact_info:
                summary += " | Contact: " + " / ".join(contact_info)

            current_session_summary = summary.strip()
            if not current_session_summary:
                current_session_summary = "Lead engaged; no key details captured yet."

            # Use UTC-4 (Guyana Time) for all timestamps
            from datetime import timedelta

            tz_guyana = timezone(timedelta(hours=-4))
            now_str = datetime.now(tz_guyana).strftime("%Y-%m-%d %H:%M")

            # Store as a single living summary (no history array)
            profile_data["_session_summary"] = current_session_summary
            profile_data["_session_summary_updated_at"] = now_str
            await self.set_yaml(profile_data)

            logger.info(
                "LeadProfile: updated session summary for user %s: %s",
                self.user_id,
                current_session_summary[:120],
            )
        except Exception as exc:
            logger.warning(
                "LeadProfile: generate_and_save_session_summary failed: %s",
                exc,
                exc_info=True,
            )

    # -------------------------------------------------------------------------
    # Factory / retrieval
    # -------------------------------------------------------------------------

    @classmethod
    async def get_for_user(cls, user: "User") -> Optional["LeadProfile"]:
        return await user.node(node=LeadProfile, direction="out")

    @classmethod
    async def get_or_create_for_user(
        cls, user: "User", required_fields: Optional[List[str]] = None
    ) -> "LeadProfile":
        existing = await cls.get_for_user(user)
        field_list = required_fields or DEFAULT_REQUIRED_FIELDS
        fields_csv = ", ".join(field_list)

        if existing:
            if not existing.user_node_id:
                existing.user_node_id = user.id
                await existing.save()
            # Sync required_fields if the agent configuration changed
            current = existing._get_list_field("required_fields")
            if required_fields and current != required_fields:
                existing.required_fields = fields_csv
                # Recalculate missing_fields and score
                missing = existing.get_missing_fields()
                existing.missing_fields = ", ".join(missing)
                total = len(field_list)
                existing.score = (
                    int(((total - len(missing)) / total) * 100) if total else 0
                )
                await existing.save()
                logger.info(
                    "LeadProfile: Updated required_fields for user %s to %s",
                    user.user_id,
                    fields_csv,
                )
            return existing

        # Guard against duplicate creation under JSONDB (no native unique index)
        current = await cls.find_one(user_node_id=user.id)
        if current:
            logger.info(
                "LeadProfile: Race-skip creation for user %s (existing %s)",
                user.user_id,
                current.id,
            )
            return current

        lp = await cls.create(
            user_node_id=user.id,
            user_id=user.user_id,
            required_fields=fields_csv,
            missing_fields=fields_csv,
            created_at=datetime.now(timezone.utc),
        )
        try:
            await user.connect(lp)
        except Exception as exc:
            # Edge already existed (another request won) → fetch existing
            logger.warning(
                "LeadProfile: Connect race for user %s (%s). Fetching existing.",
                user.user_id,
                exc,
            )
            existing = await cls.find_one(user_node_id=user.id)
            if existing:
                return existing
        return lp
