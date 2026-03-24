# Access Control Action

Role-based access control with user_id-based permissions for secure agent operations.

## Overview

The Access Control Action provides user-based permission management and channel/resource permissions for jvagent applications. Identity is keyed by `user_id` only (no sessions or conversations). It supports allow/deny rules for users and user groups, per-action restriction in the InteractWalker, and programmatic + REST APIs for runtime updates.

## Features

- **User-only identity**: All permissions key off `user_id`
- **Channel/Resource permissions**: Fine-grained access by channel and action (InteractAction class name)
- **User groups**: Assign users to groups for permission inheritance
- **Allow/Deny rules**: Deny rules checked first; allow rules grant access
- **default_deny**: When true, deny when no rule matches; when false, allow
- **action_aliases**: Map short names to action class names in config
- **enforce**: When false, all `has_action_access` checks succeed (policy off). Prefer this over disabling the graph node when you want the action to remain discoverable.
- **allow_anonymous**: When false (default), empty/missing `user_id` is denied whenever policy applies. Set true only if anonymous interact is intentional.
- **Exceptions**: Actions exempt from permission checks
- **Programmatic API**: Add/remove users, groups, and permission rules
- **Admin REST endpoints**: All agent-scoped via `agent_id` (singleton per agent)

## Agent YAML Configuration

Configure directly under `context` (not under `config`). This ensures attributes are
populated correctly when running on Lambda and other serverless environments.

```yaml
actions:
  - action: jvagent/access_control_action
    context:
      enabled: true
      enforce: true
      allow_anonymous: false
      description: "Per-user/group access control for interact actions"
      default_deny: false
      action_aliases:
        persona: PersonaAction
        report: ReportInterviewInteractAction
      permissions:
        default:
          any:
            deny: []
            allow: [{ group: all, enabled: true }]
          PersonaAction:
            deny: []
            allow: [admins]
        whatsapp:
          any:
            deny: []
            allow: [{ user: "5926431530", enabled: true }]
      user_groups:
        admins: [user_abc123, user_def456]
        support_team: [user_ghi789]
      exceptions:
        - ConverseInteractAction
        - IntroInteractAction
```

## Permission Structure

```
channel -> action_label -> allow/deny -> rules
```

- **Channel**: "default" (web), "whatsapp", etc.
- **Action label**: InteractAction class name (e.g. "PersonaAction", "ReportInterviewInteractAction") or "any"
- **Allow/Deny**: Deny rules checked first; allow rules grant access
- **Rules**: `{ user: "user_id" }` or `{ group: "group_name" }`; `group: "all"` matches everyone

## Programmatic API

```python
# User groups
await action.add_user_group("support", ["u1", "u2"])
await action.add_user_to_group("admins", "user_abc")
await action.add_users_to_group("admins", ["u1", "u2"])
await action.remove_user_from_group("admins", "user_abc")
await action.remove_user_group("support")
groups = action.get_user_groups()

# Direct user access (allow/deny)
await action.add_user_to_allow("default", "PersonaAction", "user_xyz")
await action.add_user_to_deny("default", "DeleteAction", "user_bad")
await action.remove_user_from_permission("default", "X", "user_id", from_allow=True)

# Group-based rules
await action.add_group_to_allow("default", "PersonaAction", "admins")
await action.add_group_to_deny("default", "DeleteAction", "guests")
await action.remove_group_from_permission("default", "X", "group", from_allow=True)

# Check access
has_access = await action.has_action_access(
    user_id="user_abc",
    action_label="PersonaAction",
    channel="default",
)

# Bulk config (includes default_deny, action_aliases, enforce, allow_anonymous)
config = action.export_config()
await action.import_config(config, purge=True)
```

## REST API (Admin Only)

All endpoints require `auth=True` and `roles=["admin"]`. AccessControlAction is a
singleton per agent, so all endpoints are agent-scoped via `agent_id`.

### Endpoints (`/agents/{agent_id}/access_control/`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/config?format=json\|yaml` | Export config |
| PUT | `/config` | Replace permissions |
| PATCH | `/config` | Merge permissions |
| POST | `/check` | Check access (user_id, action_label, channel) |
| POST | `/user_groups` | Create group (body: `{name, user_ids?}`) |
| GET | `/user_groups` | List groups |
| POST | `/user_groups/{group}/users` | Add users to group |
| DELETE | `/user_groups/{group}/users` | Remove users from group |
| DELETE | `/user_groups/{group}` | Remove group |
| POST | `/permissions` | Add user/group to allow or deny |
| DELETE | `/permissions` | Remove user/group from allow or deny |

### Config Endpoint (PUT/PATCH)

Resolves AccessControlAction from `agent_id` (no start_node). Request body:

```json
{
  "permissions": {
    "default": {
      "any": { "deny": [], "allow": [{ "group": "all", "enabled": true }] },
      "ReportInterviewInteractAction": { "deny": [{ "group": "all" }], "allow": [] }
    },
    "whatsapp": {
      "any": { "deny": [], "allow": [{ "group": "all", "enabled": true }] },
      "ReportInterviewInteractAction": { "deny": [], "allow": [{ "group": "all", "enabled": true }] }
    }
  }
}
```

- **PUT**: Replaces permissions entirely. Preserves `user_groups` and `exceptions` (and other non-permissions fields on the node). Use `export_config` / `import_config` in code to replace `default_deny`, `action_aliases`, `enforce`, or `allow_anonymous` in one shot.
- **PATCH**: Merges permissions at channel level.

## Integration

- **Policy applies** when the graph node is `enabled` and `enforce` is true (`policy_applies()`). If there is **no** `AccessControlAction` on the agent, interact proceeds without access checks (open by default).
- **Session gate**: After `memory.get_session()` resolves `user_id`, the walker checks resource `interact` before creating an interaction. Configure `default` / `whatsapp` / … channel keys for `interact` alongside per–class-name keys.
- **InteractWalker**: Before each foreground `InteractAction` runs, access is checked using that action’s class name. Deferred (`run_in_background`) actions are checked when queued and again before `execute` in the background runner.
- **HTTP `/interact`**: Entry access is enforced inside the walker (not on the raw request parameter alone), so `session_id`-only clients are evaluated after session resolution.
- **WhatsApp webhook**: Uses `sender` (phone) as `user_id` for channel `whatsapp`; denials emit structured logs (`access_control_denied`).
- **Duplicates**: If more than one `AccessControlAction` exists for an agent, an error is logged and the first match is used—fix the graph to a single node.

## Migration (breaking)

- Empty `user_id` no longer bypasses checks when policy applies; set `allow_anonymous: true` only if you need that behavior.
- Turning off enforcement: set `enforce: false` (or disable the graph node); do not rely on old “empty user always allowed” behavior.
- Background interact actions are now subject to the same rules as foreground actions.
- Export/import round-trips `default_deny`, `action_aliases`, `enforce`, and `allow_anonymous`; merge import deduplicates `exceptions` entries.

## Channel Adapters

Channels that receive requests before user resolution (e.g. WhatsApp with `sender` phone) should either:
- Resolve to `user_id` via `memory.get_session()` before the access check, or
- Use the channel-specific stable ID (e.g. phone number) as `user_id` in config.
