# HeyGen Video Action

An action for generating videos using the HeyGen API.

## Overview

This package provides `HeygenVideoAction`, an `Action` subclass that wraps
HeyGen's video generation endpoint.  It can be registered with an agent and
invoked from other actions or code to produce videos from a plain text script.

Configuration:

* `api_key` – HeyGen API key (can also be supplied via `HEYGEN_API_KEY`
  environment variable if desired)
* `api_base` – Base URL for the HeyGen API (defaults to `https://api.heygen.com/v1`)
* `timeout` – HTTP request timeout in seconds

## Usage

Register the action in your `agent.yaml`:

```yaml
actions:
  - action: jvagent/heygen_video
    api_key: "${HEYGEN_API_KEY}"
```

Then, from another action, you can obtain and call it:

```python
video_action = await self.get_action("HeygenVideoAction")
resp = await video_action.create_video("Hello world", voice="en_us_female")
url = resp.get("video_url") if resp else None
```

The response object is the raw JSON returned by HeyGen.  Inspect the provider
API docs for details on available parameters.
