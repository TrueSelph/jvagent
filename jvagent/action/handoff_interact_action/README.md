# Handoff Interact Action (`jvagent/handoff_interact_action`)

Detects when the user wants a human and uses a language model to choose how to help: **direct_contact** (show support email/phone), **agent_escalation** (notify a human on WhatsApp with a structured internal summary), or **scheduled_callback** (confirm follow-up and notify when contact details are present). Contact information is read from the thread where possible; if escalation or callback needs details and none are present, the user is asked to provide them.

Registered package id (namespace/action): `jvagent/handoff_interact_action`.

## License

See the application-level [LICENSE](../../../../../../LICENSE).

## Author

**Tharick Jairam** · jvagent/handoff_interact_action / V75 Inc.
