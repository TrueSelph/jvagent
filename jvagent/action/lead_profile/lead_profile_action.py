"""Lead Profile Action.

Exposes lead-profile management as first-class orchestrator tools so the
orchestrator can decide *when* to update or sync, rather than blindly
running a background action every turn.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.memory.user import User
from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_executor import get_dispatch_visitor

logger = logging.getLogger(__name__)

# Deduplication guard: prevents duplicate tool calls within the same turn
_LAST_TOOL_CALLS: Dict[Tuple[str, str], float] = {}
_TOOL_DEDUP_TTL: float = 30.0

# Phone number pattern: strip everything except digits and leading +
_PHONE_DIGITS_RE = re.compile(r"[^\d+]")


def _validate_and_normalize_phone(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """Validate and normalize phone number.

    Returns:
        (normalized_phone_string, error_message)
    """
    if not raw or not raw.strip():
        return None, "Phone number cannot be empty."

    # Strip everything except digits and leading +
    cleaned = _PHONE_DIGITS_RE.sub("", raw.strip())
    digits_only = re.sub(r"[^\d]", "", cleaned)

    # 1. Less than 7 digits is invalid
    if len(digits_only) < 7:
        return (
            None,
            f"Invalid phone number '{raw.strip()}': must contain at least 7 digits.",
        )

    # 2. Exactly 7 digits -> assume Guyana (+592)
    if len(digits_only) == 7:
        return f"+592 {digits_only[:3]} {digits_only[3:]}", None

    # 3. 10 digits starting with 592 -> Guyana (+592)
    if len(digits_only) == 10 and digits_only.startswith("592"):
        return f"+592 {digits_only[3:6]} {digits_only[6:]}", None

    # 4. More than 7 digits (foreign number or already has country code)
    # Ensure it starts with '+' if it doesn't already. Avoid adding any other country codes.
    has_plus = cleaned.startswith("+")
    if not has_plus:
        return f"+{digits_only}", None

    # Already has plus, format nicely or keep as is
    return cleaned, None


def _validate_and_normalize_email(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """Validate and normalize email address.

    Returns:
        (normalized_email_string, error_message)
    """
    if not raw or not raw.strip():
        return None, "Email address cannot be empty."

    cleaned = raw.strip()

    # 1. Must contain exactly one @ sign
    if cleaned == "N/A" or cleaned == "n/a":
        return cleaned, None  # Allow 'N/A' as a valid placeholder
    if cleaned.count("@") != 1:
        return (
            None,
            f"Invalid email address '{cleaned}': must contain exactly one '@' sign.",
        )

    # 2. Cannot have spaces
    if " " in cleaned:
        return None, f"Invalid email address '{cleaned}': cannot contain spaces."

    # 3. Must have something before and after @
    local_part, domain_part = cleaned.split("@")
    if not local_part or not domain_part:
        return (
            None,
            f"Invalid email address '{cleaned}': must have text before and after '@'.",
        )

    # 4. Domain must have at least one dot (e.g., example.com)
    if "." not in domain_part:
        return (
            None,
            f"Invalid email address '{cleaned}': domain must contain a dot (e.g., 'example.com').",
        )

    # 5. Lowercase for consistency
    return cleaned.lower(), None


_FIELD_ALIASES: Dict[str, List[str]] = {
    "name": ["name", "first_name", "last_name", "full_name", "contact_name"],
    "organization": [
        "organization",
        "company",
        "org",
        "organisation",
        "organization_company",
        "organization / company",
    ],
    "project": [
        "project_description",
        "project_name",
        "project",
        "project_details",
        "project name",
        "project description",
    ],
    "project_location": [
        "project_location",
        "location",
        "site_location",
        "site",
        "project location",
    ],
    "email": ["email", "email_address", "e_mail", "mail"],
    "phone": ["phone", "phone_number", "telephone", "mobile", "contact_number"],
    "interested_products": [
        "interested_products",
        "interested_services",
        "products interested in",
        "products and services interested in",
        "interested in",
        "products wanted",
        "services_wanted",
        "products/services needed",
        "items interested in",
        "interested_product",
        "interested product",
        "interested service",
        "product interested",
        "products/services interested",
        "items interested",
    ],
    "role": ["role", "title", "job_title", "position", "role_title"],
    "industry": ["industry", "sector", "vertical", "field"],
    "team_size": [
        "team_size",
        "team size",
        "headcount",
        "team_count",
        "number_of_employees",
        "employees",
    ],
    "budget": ["budget", "budget_amount", "price_range", "spend"],
    "timeline": ["timeline", "deadline", "target_date", "schedule", "when"],
    "pain_points": [
        "pain_points",
        "pain points",
        "challenges",
        "problems",
        "issues",
        "frustrations",
    ],
    "current_tools": [
        "current_tools",
        "current tools",
        "tools",
        "systems",
        "software",
        "existing_tools",
    ],
    "communication_preference": [
        "communication_preference",
        "communication preference",
        "contact_preference",
        "preferred_contact",
        "communication_style",
    ],
    "requested_items": [
        "requested_items",
        "requested items",
        "items requested",
        "products requested",
        "items they want",
        "things they want",
        "products they need",
        "requested_item",
        "requested item",
        "item requested",
        "product requested",
        "product they need",
    ],
    "declined_items": [
        "declined_items",
        "declined items",
        "items declined",
        "not wanted",
        "don't want",
        "do not want",
        "don't need",
        "do not need",
        "skip",
        "remove",
        "not needed",
        "no longer needed",
        "changed my mind",
        "don't bother",
    ],
    "feedback": [
        "feedback",
        "customer_feedback",
        "product_feedback",
        "feedback_notes",
        "comments",
        "opinion",
    ],
}

_DEFAULT_FIELD_DESCRIPTIONS: Dict[str, str] = {
    "name": "Full name of the contact",
    "organization": "Company or organization name",
    "project_description": "Description of the project or what they need",
    "project_location": "Where the project is based",
    "email": "Email address",
    "phone": "Phone number",
    "interested_products": "GENERAL product categories only (e.g. 'safety boots', 'CRM'). NEVER use for specific models, sizes, colors, or quantities.",
    "role": "Job title or role",
    "industry": "Industry or sector",
    "team_size": "Team size or headcount",
    "budget": "Budget range",
    "timeline": "Project timeline or deadline",
    "pain_points": "Problems or challenges they want to solve",
    "current_tools": "Current tools, systems, or software they use",
    "communication_preference": "Preferred way to communicate",
    "requested_items": "EXACT product requests with size/quantity/color. Format: '<qty> <product name> <size/color/model>'. Separate multiple items with '|'.",
    "previous_requests": "Past interests or previous orders (auto-managed)",
    "declined_items": "Items the user explicitly declined or removed",
    "feedback": "Customer feedback, opinions, or suggestions",
}


class LeadProfileAction(Action):
    """Tool-based lead profile manager: update and sync to Google Sheet."""

    description: str = "Update the lead profile from conversation data."

    # -- LLM config for extraction (mirrors old LeadInteractAction) ----------
    model_action_type: str = attribute(default="OpenAILanguageModelAction")
    model: str = attribute(default="gpt-4o-mini")
    model_temperature: float = attribute(default=0.1)
    extraction_max_tokens: int = attribute(default=512)
    fields: Dict[str, Dict[str, Any]] = attribute(
        default_factory=dict,
        description=(
            "Field definitions that extend or override defaults. "
            "Each key is a canonical field name; value is a dict with optional keys: "
            "required (bool), aliases (list[str]), description (str), merge (bool). "
            "Example: {ai_experience: {required: false, description: 'AI experience level', aliases: ['ai experience']}}"
        ),
    )
    tool_description: str = attribute(
        default="",
        description="Custom description for the lead_profile__save tool. If empty, a default is built from field_descriptions.",
    )
    max_days_to_archive: int = attribute(
        default=0,
        description="Maximum days before a changed field is archived to past_<field>. 0 disables auto-archiving.",
    )
    auto_update_fields: List[str] = attribute(
        default_factory=list,
        description="Fields that should be automatically updated from conversation data without requiring explicit user confirmation. Use with caution to avoid overwriting important information.",
    )

    # -- Storage config --
    storage_mode: str = attribute(
        default="db_only",
        description="Where to store lead profiles: db_only",
    )

    # -- Sales agent notification --------------------------------------------
    sales_agent_email: str = attribute(
        default="",
        description="Email address of the sales agent to notify about new lead requests",
    )
    sales_agent_phone: str = attribute(
        default="",
        description="WhatsApp phone number of the sales agent to notify about new lead requests",
    )

    # ------------------------------------------------------------------
    # Resolved field config (merges defaults with attribute overrides)
    # ------------------------------------------------------------------

    def _resolved_field_aliases(self) -> Dict[str, List[str]]:
        merged = dict(_FIELD_ALIASES)
        for key, cfg in (self.fields or {}).items():
            aliases = cfg.get("aliases", []) if isinstance(cfg, dict) else []
            if aliases:
                if key in merged:
                    merged[key] = merged[key] + [
                        a for a in aliases if a not in merged[key]
                    ]
                else:
                    merged[key] = list(aliases)
        return merged

    def _resolved_field_descriptions(self) -> Dict[str, str]:
        merged = dict(_DEFAULT_FIELD_DESCRIPTIONS)
        for key, cfg in (self.fields or {}).items():
            desc = cfg.get("description", "") if isinstance(cfg, dict) else ""
            if desc:
                merged[key] = desc
        return merged

    _DEFAULT_REQUIRED = [
        "name",
        "organization",
        "project_description",
        "email",
        "phone",
    ]
    _DEFAULT_OPTIONAL = [
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
    _DEFAULT_MERGE = ["interested_products"]

    def _resolved_required_fields(self) -> List[str]:
        custom = {
            k
            for k, cfg in (self.fields or {}).items()
            if isinstance(cfg, dict) and cfg.get("required", False)
        }
        removed = {
            k
            for k, cfg in (self.fields or {}).items()
            if isinstance(cfg, dict)
            and k in self._DEFAULT_REQUIRED
            and not cfg.get("required", False)
        }
        return [f for f in self._DEFAULT_REQUIRED if f not in removed] + [
            f for f in custom if f not in self._DEFAULT_REQUIRED and f not in removed
        ]

    def _resolved_optional_fields(self) -> List[str]:
        custom = {
            k
            for k, cfg in (self.fields or {}).items()
            if isinstance(cfg, dict) and not cfg.get("required", False)
        }
        return [f for f in self._DEFAULT_OPTIONAL if f not in custom] + [
            f for f in custom if f not in self._DEFAULT_OPTIONAL
        ]

    def _resolved_merge_fields(self) -> List[str]:
        result = list(self._DEFAULT_MERGE)
        for key, cfg in (self.fields or {}).items():
            if isinstance(cfg, dict):
                if cfg.get("merge", False) and key not in result:
                    result.append(key)
                if not cfg.get("merge", False) and key in result:
                    result.remove(key)
        return result

    def _all_fields(self) -> List[str]:
        base = set(self._resolved_required_fields()) | set(
            self._resolved_optional_fields()
        )
        for key in self.fields or {}:
            base.add(key)
        return list(base)

    # ------------------------------------------------------------------
    # Tool surface
    # ------------------------------------------------------------------

    async def get_tools(self) -> List[Any]:
        field_aliases = self._resolved_field_aliases()
        field_descriptions = self._resolved_field_descriptions()
        all_props: Dict[str, Any] = {
            "fields": {
                "type": "object",
                "description": "Flat key-value map of extracted lead fields (snake_case keys). Example: {'interested_products': 'steel toe boots', 'name': 'Jane'}. You may also pass fields as top-level arguments directly.",
            },
        }
        for canonical in self._all_fields():
            desc = field_descriptions.get(
                canonical, f"Lead field: {canonical.replace('_', ' ')}."
            )
            all_props[canonical] = {
                "type": "string",
                "description": desc,
            }
        for canonical in field_aliases:
            if canonical not in all_props:
                desc = field_descriptions.get(
                    canonical, f"Lead field: {canonical.replace('_', ' ')}."
                )
                all_props[canonical] = {
                    "type": "string",
                    "description": desc,
                }

        if self.tool_description:
            save_desc = self.tool_description
        else:
            field_lines = []
            for fname, fdesc in field_descriptions.items():
                field_lines.append(f"- {fname}: {fdesc}")
            save_desc = (
                "SAVE/UPDATE the user's lead profile with new data. "
                "Do NOT call this tool with no arguments or empty parameters. Only call it when there is new information to save. "
                "Do NOT call this to retrieve the profile — use lead_profile__retrieve for that. "
                "Call this WHENEVER the user provides new personal/business data, product interest, phone numbers, preferences or feedback. "
                "IMPORTANT: Save even negative or absent values (e.g. 'no AI experience' → ai_experience='none', 'no budget' → budget='none'). "
                "Never omit a field just because the value is negative — the profile needs to know what the user does NOT have as well. "
                "IMPORTANT: This tool ONLY saves to the local profile — it does NOT sync to external systems. "
                "After this returns 'updated', you MUST call the lead_sync tool to push the profile to Google Sheets and other external systems. "
                "Field descriptions:\n" + "\n".join(field_lines)
            )

        return [
            Tool(
                name="lead_profile__save",
                description=save_desc,
                parameters_schema={
                    "type": "object",
                    "properties": all_props,
                    "minProperties": 1,
                },
                execute=self._tool_update,
            ),
            Tool(
                name="lead_profile__update",
                description=(
                    "Update the user's lead profile with new fields. "
                    "Do NOT call this tool with no arguments or empty parameters. Only call it when there is new information to update. "
                    "NOTE: Use lead_profile__save instead — it updates the profile in one call. "
                    "Use this tool only when you specifically want to update without calling lead_sync."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": all_props,
                    "minProperties": 1,
                },
                execute=self._tool_update,
            ),
            Tool(
                name="lead_profile__retrieve",
                description=(
                    "Retrieve the current user's lead profile. Returns all stored fields "
                    "so you can personalise your response and identify missing information. "
                    "Call this at the start of a conversation or when you need to check what "
                    "you already know about the user before asking questions."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
                execute=self._tool_retrieve,
            ),
        ]

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    async def _tool_update(self, **kwargs: Any) -> str:
        """Accept either {"fields": {...}} or flat kwargs from the model."""
        visitor = get_dispatch_visitor()
        interaction = getattr(visitor, "interaction", None)
        if not interaction:
            return json.dumps({"error": "no active interaction"})

        user = await interaction.get_user()
        if not user:
            return json.dumps({"error": "no user found"})

        fields: Dict[str, Any] = kwargs.pop("fields", None) or kwargs
        # --- Normalize keys immediately so routing and merging logic operates on canonical keys ---
        fields = self._normalize_fields(fields)

        # -- Dedup: skip if this exact call was already executed this turn --
        call_key = (user.user_id, json.dumps(fields, sort_keys=True, default=str))
        now = time.time()
        last = _LAST_TOOL_CALLS.get(call_key, 0)
        if now - last < _TOOL_DEDUP_TTL:
            logger.debug(
                "lead_profile__update dedup: skipping duplicate call for %s",
                user.user_id,
            )
            return json.dumps({"status": "deduplicated"})
        _LAST_TOOL_CALLS[call_key] = now

        # Auto-fill phone and name from WhatsApp if missing
        try:
            channel = getattr(interaction, "channel", "default")
            user = await interaction.get_user()
            if channel.lower() == "whatsapp" and user.user_id:
                # Auto-fill phone if not being set
                if "phone" not in fields and "phone_number" not in fields:
                    fields["phone"] = user.user_id
                    logger.info(
                        "lead_profile__update: set phone from whatsapp session_id: %s",
                        user.user_id,
                    )
                # Auto-fill name if not being set and available from User
                if ("name" not in fields or not fields.get("name")) and user.name:
                    fields["name"] = user.name
                    logger.info(
                        "lead_profile__update: set name from user.name: %s",
                        user.name,
                    )
        except Exception as exc:
            logger.debug(
                "lead_profile__update: could not read interaction channel/session: %s",
                exc,
            )

        lp = await self._get_profile(user)
        if not lp:
            return json.dumps({"error": "failed to load lead profile"})

        profile_data = lp.get_yaml() or {}
        for merge_field in self._resolved_merge_fields():
            new_val = str(fields.get(merge_field, "")).strip()
            if not new_val:
                continue
            old_val = str(profile_data.get(merge_field, "")).strip()
            lp_last_updated = lp.last_updated
            if lp_last_updated is None:
                lp_last_updated = lp.created_at

            days_since = 999
            if lp_last_updated:
                try:
                    now_utc = datetime.now(timezone.utc)
                    if lp_last_updated.tzinfo is None:
                        lp_last_updated = lp_last_updated.replace(tzinfo=timezone.utc)
                    days_since = (now_utc - lp_last_updated).days
                except Exception:
                    pass

            if old_val and days_since > self.max_days_to_archive > 0:
                past_field = f"past_{merge_field}"
                past = str(profile_data.get(past_field, "")).strip()
                past_list = (
                    [p.strip() for p in past.split(",") if p.strip()] if past else []
                )
                if old_val not in past_list:
                    past_list.append(old_val)
                    fields[past_field] = ", ".join(past_list)
                    logger.info(
                        "lead_profile__update: archived '%s' to %s (%.1f days old)",
                        old_val,
                        past_field,
                        days_since,
                    )
            elif old_val:
                old_items = [p.strip() for p in old_val.split(",") if p.strip()]
                new_items = [p.strip() for p in new_val.split(",") if p.strip()]

                items_to_remove = set()
                for new_item in new_items:
                    new_lower = new_item.lower()
                    for old_item in old_items:
                        old_lower = old_item.lower()
                        if old_lower != new_lower and old_lower in new_lower:
                            items_to_remove.add(old_item)

                merged_list = [i for i in old_items if i not in items_to_remove]
                merged_set = {i.lower() for i in merged_list}
                for item in new_items:
                    if item.lower() not in merged_set:
                        merged_list.append(item)
                        merged_set.add(item.lower())

                fields[merge_field] = ", ".join(merged_list)
                if items_to_remove:
                    logger.info(
                        "lead_profile__update: refined %s (removed %s, added %s)",
                        merge_field,
                        list(items_to_remove),
                        new_items,
                    )
                else:
                    logger.info(
                        "lead_profile__update: merged %s to '%s' (%.1f days since last)",
                        merge_field,
                        fields[merge_field],
                        days_since,
                    )

        # --- Remove fulfilled items from requested_items ---
        await self._remove_fulfilled_items(fields, profile_data)

        # --- Track when requested_items is updated (for auto-archive) ---
        for field in self.auto_update_fields:
            if field in fields and fields[field]:
                from jvagent.core.app import App

                app = await App.get()
                now_str = await app.now("%Y-%m-%d %H:%M")
                profile_data[f"_field_updated_at"] = now_str

        # Standardize phone number if present
        phone_raw = fields.get("phone")
        if phone_raw:
            normalized_phone, err = _validate_and_normalize_phone(str(phone_raw))
            if err:
                return json.dumps({"error": err, "status": "invalid-argument"})
            fields["phone"] = normalized_phone

        # Validate email if present
        email_raw = fields.get("email")
        if email_raw:
            normalized_email, err = _validate_and_normalize_email(str(email_raw))
            if err:
                return json.dumps({"error": err, "status": "invalid-argument"})
            fields["email"] = normalized_email

        normalized = fields
        if not normalized:
            # Even with no fields, return the current profile
            try:
                profile_md = await lp.as_markdown(include_empty=False)
            except Exception:
                profile_md = ""
            result: Dict[str, Any] = {"status": "no-op", "reason": "no extractable fields"}
            if profile_md.strip():
                result["lead_profile"] = profile_md
            return json.dumps(result)

        changed = await lp.update_yaml(normalized)
        if changed:
            # Build a human-readable summary of what was set (with values)
            updates_desc = []
            for k, v in normalized.items():
                if k.startswith("_"):
                    continue
                # Truncate very long values for brevity
                val_str = str(v)[:120]
                if len(str(v)) > 120:
                    val_str += "..."
                updates_desc.append(f"{k} = '{val_str}'")
            summary_text = (
                "Set: " + "; ".join(updates_desc) if updates_desc else "Updated profile"
            )
            await lp.append_to_section("conversation_summaries", summary_text)

            # --- Log required field captures ---
            from jvagent.core.app import App

            app = await App.get()
            now_str = await app.now("%Y-%m-%d %H:%M")
            logged_fields = []
            for field in self._resolved_required_fields():
                if field in normalized and normalized[field]:
                    logged_fields.append(f"{field}={normalized[field]}")
            if logged_fields:
                log_entry = f"Captured: {', '.join(logged_fields)}"
                try:
                    await lp.log_conversation(log_entry)
                    logger.info(
                        "lead_profile__save: logged captured fields for user %s: %s",
                        user.user_id,
                        logged_fields,
                    )
                except Exception:
                    pass

            # --- Log declined items with timestamp AND remove from requested_items ---
            declined_raw = fields.get("declined_items", "")
            if declined_raw:
                declined_list = [
                    i.strip() for i in str(declined_raw).split(",") if i.strip()
                ]
                if declined_list:
                    # Remove declined items from requested_items
                    await self._remove_declined_items(
                        declined_list, fields, profile_data, lp
                    )
                    # Log the decline
                    await self._log_declined_items(declined_list, lp, user.user_id)

            # --- Regenerate session summary from conversation_summaries ---
            try:
                await lp.generate_and_save_session_summary(interaction.conversation_id)
            except Exception:
                pass

            # --- Immediate conversation log when requested_items is updated ---
            if "requested_items" in normalized:
                try:
                    entry = f"User requested: {normalized['requested_items']}"
                    await lp.log_conversation(entry)
                    logger.info(
                        "lead_profile__save: immediate log for requested_items for user %s",
                        user.user_id,
                    )
                except Exception:
                    pass

            pass

        try:
            profile_md = await lp.as_markdown(include_empty=False)
        except Exception:
            profile_md = ""

        status_label = "updated" if changed else "no-op"
        result = {
            "status": status_label,
            "fields_saved": list(normalized.keys()) if changed else [],
        }
        if profile_md.strip():
            result["lead_profile"] = profile_md
        return json.dumps(result)

    async def _tool_retrieve(self, **kwargs: Any) -> str:
        """Retrieve the current user's lead profile."""
        visitor = get_dispatch_visitor()
        interaction = getattr(visitor, "interaction", None)
        if not interaction:
            return json.dumps({"error": "no active interaction"})

        user = await interaction.get_user()
        if not user:
            return json.dumps({"error": "no user found"})

        lp = await self._get_profile(user)
        if not lp:
            return json.dumps({"status": "no_profile", "fields": {}})

        profile_data = lp.get_yaml() or {}
        if not profile_data:
            return json.dumps({"status": "empty_profile", "fields": {}})

        clean = {k: v for k, v in profile_data.items() if not k.startswith("_")}
        return json.dumps({"status": "ok", "fields": clean})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_profile(self, user: User) -> Optional[Any]:
        """Resolve the LeadProfile node, with cross-branch compatibility."""
        try:
            from jvagent.action.lead_profile import LeadProfile

            return await LeadProfile.get_or_create_for_user(
                user, required_fields=list(self._resolved_required_fields())
            )
        except (ImportError, AttributeError):
            try:
                from jvagent.actions.lead_profile import LeadProfile  # type: ignore[import-not-found,no-redef]

                return await LeadProfile.get_or_create_for_user(
                    user, required_fields=list(self._resolved_required_fields())
                )
            except (ImportError, AttributeError) as exc:
                logger.warning("LeadProfile unavailable: %s", exc)
                return None

    def _normalize_fields(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key, value in raw.items():
            if key.startswith("_"):
                out[key] = value
                continue
            canonical = self._canonical_key(key)
            if canonical:
                out[canonical] = value
            else:
                out[key] = value
        return out

    def _canonical_key(self, raw: str) -> Optional[str]:
        if not raw:
            return None
        clean = raw.strip().lower().replace(" ", "_").replace("-", "_")
        aliases = self._resolved_field_aliases()
        for canonical, alias_list in aliases.items():
            if clean == canonical:
                return canonical
            for alias in alias_list:
                if clean == alias.lower().replace(" ", "_").replace("-", "_"):
                    return canonical
        if (
            clean in self._resolved_required_fields()
            or clean in self._resolved_optional_fields()
        ):
            return clean
        if clean in {f for fs in aliases.values() for f in fs}:
            return clean
        return None

    def _is_specific_item(self, text: str) -> bool:
        """Return True if text looks like a specific item with sizes/quantities/models."""
        text_lower = text.lower()
        specific_indicators = [
            "size",
            "sz",
            "rolls",
            "pieces",
            "units",
            "ft",
            "feet",
            "inches",
            'in"',
            "mm",
            "meters",
            "kg",
            "lbs",
            "pairs",
            "pair",
            "pack",
            "box",
            "carton",
            "dozen",
            "qty",
            "quantity",
            "length",
            "width",
            "height",
            "diameter",
            "model",
            "sku",
            "code",
            "no.",
            "number",
            "serial",
        ]
        has_numbers = bool(re.search(r"\d", text))
        has_indicator = any(ind in text_lower for ind in specific_indicators)
        return (has_numbers and has_indicator) or (has_numbers and len(text) > 40)

    def _merge_requested_items(
        self, new_items: List[str], existing_list: List[str]
    ) -> List[str]:
        """Merge new requested items, replacing existing items that are corrections of the same product."""
        result = list(existing_list)
        for new_item in new_items:
            replaced = False
            new_lower = new_item.lower()
            new_words = {w for w in new_lower.split() if len(w) > 2}
            for i, ex in enumerate(result):
                ex_lower = ex.lower()
                ex_words = {w for w in ex_lower.split() if len(w) > 2}
                overlap = new_words & ex_words
                shorter = (
                    min(len(new_words), len(ex_words)) if new_words and ex_words else 0
                )
                if shorter > 0 and len(overlap) / shorter >= 0.5:
                    result[i] = new_item
                    replaced = True
                    logger.info(
                        "lead_profile__update: replaced requested item '%s' with '%s'",
                        ex,
                        new_item,
                    )
                    break
            if not replaced and new_item not in result:
                result.append(new_item)
        return result

    @staticmethod
    def _consolidate_size_entries(items: List[str]) -> List[str]:
        """Attach orphaned size/qty tokens to their matching item.

        The LLM sometimes writes::

            ['size 38', '1 Pink Fly Knit Steel Toe shoe', '1 Hard Hat Elastic Cover']

        This method detects entries that look like a bare size/qty annotation
        (e.g. ``'size 38'``, ``'sz 10'``, ``'quantity 2'``, ``'qty 3'``) and
        appends them to the most likely matching item already in the list.
        If no matching item is found, the annotation is appended to the
        immediately preceding item.
        """
        import re as _re

        # Pattern: entry is ONLY a size/qty annotation with no real product noun
        _SIZE_ONLY = _re.compile(
            r"^(?:size|sz|eu|us|uk|quantity|qty|q\.?ty\.?)\s*[\d\.]+\s*$",
            _re.IGNORECASE,
        )
        # Also catch patterns like 'size 38' at the start of otherwise empty entries
        _SIZE_PREFIX = _re.compile(
            r"^(?:size|sz|eu|us|uk)\s+([\d\.]+(?:\s*-\s*[\d\.]+)?)\s*$",
            _re.IGNORECASE,
        )

        if not items:
            return items

        result: List[str] = []
        pending_size: Optional[str] = None
        pending_idx: Optional[int] = None  # index of the last real item

        for entry in items:
            if _SIZE_ONLY.match(entry) or _SIZE_PREFIX.match(entry):
                # This is a bare annotation â€” remember it and try to attach
                pending_size = entry.strip()
            else:
                # Real item â€” first flush any pending size onto the previous item
                if pending_size and result:
                    result[-1] = f"{result[-1]} {pending_size}"
                    logger.info(
                        "_consolidate_size_entries: attached '%s' to '%s'",
                        pending_size,
                        result[-1],
                    )
                    pending_size = None
                result.append(entry)

        # If there's still a pending size after iterating, attach to last real item
        if pending_size and result:
            result[-1] = f"{result[-1]} {pending_size}"
            logger.info(
                "_consolidate_size_entries: attached trailing '%s' to '%s'",
                pending_size,
                result[-1],
            )

        return result

    @staticmethod
    def _deduplicate_items(raw: str) -> str:
        """Deduplicate a comma-separated items string (keeps longest/most-specific form)."""
        if not raw or not raw.strip():
            return raw
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if len(parts) <= 1:
            return raw
        kept: List[str] = []
        for candidate in parts:
            cl = candidate.lower()
            absorbed = False
            for i, existing in enumerate(kept):
                el = existing.lower()
                if el == cl:
                    if len(candidate) > len(existing):
                        kept[i] = candidate
                    absorbed = True
                    break
                if el in cl:  # candidate is more specific
                    kept[i] = candidate
                    absorbed = True
                    break
                if cl in el:  # existing is already more specific
                    absorbed = True
                    break
            if not absorbed:
                kept.append(candidate)
        return ", ".join(kept)

    async def _maybe_generate_session_summary(self, lp: Any) -> None:
        """Build a simple narrative _session_summary from conversation_summaries.

        Uses requested_items when available (preferred); falls back to
        interested_products. Both are deduplicated before rendering.
        """
        try:
            node = await lp.get_section("conversation_summaries")
            if not node or node.is_empty():
                return
            content = node.content or ""
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            if not lines:
                return

            profile_data = lp.get_yaml() or {}
            name = profile_data.get("name", "")
            org = profile_data.get("organization", "")
            project = profile_data.get("project_name", "")
            location = profile_data.get("project_location", "")
            interested = str(profile_data.get("interested_products", "")).strip()
            requested = self._deduplicate_items(
                str(profile_data.get("requested_items", "")).strip()
            )
            feedback = profile_data.get("feedback", "")
            phone = profile_data.get("phone", "")
            email = profile_data.get("email", "")

            # Build identity tokens joined with spaces
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
                actions.append(f"interested in {self._deduplicate_items(interested)}")
            if feedback:
                actions.append(f"gave feedback: {feedback}")

            contact_info: List[str] = []
            if phone:
                contact_info.append(f"phone {phone}")
            if email:
                contact_info.append(f"email {email}")

            summary = " ".join(identity_parts)
            if actions:
                summary += " â€” " + "; ".join(actions)
            if contact_info:
                summary += " | Contact: " + " / ".join(contact_info)

            session_summary = summary.strip()
            if not session_summary:
                session_summary = "Lead engaged; no key details captured yet."

            profile_data["_session_summary"] = session_summary
            await lp.set_yaml(profile_data)
            logger.info(
                "lead_profile__save: generated session summary: %s",
                session_summary[:120],
            )
        except Exception as exc:
            logger.debug(
                "lead_profile__save: session summary generation failed: %s", exc
            )

    async def _resolve_vague_items(
        self, items: List[str], profile_data: Dict[str, Any], lp: Any
    ) -> List[str]:
        """Resolve vague items like '1 boot' to specific products from history.

        Looks through the conversation_log and past requested_items to find
        the most recent specific product mention that matches the vague term.
        """
        resolved: List[str] = []

        # Build a lookup table of product keywords -> full product name from history
        product_history: Dict[str, str] = {}

        # Scan conversation_log for past product mentions
        try:
            log_node = await lp.get_section("conversation_log")
            if log_node and log_node.content:
                for line in reversed(log_node.content.splitlines()):
                    line = line.strip()
                    # Look for patterns like "requested: <product>" or "User requested: <product>"
                    match = re.search(
                        r"requested[:\s]+(.+?)(?:\||$)", line, re.IGNORECASE
                    )
                    if match:
                        hist_items = [i.strip() for i in match.group(1).split(",")]
                        for hist_item in hist_items:
                            # Extract keywords (lowercase, alphanumeric only)
                            keywords = re.findall(r"\b[a-z]+\b", hist_item.lower())
                            for kw in keywords:
                                if len(kw) > 3 and kw not in (
                                    "size",
                                    "roll",
                                    "rolls",
                                    "piece",
                                    "pieces",
                                ):
                                    if kw not in product_history:
                                        product_history[kw] = hist_item
        except Exception:
            pass

        # Also scan past requested_items in profile YAML
        past_requested = str(profile_data.get("requested_items", "")).strip()
        if past_requested:
            for past_item in [
                i.strip() for i in past_requested.split(",") if i.strip()
            ]:
                keywords = re.findall(r"\b[a-z]+\b", past_item.lower())
                for kw in keywords:
                    if len(kw) > 3 and kw not in (
                        "size",
                        "roll",
                        "rolls",
                        "piece",
                        "pieces",
                    ):
                        if kw not in product_history:
                            product_history[kw] = past_item

        # Now resolve each vague item
        for item in items:
            item_lower = item.lower()
            # Check if this item is vague (generic product name without model/size details)
            if self._is_vague_item(item):
                # Try to find a match in history
                keywords = re.findall(r"\b[a-z]+\b", item_lower)
                best_match = None
                for kw in keywords:
                    if kw in product_history and len(kw) > 3:
                        # Found a potential match
                        hist_product = product_history[kw]
                        # Verify the historical product contains the vague term
                        if kw in item_lower or any(kw in w for w in item_lower.split()):
                            best_match = hist_product
                            break

                if best_match:
                    # Replace vague item with the historical full product, preserving quantity
                    qty_match = re.match(r"^(\d+\s*)", item)
                    if qty_match:
                        resolved.append(f"{qty_match.group(1)}{best_match}")
                    else:
                        resolved.append(best_match)
                    logger.debug("Resolved vague item '%s' to '%s'", item, best_match)
                else:
                    # No match found, keep original
                    resolved.append(item)
            else:
                resolved.append(item)

        return resolved

    def _is_vague_item(self, item: str) -> bool:
        """Check if an item description is too vague (e.g., '1 boot', '3 vest')."""
        item_lower = item.lower()
        # Vague items are typically: quantity + generic product name, no size/model/color
        vague_patterns = [
            r"^\d+\s*(boot|boots|shoe|shoes|vest|vests|hat|hats|cover|covers|roll|rolls|fabric|tape|glove|gloves)$",
            r"^(boot|boots|shoe|shoes|vest|vests|hat|hats|cover|covers|roll|rolls|fabric|tape|glove|gloves)\s*\d*$",
        ]
        for pattern in vague_patterns:
            if re.match(pattern, item_lower.strip()):
                return True
        return False

    async def _log_declined_items(
        self, declined_items: List[str], lp: Any, user_id: str
    ) -> None:
        """Log declined items with timestamp to both conversation_log and previous_requests."""
        if not declined_items:
            return

        # Use UTC-4 (Guyana Time) for all timestamps
        from jvagent.core.app import App

        app = await App.get()
        now_str = await app.now("%Y-%m-%d %H:%M")

        # Log to conversation_log
        entry = f"[{now_str}] User declines: {', '.join(declined_items)}"
        try:
            await lp.log_conversation(entry)
            logger.info(
                "lead_profile__save: logged declined items for user %s: %s",
                user_id,
                declined_items,
            )
        except Exception as exc:
            logger.debug("Failed to log declined items to conversation_log: %s", exc)

        # Also add to previous_requests with timestamps
        profile_data = lp.get_yaml() or {}
        prev_requests = profile_data.get("previous_requests", "")

        # Format: [timestamp] declined: item
        for item in declined_items:
            new_entry = f"[{now_str}] declined: {item}"
            if prev_requests:
                prev_requests += f"\n{new_entry}"
            else:
                prev_requests = new_entry

        profile_data["previous_requests"] = prev_requests
        await lp.set_yaml(profile_data)

    async def _remove_declined_items(
        self,
        declined_items: List[str],
        fields: Dict[str, Any],
        profile_data: Dict[str, Any],
        lp: Any,
    ) -> None:
        """Remove declined items from requested_items and archive them in previous_requests."""
        existing_requested = str(fields.get("requested_items", "")).strip()
        if not existing_requested and not profile_data.get("requested_items"):
            return

        # Combine current fields + profile data for the full list
        existing_list: List[str] = []
        if existing_requested:
            existing_list = [
                r.strip() for r in existing_requested.split(",") if r.strip()
            ]
        else:
            existing_list = [
                r.strip()
                for r in str(profile_data.get("requested_items", "")).split(",")
                if r.strip()
            ]

        # Remove items that match declined keywords and archive them
        cleaned: List[str] = []
        archived: List[str] = []
        for item in existing_list:
            keep = True
            item_lower = item.lower()
            for declined in declined_items:
                declined_lower = declined.lower()
                # Check if declined term matches any keyword in the item
                declined_keywords = set(re.findall(r"\b[a-z]+\b", declined_lower))
                item_keywords = set(re.findall(r"\b[a-z]+\b", item_lower))
                # If there's significant overlap, remove it
                if declined_keywords & item_keywords:
                    keep = False
                    archived.append(item)
                    logger.info(
                        "lead_profile__save: archived declined item '%s' to previous_requests",
                        item,
                    )
                    break
            if keep:
                cleaned.append(item)

        fields["requested_items"] = ", ".join(cleaned) if cleaned else ""

        # Archive declined items to previous_requests with timestamp
        if archived:
            # Use UTC-4 (Guyana Time) for all timestamps
            from datetime import timedelta

            tz_guyana = timezone(timedelta(hours=-4))
            now_str = datetime.now(tz_guyana).strftime("%Y-%m-%d %H:%M")
            prev_requests = profile_data.get("previous_requests", "")
            for item in archived:
                new_entry = f"[{now_str}] declined: {item}"
                if prev_requests:
                    prev_requests += f"\n{new_entry}"
                else:
                    prev_requests = new_entry
            profile_data["previous_requests"] = prev_requests
            await lp.set_yaml(profile_data)
