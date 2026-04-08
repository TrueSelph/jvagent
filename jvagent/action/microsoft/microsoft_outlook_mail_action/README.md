# Microsoft Outlook Mail Action

Send mail and list messages in the signed-in user’s mailbox via **Microsoft Graph** (`/me/sendMail`, `/me/messages`), with **Entra ID** OAuth (PKCE) handled by `MicrosoftAction`.

Shared setup (environment variables, redirect URI, OAuth pages) is documented in the [Microsoft actions README](../README.md).

## Microsoft Graph scopes

Delegated scopes requested at sign-in:

- `offline_access`
- `User.Read`
- `Mail.Read`
- `Mail.Send`

Grant matching **API permissions** in Entra ID for your app registration.

## Agent wiring (`agent.yaml`)

```yaml
- action: jvagent/microsoft_outlook_mail_action
```

No action-level OAuth secrets: use `MICROSOFT_CLIENT_ID`, optional `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_TENANT_ID`, and `JVAGENT_PUBLIC_BASE_URL` as described in the parent README. After deploy, open the action’s **`auth_url`** once to complete consent.

## REST API (unified mail)

Paths assume the default `/api` prefix. Routes require authenticated **admin** access (see OpenAPI for auth).

| Method | Path | Description |
| ------ | ---- | ----------- |
| GET | `/api/microsoft/{action_id}` | OAuth landing HTML (“Sign in with Microsoft”) |
| POST | `/api/actions/{action_id}/send` | Send email: `to`, `subject`, `body`; optional `user_id` (ignored; uses `/me`) |
| GET | `/api/actions/{action_id}/list` | List messages: `query` (Graph `$search`), `max_results`, optional `user_id` |
| GET | `/api/actions/{action_id}/profile` | Profile: `emailAddress`, `displayName` from `/me` |

### List response shape

Each message entry includes `id` and `threadId` (Graph `conversationId`).

### Send behavior

Body is sent as **plain text** (`contentType: Text`). Messages are saved to Sent Items.
