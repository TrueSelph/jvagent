"""Access Control action."""

import logging
from typing import Any, ClassVar, Dict, List, Optional, Union

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.core.channel import normalize_channel

logger = logging.getLogger(__name__)


def log_access_denied(
    *,
    agent_id: str,
    user_id: Optional[str],
    channel: str,
    action_label: str,
    stage: str,
    reason: str = "rule",
) -> None:
    """Structured log line for denied access (interact entry, walker, background, whatsapp)."""
    logger.warning(
        "access_control_denied",
        extra={
            "event": "access_control_denied",
            "agent_id": agent_id,
            "user_id": user_id or "",
            "channel": channel,
            "action_label": action_label,
            "stage": stage,
            "reason": reason,
        },
    )


class AccessControlAction(Action):
    """Agent access control with permissions per channel, action and user_id."""

    # AUDIT-actions XC-4: admin-facing routes under /agents/{agent_id}/
    # access_control/ (11 sub-routes). Per-agent grouping, not per-action,
    # so use the {agent_id} placeholder.
    additional_endpoint_path_templates: ClassVar[List[str]] = [
        "/agents/{agent_id}/access_control/",
    ]

    exceptions: List[str] = attribute(
        default_factory=list, description="Actions exempt from permissions"
    )

    permissions: Dict[str, Dict] = attribute(
        default_factory=lambda: AccessControlAction._get_default_permissions(),
        description="Channel/resource/user permissions structure",
    )

    user_groups: Dict[str, Dict[str, List[str]]] = attribute(
        default_factory=dict,
        description="Action label to group name to user IDs mapping",
    )

    default_deny: bool = attribute(
        default=False,
        description="If True, deny when no rule matches; otherwise allow",
    )

    action_aliases: Dict[str, str] = attribute(
        default_factory=dict,
        description="Short name to class name mapping for action_label",
    )

    enforce: bool = attribute(
        default=True,
        description="When False, skip permission evaluation (allow all). Use instead of disabling the graph node when possible.",
    )

    allow_anonymous: bool = attribute(
        default=False,
        description="When True, missing/empty user_id is allowed without evaluating rules.",
    )

    @staticmethod
    def _get_default_permissions() -> Dict[str, Dict]:
        """Get default permissions structure."""
        return {
            "default": {
                "any": {"deny": [], "allow": [{"group": "all", "enabled": True}]}
            }
        }

    def policy_applies(self) -> bool:
        """Whether this node should enforce permissions (graph enabled + enforce flag)."""
        return bool(self.enabled) and bool(self.enforce)

    async def has_action_access(
        self,
        user_id: str,
        action_label: str = "all",
        channel: str = "default",
    ) -> bool:
        """Check if user has access to action."""
        try:
            channel = normalize_channel(channel)
            if not self.policy_applies():
                return True

            uid = (user_id or "").strip()
            if not uid:
                if self.allow_anonymous:
                    return True
                return False

            resolved_label = self.action_aliases.get(action_label, action_label)
            if resolved_label in self.exceptions:
                return True

            return self._check_access(uid, channel, resolved_label)
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
            if rule.get("enabled", True) and self._matches_rule(
                user_id, rule, resource
            ):
                return False

        # Check allow rules
        for allow_rule in resource_perms.get("allow", []):
            rule = self._normalize_rule(allow_rule)
            if rule.get("enabled", True) and self._matches_rule(
                user_id, rule, resource
            ):
                return True

        return not self.default_deny

    def _normalize_rule(self, rule: Any) -> Dict:
        """Normalize rule to dict format. Supports shorthand like 'all' or 'admins'."""
        if isinstance(rule, dict):
            return rule
        if isinstance(rule, str):
            return {"group": rule, "enabled": True}
        return {}

    def _resolve_user_groups(self, action_label: str) -> Dict[str, List[str]]:
        """Resolve user groups for an action label.

        Looks up ``action_label`` first, then merges with ``"default"``.
        Returns an empty dict when neither key exists. Inner lists are
        deep-copied to prevent shared-reference mutation across
        concurrent serverless invocations.
        """
        logger.warning(
            "AccessControl: _resolve_user_groups action_label=%s aca_id=%s aca_enabled=%s user_groups_keys=%s",
            action_label,
            getattr(self, "id", "?"),
            getattr(self, "enabled", "?"),
            list(self.user_groups.keys()) if self.user_groups else [],
        )
        if action_label in self.user_groups:
            groups = self.user_groups[action_label]
            default_groups = self.user_groups.get("default", {})
            merged = {k: list(v) if isinstance(v, list) else v for k, v in default_groups.items()}
            for k, v in groups.items():
                merged[k] = list(v) if isinstance(v, list) else v
            logger.warning(
                "AccessControl: _resolve_user_groups merged default=%s with %s=%s → %s",
                default_groups,
                action_label,
                groups,
                merged,
            )
            return merged
        result = {k: list(v) if isinstance(v, list) else v for k, v in self.user_groups.get("default", {}).items()}
        logger.warning(
            "AccessControl: _resolve_user_groups fallback to default → %s",
            result,
        )
        return result

    def _matches_rule(
        self, user_id: str, rule: Dict, action_label: str = "default"
    ) -> bool:
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
            groups = self._resolve_user_groups(action_label)
            if rule_group in groups and user_id in groups[rule_group]:
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
        self,
        name: str,
        user_ids: Optional[List[str]] = None,
        action_label: str = "default",
    ) -> None:
        """Create user group, optionally with initial user_ids. Idempotent if exists."""
        scope = self.user_groups.setdefault(action_label, {})
        if name not in scope:
            scope[name] = []
        if user_ids:
            for uid in user_ids:
                if uid not in scope[name]:
                    scope[name].append(uid)
        await self.save()

    async def add_user_to_group(
        self, group: str, user_id: str, action_label: str = "default"
    ) -> None:
        """Add user to group. No-op if already in group."""
        scope = self.user_groups.setdefault(action_label, {})
        if group not in scope:
            scope[group] = []
        if user_id not in scope[group]:
            scope[group].append(user_id)
        await self.save()

    async def add_users_to_group(
        self, group: str, user_ids: List[str], action_label: str = "default"
    ) -> None:
        """Add users to group. No-op for users already in group."""
        scope = self.user_groups.setdefault(action_label, {})
        if group not in scope:
            scope[group] = []
        for uid in user_ids:
            if uid not in scope[group]:
                scope[group].append(uid)
        await self.save()

    async def remove_user_from_group(
        self, group: str, user_id: str, action_label: str = "default"
    ) -> None:
        """Remove user from group."""
        scope = self.user_groups.get(action_label)
        if scope and group in scope:
            scope[group] = [u for u in scope[group] if u != user_id]
        await self.save()

    async def remove_user_group(self, name: str, action_label: str = "default") -> None:
        """Remove user group."""
        scope = self.user_groups.get(action_label)
        if scope and name in scope:
            del scope[name]
        await self.save()

    def get_user_groups(
        self, action_label: Optional[str] = None
    ) -> Union[Dict[str, Dict[str, List[str]]], Dict[str, List[str]]]:
        """Return copy of user groups.

        When action_label is provided, returns the groups dict for that
        action label (merged with default).  Otherwise returns the full
        nested structure.
        """
        if action_label is not None:
            return {
                k: list(v) for k, v in self._resolve_user_groups(action_label).items()
            }
        return {
            outer_k: {inner_k: list(inner_v) for inner_k, inner_v in outer_v.items()}
            for outer_k, outer_v in self.user_groups.items()
        }

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

    @staticmethod
    def _migrate_user_groups(ug: Any) -> Dict[str, Dict[str, List[str]]]:
        """Migrate flat user_groups to nested format.

        If any top-level value is a list (legacy flat format), the entire
        dict is wrapped under a ``"default"`` key.  Already-nested dicts
        are returned unchanged.
        """
        if not isinstance(ug, dict) or not ug:
            return {}
        first_value = next(iter(ug.values()), None)
        if isinstance(first_value, list):
            return {"default": {k: list(v) for k, v in ug.items()}}
        return ug

    def export_config(self) -> Dict[str, Any]:
        """Export access control configuration."""
        return {
            "permissions": self.permissions,
            "user_groups": self.user_groups,
            "exceptions": self.exceptions,
            "default_deny": self.default_deny,
            "action_aliases": self.action_aliases,
            "enforce": self.enforce,
            "allow_anonymous": self.allow_anonymous,
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
                raw_groups = config.get("user_groups", config.get("session_groups", {}))
                self.user_groups = self._migrate_user_groups(raw_groups)
                self.exceptions = list(config.get("exceptions", []))
                self.default_deny = bool(config.get("default_deny", False))
                self.action_aliases = dict(config.get("action_aliases", {}))
                self.enforce = bool(config.get("enforce", True))
                self.allow_anonymous = bool(config.get("allow_anonymous", False))
            else:
                if "permissions" in config:
                    self.permissions.update(config["permissions"])
                if "user_groups" in config:
                    migrated = self._migrate_user_groups(config["user_groups"])
                    for scope, groups in migrated.items():
                        if scope in self.user_groups:
                            self.user_groups[scope].update(groups)
                        else:
                            self.user_groups[scope] = groups
                elif "session_groups" in config:
                    migrated = self._migrate_user_groups(config["session_groups"])
                    for scope, groups in migrated.items():
                        if scope in self.user_groups:
                            self.user_groups[scope].update(groups)
                        else:
                            self.user_groups[scope] = groups
                if "exceptions" in config:
                    for ex in config["exceptions"]:
                        if ex not in self.exceptions:
                            self.exceptions.append(ex)
                if "default_deny" in config:
                    self.default_deny = bool(config["default_deny"])
                if "action_aliases" in config:
                    self.action_aliases.update(config["action_aliases"])
                if "enforce" in config:
                    self.enforce = bool(config["enforce"])
                if "allow_anonymous" in config:
                    self.allow_anonymous = bool(config["allow_anonymous"])

            await self.save()
        except Exception as e:
            logger.error(f"Error importing configuration: {e}")
            raise
