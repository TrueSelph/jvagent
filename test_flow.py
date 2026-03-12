import json

from google_auth_oauthlib.flow import Flow

client_config = {
    "web": {
        "client_id": "test_client_id",
        "client_secret": "test_client_secret",  # pragma: allowlist secret
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}

try:
    flow = Flow.from_client_config(
        client_config,
        scopes=["https://www.googleapis.com/auth/drive"],
        redirect_uri="http://localhost:8080/",
    )
    url, _ = flow.authorization_url()
    print("Success:", url)
except Exception as e:
    print("Error:", e)
