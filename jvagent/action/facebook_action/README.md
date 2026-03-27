# FacebookAction

Core action for the [Facebook Graph API](https://developers.facebook.com/docs/graph-api): Page feed, Messenger (when combined with Meta webhooks), and related helpers.

## Tokens

| Concept | Where | Graph usage |
|--------|--------|-------------|
| **User** token | `access_token` / `FACEBOOK_ACCESS_TOKEN` | `GET /me`, `GET /me/accounts` (list Pages, resolve Page token) |
| **Page** token | `page_access_token` / `FACEBOOK_PAGE_ACCESS_TOKEN` | Page ID paths, Messenger, feed, posts, comments, reactions |
| **App** token | Derived as `{app-id}\|{app-secret}` inside `FacebookAPI` | App subscriptions webhook (`POST /{app-id}/subscriptions`) |

Page-scoped HTTP endpoints use `action.api()` (requires a stored Page token). **`GET .../facebook/me`** and **`GET .../facebook/pages`** use the **user** token via `discovery_api()`. **`POST .../facebook/webhook/register`** uses **`app_api()`** (app token only; no Page token required).

On **register** and **reload**, if `page_access_token` is empty but `access_token` (user) and `page_id` are set, the action loads `me/accounts`, finds the Page whose `id` matches `page_id`, saves its `access_token` as `page_access_token`, and persists the action.

## Enable in an agent

Add to `agent.yaml`:

```yaml
actions:
  - action: jvagent/facebook_action
    context:
      label: FacebookAction
      api_url: https://graph.facebook.com/v21.0/
      app_id: "${FACEBOOK_APP_ID}"
      app_secret: "${FACEBOOK_APP_SECRET}"
      page_id: "${FACEBOOK_PAGE_ID}"
      # User token: /me and /me/accounts (auto-fill Page token on register/reload)
      access_token: "${FACEBOOK_ACCESS_TOKEN}"
      # Or set explicitly after listing Pages (or rely on auto-resolve)
      # page_access_token: "${FACEBOOK_PAGE_ACCESS_TOKEN}"
      verify_token: "${FACEBOOK_VERIFY_TOKEN}"
      # Optional: webhook field subscriptions (comma-separated)
      # fields: "messages,messaging_postbacks"
```

Alternatively, omit `context` fields and set environment variables: `FACEBOOK_API_URL` (optional), `FACEBOOK_APP_SECRET`, `FACEBOOK_APP_ID`, `FACEBOOK_PAGE_ID`, `FACEBOOK_ACCESS_TOKEN`, optional `FACEBOOK_PAGE_ACCESS_TOKEN`, `FACEBOOK_VERIFY_TOKEN`, `FACEBOOK_WEBHOOK_FIELDS`. If `api_url` is unset, `FACEBOOK_GRAPH_BASE` + `FACEBOOK_GRAPH_VERSION` (default `v21.0`) or `https://graph.facebook.com/{version}/` is used.

## HTTP API (admin)

All routes require admin auth. Graph behavior depends on token permissions.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/actions/{action_id}/facebook/health` | Health check |
| GET | `/actions/{action_id}/facebook/page` | Page metadata |
| GET | `/actions/{action_id}/facebook/me` | Token principal (`fields` query; user token if set) |
| GET | `/actions/{action_id}/facebook/pages` | Pages for user token (`limit`); may persist `page_access_token` |
| GET | `/actions/{action_id}/facebook/page/posts` | Page feed (`limit`, optional `fields`) |
| GET | `/actions/{action_id}/facebook/posts/{post_id}` | Single post |
| GET | `/actions/{action_id}/facebook/posts/{post_id}/permalink` | Post permalink URL |
| GET | `/actions/{action_id}/facebook/posts/{post_id}/comments` | Comments on post (`limit` query) |
| GET | `/actions/{action_id}/facebook/posts/{post_id}/reactions` | Reactions on post |
| POST | `/actions/{action_id}/facebook/messenger/text` | Messenger text (`recipient_id`, `message`) |
| POST | `/actions/{action_id}/facebook/messenger/media` | Messenger media (`recipient_id`, `media_url`, `media_type`) |
| POST | `/actions/{action_id}/facebook/page/feed` | Post text to Page feed (`message`) |
| POST | `/actions/{action_id}/facebook/page/feed/images` | Feed with image URLs (`image_urls`, `caption`) |
| POST | `/actions/{action_id}/facebook/page/feed/videos` | Feed with video URLs (`title`, `caption`, `video_urls`) |
| POST | `/actions/{action_id}/facebook/page/feed/media` | Feed with mixed media (`caption`, `media_urls`) |
| POST | `/actions/{action_id}/facebook/posts/{post_id}/comments` | Comment on post (`message`) |
| POST | `/actions/{action_id}/facebook/comments/{comment_id}/replies` | Reply to comment (`message`) |
| POST | `/actions/{action_id}/facebook/comments/{comment_id}/replies/attachment` | Reply with `attachment_url` |
| POST | `/actions/{action_id}/facebook/comments/{comment_id}` | Edit comment (`message`) |
| POST | `/actions/{action_id}/facebook/comments/{comment_id}/like` | Like comment |
| POST | `/actions/{action_id}/facebook/webhook/register` | Register webhook (`webhook_url`; app token) |

## Code usage

```python
fb = await agent.get_action_by_type("FacebookAction")
# Page-scoped (requires page_access_token)
result = fb.api().post_message_to_page("Hello")
# User-scoped
fb.discovery_api().list_all_pages()
# App-scoped (webhook subscriptions)
fb.app_api().register_session("https://example.com/webhook")
url = await fb.download_url_to_public_url("https://example.com/file.jpg")
```

`FacebookAPI` lives in `facebook_api.py`. Use `action.api()` for Page calls, `discovery_api()` for `/me` and `/me/accounts`, and `app_api()` for app-only Graph.



## Facebook API Guide

### Step 1: Create a New App

1. Go to the [Facebook Developer Console](https://developers.facebook.com/apps/?show_reminder=true).
2. Click **Create a New App**.
3. Enter your **App Name**.
4. Under **Use Cases**, select **Others**.
5. Choose **Business** as the **Account Type**.
6. Click **Create App**.

### Step 2: Retrieve App Secret

1. In the **Sidebar**, navigate to **App Settings > Basic**.
2. Copy the **App Secret**.

### Step 3: Generate Access Token

1. Go to [Graph API Explorer](https://developers.facebook.com/tools/explorer/).
2. Under **User or Page**, select the desired **Facebook Page**.
3. Under **Permissions**, select all items under **events**, **groups**, and **pages**.
4. Click **Generate Access Token**.

### Step 4: Extend the Access Token

1. Once the token is generated, click the **Upside-down Exclamation Mark** in the access token field.
2. Open the **Access Token Tool** via the provided button.
3. Scroll to the bottom of the page and click **Extend Access Token**.

### Step 5: Save Required Credentials

Save the following, as you will need them for configuration:
  - **Page ID**
  - **App ID**
  - **Extended Access Token**


### How to update webhook

1. Click on webhook on the left sidebar
2. select page and subscribe to the field you want to use.
3. enter the webhook_url and the verify_token and click verify

or

call the register endpoint

```
curl -X 'POST' \
  'http://localhost:8000/api/actions/{facebook_action}/facebook/webhook/register' \
  -H 'accept: application/json' \
  -H 'Authorization: Bearer {token}' \
  -H 'Content-Type: application/json' \
  -d '{}'
```
