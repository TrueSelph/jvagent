# Access Control Action

Role-based access control with user_id-based permissions for secure agent operations.

## Overview

The Access Control Action provides user-based permission management and channel/resource permissions for jvagent applications. Identity is keyed by `user_id` only (no sessions or conversations). It supports allow/deny rules for users and user groups, per-action restriction in the InteractWalker, and programmatic + REST APIs for runtime updates. User groups are scoped by action label (e.g. `default`, `PersonaAction`), allowing different group memberships per action with automatic fallback and merge resolution.

## Features

- **User-only identity**: All permissions key off `user_id`
- **Channel/Resource permissions**: Fine-grained access by channel and action (InteractAction class name)
- **User groups**: Assign users to groups scoped by action label for permission inheritance; action-specific groups inherit from the `default` scope
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
        report: ReportInterviewSkill
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
        default:
          admins: [user_abc123, user_def456]
          support_team: [user_ghi789]
        PersonaAction:
          reviewers: [user_xyz]
      exceptions:
        - ReplyAction
        - IntroInteractAction
```

## Permission Structure

```
channel -> action_label -> allow/deny -> rules
```

- **Channel**: "default" (web), "whatsapp", etc.
- **Action label**: InteractAction class name (e.g. "PersonaAction", "ReportInterviewSkill") or "any"
- **Allow/Deny**: Deny rules checked first; allow rules grant access
- **Rules**: `{ user: "user_id" }` or `{ group: "group_name" }`; `group: "all"` matches everyone. Groups resolve per action label with `default` fallback.

## Programmatic API

```python
# User groups (scoped by action_label, defaults to "default")
await action.add_user_group("support", ["u1", "u2"])                        # default scope
await action.add_user_group("reviewers", ["u3"], action_label="PersonaAction")  # action-scoped
await action.add_user_to_group("admins", "user_abc")                        # default scope
await action.add_users_to_group("admins", ["u1", "u2"])                      # default scope
await action.add_user_to_group("reviewers", "u3", action_label="PersonaAction")
await action.remove_user_from_group("admins", "user_abc")                    # default scope
await action.remove_user_group("support")                                    # default scope
await action.remove_user_group("reviewers", action_label="PersonaAction")
groups = action.get_user_groups()                                           # full nested dict
groups = action.get_user_groups(action_label="PersonaAction")               # merged (action + default)

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
| POST | `/user_groups` | Create group (body: `{name, user_ids?, action_label?}`) |
| GET | `/user_groups?action_label=` | List groups (scoped or full nested) |
| POST | `/user_groups/{group}/users` | Add users to group (body: `{user_ids, action_label?}`) |
| DELETE | `/user_groups/{group}/users` | Remove users from group (body: `{user_ids, action_label?}`) |
| DELETE | `/user_groups/{group}` | Remove group (body: `{action_label?}`) |
| POST | `/permissions` | Add user/group to allow or deny |
| DELETE | `/permissions` | Remove user/group from allow or deny |

### Config Endpoint (PUT/PATCH)

Resolves AccessControlAction from `agent_id` (no start_node). Request body:

```json
{
  "permissions": {
    "default": {
      "any": { "deny": [], "allow": [{ "group": "all", "enabled": true }] },
      "ReportInterviewSkill": { "deny": [{ "group": "all" }], "allow": [] }
    },
    "whatsapp": {
      "any": { "deny": [], "allow": [{ "group": "all", "enabled": true }] },
      "ReportInterviewSkill": { "deny": [], "allow": [{ "group": "all", "enabled": true }] }
    }
  }
}
```

- **PUT**: Replaces permissions entirely. Preserves `user_groups` and `exceptions` (and other non-permissions fields on the node). Use `export_config` / `import_config` in code to replace `default_deny`, `action_aliases`, `enforce`, or `allow_anonymous` in one shot.
- **PATCH**: Merges permissions at channel level.

## User Groups

User groups are organized by **action label scope**, allowing different group memberships per action:

```yaml
user_groups:
  default:                    # Fallback scope — used when the action label has no entry
    admins: [user_abc123, user_def456]
    support_team: [user_ghi789]
  PersonaAction:              # Action-specific scope
    reviewers: [user_xyz]
  ReportInterviewSkill:
    analysts: [user_abc123]
```

