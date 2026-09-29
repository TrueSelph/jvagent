# Handoff Action (`jvagent/handoff_action`)

Human-support capability tools, **skill-gated** by the `handoff` library skill.
The tools stay on the orchestrator surface but refuse to run until a skill
declaring them in `allowed-tools` is active — so configure the orchestrator with
`skill_only_tools: ["handoff__*"]` and enable the `handoff` skill.

## Tools

| Tool | Purpose |
|---|---|
| `handoff__contact_details()` | Return office hours plus a random staff number and every staff email from `HandoffAction.staff`. No notification. |
| `handoff__staff_lookup(message, phone_numbers?, emails?)` | Notify staff about a question you cannot answer and record it as pending. Contact is optional. Non-retryable. |
| `handoff__agent_escalation(message, phone_numbers?, emails?)` | Notify staff that the customer wants a person now. Needs a contact. Non-retryable. |
| `handoff__scheduled_callback(message, phone_numbers?, emails?)` | Notify staff to reach the customer later. Needs a contact. Non-retryable. |
| `handoff__pending_questions()` | List customer questions waiting for an answer. |
| `handoff__save_answer(question_id, answer)` | Save the answer for one pending question into `handoff.md` and return a thank-you. Non-retryable. |

`handoff__staff_lookup` records the question on `HandoffAction.pending_questions`
(reloaded from the Action row before list/get, caches invalidated after write)
on the first call, before staff are notified. Staff are notified once the
customer shares a phone or email, or declines. A later call with the same
contact does not send again. When a later message looks like an answer, call
`handoff__pending_questions`, then `handoff__save_answer`. Pending questions are
shared across conversations for the same Action so staff answering in their own
thread can see customer questions.

## Channels

Each tool chooses its channel from `handoff_channels` (`whatsapp` via `handoff_notify_action_type`,
default `WhatsAppAction`) or `"email"` (via `handoff_email_action_type`, default
`EmailAction`). Set the per-mode default in the skill body; the recipient is
**one staff target chosen at random** from the configured list.

## Configuration

AccessControlAction `user_groups.HandoffAction.staff` in agent.yaml — a mixed
list of WhatsApp numbers and email addresses. That list is the staff identity
allowlist and the notify/contact target source. Members are classified as
phone or email; notify picks one matching target at random for the channel.

| Source | Role |
|---|---|
| `user_groups.HandoffAction.staff` | Staff WhatsApp numbers and emails. Any listed sender may save answers. Notify picks one matching target at random. |

PageIndex target is fixed: `doc_name="handoff.md"`,
`metadata={"access": "public"}`, written to `<files_root>/handoff.md` (default
`./.files/handoff.md`) — the file is created on first resolve and appended
cumulatively thereafter.

### Email channel setup

To use `channel="email"` you must wire outbound **and** inbound email:

1. Add `jvagent/mcp_oauth`, `jvagent/mcp` (a `google_workspace` server), and
   `jvagent/google_gmail_action` (Gmail) — or the SendGrid/Outlook equivalents.
2. Add `jvagent/email_action` with `provider: gmail` (or `sendgrid` / `outlook`).
3. Authorize: `/api/mcp/google_workspace/auth?service=gmail`.
4. Create the **email webhook** (`GET /api/actions/{action_id}/email/webhook-url`)
   and point your provider's inbound parse / poll at it (SendGrid Inbound Parse
   requires SPF+DKIM pass).
5. Set `EMAIL_DEFAULT_SENDER` (or the provider's from-address).

See [`../email_action/README.md`](../email_action/README.md) for the provider
matrix.

## Skill

The SOP lives in [`jvagent/skills/handoff/SKILL.md`](../../skills/handoff/SKILL.md)
(`allowed-tools` = these four, `requires-actions: [HandoffAction]`,
`extends: action:jvagent/handoff_action`). The action also contributes an
always-on orchestration parameter (`key: handoff_routing`) that tells the loop
when to hand off.

## Orchestrator wiring (agent.yaml)

```yaml
  - action: jvagent/orchestrator
    context:
      skill_only_tools: ["handoff__*"]   # gated until the handoff skill is active
      skills: [..., handoff]
  - action: jvagent/handoff_action
    context:
      enabled: true
```

## License

See the application-level [LICENSE](../../../../LICENSE).

## Author

**Tharick Jairam** · jvagent/handoff_action / V75 Inc.
