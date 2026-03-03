"""Access Control action."""

import logging
from typing import Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.core.channel import normalize_channel

logger = logging.getLogger(__name__)


class AccessControlAction(Action):
    """Agent access control with permissions per channel, action and user_id."""

    exceptions: List[str] = attribute(
        default_factory=list, description="Actions exempt from permissions"
    )

    permissions: Dict[str, Dict] = attribute(
        default_factory=lambda: AccessControlAction._get_default_permissions(),
        description="Channel/resource/user permissions structure",
    )

    user_groups: Dict[str, List[str]] = attribute(
        default_factory=dict, description="Group name to user IDs mapping"
    )

    default_deny: bool = attribute(
        default=False,
        description="If True, deny when no rule matches; otherwise allow",
    )

    action_aliases: Dict[str, str] = attribute(
        default_factory=dict,
        description="Short name to class name mapping for action_label",
    )

    @staticmethod
    def _get_default_permissions() -> Dict[str, Dict]:
        """Get default permissions structure."""
        return {
            "default": {
                "any": {"deny": [], "allow": [{"group": "all", "enabled": True}]}
            }
        }

    async def has_action_access(
        self,
        user_id: str,
        action_label: str = "all",
        channel: str = "default",
    ) -> bool:
        """Check if user has access to action."""
        try:
            channel = normalize_channel(channel)
            if not self.enabled:
                return True

            if not user_id:
                return True

            resolved_label = self.action_aliases.get(action_label, action_label)
            if resolved_label in self.exceptions:
                return True

            return self._check_access(user_id, channel, resolved_label)
        except Exception as e:
            logger.error(f"Error checking access for user {user_id}: {e}")
            return False

    def _check_access(self, user_id: str, channel: str, resource: str) -> bool:
        """Check access using permissions structure."""
        channel_perms = self.permissions.get(
            channel, self.permissions.get("default", {})
        )
        resource_perms = channel_perms.get(resource, channel_perms.get("any", {}))

        # Check deny rules first
        for deny_rule in resource_perms.get("deny", []):
            rule = self._normalize_rule(deny_rule)
            if rule.get("enabled", True) and self._matches_rule(user_id, rule):
                return False

        # Check allow rules
        for allow_rule in resource_perms.get("allow", []):
            rule = self._normalize_rule(allow_rule)
            if rule.get("enabled", True) and self._matches_rule(user_id, rule):
                return True

        return not self.default_deny

    def _normalize_rule(self, rule: Any) -> Dict:
        """Normalize rule to dict format. Supports shorthand like 'all' or 'admins'."""
        if isinstance(rule, dict):
            return rule
        if isinstance(rule, str):
            return {"group": rule, "enabled": True}
        return {}

    def _matches_rule(self, user_id: str, rule: Dict) -> bool:
        """Check if user matches permission rule."""
        rule_user = rule.get("user")
        if rule_user is not None:
            if rule_user in ["all", "any"]:
                return True
            if rule_user == user_id:
                return True

        rule_group = rule.get("group")
        if rule_group:
            if rule_group in ["all", "any"]:
                return True
            if (
                rule_group in self.user_groups
                and user_id in self.user_groups[rule_group]
            ):
                return True

        return False

    def _ensure_permission_entry(self, channel: str, action_label: str) -> None:
        """Ensure permissions[channel][action_label] exists with allow/deny lists."""
        if channel not in self.permissions:
            self.permissions[channel] = {}
        if action_label not in self.permissions[channel]:
            self.permissions[channel][action_label] = {"deny": [], "allow": []}
        entry = self.permissions[channel][action_label]
        if "deny" not in entry:
            entry["deny"] = []
        if "allow" not in entry:
            entry["allow"] = []

    async def add_user_group(
        self, name: str, user_ids: Optional[List[str]] = None
    ) -> None:
        """Create user group, optionally with initial user_ids. Idempotent if exists."""
        if name not in self.user_groups:
            self.user_groups[name] = []
        if user_ids:
            for uid in user_ids:
                if uid not in self.user_groups[name]:
                    self.user_groups[name].append(uid)
        await self.save()

    async def add_user_to_group(self, group: str, user_id: str) -> None:
        """Add user to group. No-op if already in group."""
        if group not in self.user_groups:
            self.user_groups[group] = []
        if user_id not in self.user_groups[group]:
            self.user_groups[group].append(user_id)
        await self.save()

    async def add_users_to_group(self, group: str, user_ids: List[str]) -> None:
        """Add users to group. No-op for users already in group."""
        if group not in self.user_groups:
            self.user_groups[group] = []
        for uid in user_ids:
            if uid not in self.user_groups[group]:
                self.user_groups[group].append(uid)
        await self.save()

    async def remove_user_from_group(self, group: str, user_id: str) -> None:
        """Remove user from group."""
        if group in self.user_groups:
            self.user_groups[group] = [
                u for u in self.user_groups[group] if u != user_id
            ]
        await self.save()

    async def remove_user_group(self, name: str) -> None:
        """Remove user group."""
        if name in self.user_groups:
            del self.user_groups[name]
        await self.save()

    def get_user_groups(self) -> Dict[str, List[str]]:
        """Return copy of user groups."""
        return {k: list(v) for k, v in self.user_groups.items()}

    async def add_user_to_allow(
        self, channel: str, action_label: str, user_id: str
    ) -> None:
        """Add user directly to allow list for channel+action."""
        self._ensure_permission_entry(channel, action_label)
        rules = self.permissions[channel][action_label]["allow"]
        if not any(r.get("user") == user_id for r in rules if isinstance(r, dict)):
            rules.append({"user": user_id, "enabled": True})
        await self.save()

    async def add_user_to_deny(
        self, channel: str, action_label: str, user_id: str
    ) -> None:
        """Add user directly to deny list for channel+action."""
        self._ensure_permission_entry(channel, action_label)
        rules = self.permissions[channel][action_label]["deny"]
        if not any(r.get("user") == user_id for r in rules if isinstance(r, dict)):
            rules.append({"user": user_id, "enabled": True})
        await self.save()

    async def remove_user_from_permission(
        self,
        channel: str,
        action_label: str,
        user_id: str,
        from_allow: bool = True,
    ) -> None:
        """Remove user rule from allow or deny list."""
        self._ensure_permission_entry(channel, action_label)
        key = "allow" if from_allow else "deny"
        rules = self.permissions[channel][action_label][key]
        self.permissions[channel][action_label][key] = [
            r for r in rules if not (isinstance(r, dict) and r.get("user") == user_id)
        ]
        await self.save()

    async def add_group_to_allow(
        self, channel: str, action_label: str, group: str
    ) -> None:
        """Add group to allow list for channel+action."""
        self._ensure_permission_entry(channel, action_label)
        rules = self.permissions[channel][action_label]["allow"]
        if not any(r.get("group") == group for r in rules if isinstance(r, dict)):
            rules.append({"group": group, "enabled": True})
        await self.save()

    async def add_group_to_deny(
        self, channel: str, action_label: str, group: str
    ) -> None:
        """Add group to deny list for channel+action."""
        self._ensure_permission_entry(channel, action_label)
        rules = self.permissions[channel][action_label]["deny"]
        if not any(r.get("group") == group for r in rules if isinstance(r, dict)):
            rules.append({"group": group, "enabled": True})
        await self.save()

    async def remove_group_from_permission(
        self,
        channel: str,
        action_label: str,
        group: str,
        from_allow: bool = True,
    ) -> None:
        """Remove group rule from allow or deny list."""
        self._ensure_permission_entry(channel, action_label)
        key = "allow" if from_allow else "deny"
        rules = self.permissions[channel][action_label][key]
        self.permissions[channel][action_label][key] = [
            r for r in rules if not (isinstance(r, dict) and r.get("group") == group)
        ]
        await self.save()

    def export_config(self) -> Dict[str, Any]:
        """Export access control configuration."""
        return {
            "permissions": self.permissions,
            "user_groups": self.user_groups,
            "exceptions": self.exceptions,
        }

    async def import_config(self, config: Dict[str, Any], purge: bool = False) -> None:
        """Import access control configuration.

        Args:
            config: Configuration dictionary
            purge: If True, replace existing config completely
        """
        try:
            if purge:
                self.permissions = config.get("permissions", {})
                self.user_groups = config.get(
                    "user_groups", config.get("session_groups", {})
                )
                self.exceptions = config.get("exceptions", [])
            else:
                if "permissions" in config:
                    self.permissions.update(config["permissions"])
                if "user_groups" in config:
                    self.user_groups.update(config["user_groups"])
                elif "session_groups" in config:
                    self.user_groups.update(config["session_groups"])
                if "exceptions" in config:
                    self.exceptions.extend(config["exceptions"])

            await self.save()
        except Exception as e:
            logger.error(f"Error importing configuration: {e}")
            raise