### Resolution

When `_matches_rule` evaluates a `{ group: "admins" }` rule for action label `PersonaAction`:

1. Look up `user_groups["PersonaAction"]["admins"]`
2. Fall back to `user_groups["default"]["admins"]` if missing
3. **Merge**: action-specific and default groups are merged (action-specific wins on conflicts)

This means a user in `default.admins` is also considered an admin for `PersonaAction` unless `PersonaAction.admins` overrides it. The special group names `"all"` and `"any"` always match without looking up `user_groups`.

### Legacy Flat Format

The original `user_groups` format was flat: `{ admins: [user_abc] }`. This is auto-migrated to `{ default: { admins: [user_abc] } }` by `_migrate_user_groups()` during `import_config`. Both the programmatic API and REST endpoints accept the flat format for backward compatibility.

### Programmatic API (action_label parameter)

All group methods accept an optional `action_label` parameter (default `"default"`):

| Method | action_label behavior |
|--------|---------------------|
| `add_user_group(name, user_ids, action_label)` | Creates group under the given scope |
| `add_user_to_group(group, user_id, action_label)` | Adds user to group in the given scope |
| `add_users_to_group(group, user_ids, action_label)` | Adds users to group in the given scope |
| `remove_user_from_group(group, user_id, action_label)` | Removes user from group in the given scope |
| `remove_user_group(name, action_label)` | Deletes group from the given scope |
| `get_user_groups(action_label)` | No argument → full nested dict; with label → merged dict for that scope |

### REST API (action_label parameter)

All `user_groups` endpoints accept an `action_label` field in the request body (or query param for GET), defaulting to `"default"`:

| Endpoint | action_label source |
|----------|-------------------|
| `POST /user_groups` | Body: `{name, user_ids?, action_label?}` |
| `GET /user_groups?action_label=` | Query param (omit for full nested structure) |
| `POST /user_groups/{group}/users` | Body: `{user_ids, action_label?}` |
| `DELETE /user_groups/{group}/users` | Body: `{user_ids, action_label?}` |
| `DELETE /user_groups/{group}` | Body: `{action_label?}` |

## Integration

- **Policy applies** when the graph node is `enabled` and `enforce` is true (`policy_applies()`). If there is **no** `AccessControlAction` on the agent, interact proceeds without access checks (open by default).
- **Session gate**: After `memory.get_session()` resolves `user_id`, the walker checks resource `interact` before creating an interaction. Configure `default` / `whatsapp` / … channel keys for `interact` alongside per–class-name keys.
- **InteractWalker**: Before each foreground `InteractAction` runs, access is checked using that action’s class name. Deferred (`run_in_background`) actions are checked when queued and again before `execute` in the background runner.
- **HTTP `/interact`**: Entry access is enforced inside the walker (not on the raw request parameter alone), so `session_id`-only clients are evaluated after session resolution.
- **WhatsApp webhook**: Uses `sender` (phone) as `user_id` for channel `whatsapp`; denials emit structured logs (`access_control_denied`).
- **Duplicates**: If more than one `AccessControlAction` exists for an agent, an error is logged and the first match is used—fix the graph to a single node.

## Migration (breaking)

- **`user_groups` is now nested by action label**: The flat format `{ admins: [...] }` is auto-migrated to `{ default: { admins: [...] } }`. Existing YAML configs with flat `user_groups` continue to work via `_migrate_user_groups()`, but new configs should use the nested format.
- Empty `user_id` no longer bypasses checks when policy applies; set `allow_anonymous: true` only if you need that behavior.
- Turning off enforcement: set `enforce: false` (or disable the graph node); do not rely on old "empty user always allowed" behavior.
- Background interact actions are now subject to the same rules as foreground actions.
- Export/import round-trips `default_deny`, `action_aliases`, `enforce`, and `allow_anonymous`; merge import deduplicates `exceptions` entries.
- All group-related API methods now accept an `action_label` parameter (default `"default"`). Code calling these methods without the parameter is unaffected.

## Channel Adapters

Channels that receive requests before user resolution (e.g. WhatsApp with `sender` phone) should either:
- Resolve to `user_id` via `memory.get_session()` before the access check, or
- Use the channel-specific stable ID (e.g. phone number) as `user_id` in config.
