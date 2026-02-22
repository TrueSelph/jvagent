"""Access Control action."""

import logging
import re
from typing import Any, Dict, List

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action

logger = logging.getLogger(__name__)


class AccessControlAction(Action):
    """Agent access control with permissions per channel, action and session_id."""

    exceptions: List[str] = attribute(
        default_factory=list, description="Actions exempt from permissions"
    )

    permissions: Dict[str, Dict] = attribute(
        default_factory=lambda: AccessControlAction._get_default_permissions(),
        description="Channel/resource/user permissions structure",
    )

    session_groups: Dict[str, List[str]] = attribute(
        default_factory=dict, description="Group name to session IDs mapping"
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
        self, session_id: str, action_label: str = "all", channel: str = "default"
    ) -> bool:
        """Check if session has access to action."""
        try:
            if not self.enabled:
                return True

            if action_label in self.exceptions:
                return True

            user_id = self._normalize_session_id(session_id)
            return self._check_access(user_id, channel, action_label)
        except Exception as e:
            logger.error(f"Error checking access for session {session_id}: {e}")
            return False

    def _normalize_session_id(self, session_id: str) -> str:
        """Normalize session ID by extracting user ID from WhatsApp format."""
        if re.match(r"^\+?\d+_[\w-]+$", session_id):
            return session_id.split("_")[0].replace("+", "")
        return session_id

    def _check_access(self, user_id: str, channel: str, resource: str) -> bool:
        """Check access using permissions structure."""
        channel_perms = self.permissions.get(
            channel, self.permissions.get("default", {})
        )
        resource_perms = channel_perms.get(resource, channel_perms.get("any", {}))

        # Check deny rules first
        for deny_rule in resource_perms.get("deny", []):
            if deny_rule.get("enabled", True) and self._matches_rule(
                user_id, deny_rule
            ):
                return False

        # Check allow rules
        for allow_rule in resource_perms.get("allow", []):
            if allow_rule.get("enabled", True) and self._matches_rule(
                user_id, allow_rule
            ):
                return True

        return False

    def _matches_rule(self, user_id: str, rule: Dict) -> bool:
        """Check if user matches permission rule."""
        if rule.get("user") == user_id:
            return True

        rule_group = rule.get("group")
        if rule_group:
            if rule_group in ["all", "any"]:
                return True
            if (
                rule_group in self.session_groups
                and user_id in self.session_groups[rule_group]
            ):
                return True

        return False

    def export_config(self) -> Dict[str, Any]:
        """Export access control configuration."""
        return {
            "permissions": self.permissions,
            "session_groups": self.session_groups,
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
                self.session_groups = config.get("session_groups", {})
                self.exceptions = config.get("exceptions", [])
            else:
                # Merge configurations
                if "permissions" in config:
                    self.permissions.update(config["permissions"])
                if "session_groups" in config:
                    self.session_groups.update(config["session_groups"])
                if "exceptions" in config:
                    self.exceptions.extend(config["exceptions"])

            await self.save()
        except Exception as e:
            logger.error(f"Error importing configuration: {e}")
            raise
