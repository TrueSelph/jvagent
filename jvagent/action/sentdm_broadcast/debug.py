import requests
import time

API_KEY = "ec07d41e-7fd2-42ca-9c70-55ddf84a17f0"
SENDER_ID="5311d08c-f79a-41b1-8ebb-1f0efaefdf8f"
BASE_URL = "https://api.sent.dm/v3"
HEADERS = {
    "Content-Type": "application/json",
    "x-api-key": API_KEY,
    "x-sender-id": SENDER_ID
}

# --- Step 1: Send the message ---
send_url = f"{BASE_URL}/messages"
body = {
    "to": ["+5926767191"],
    "channel": ["whatsapp"],
    "template": {
        "id": "2c2d9c56-4124-4b0e-ba18-fe4570542c2c",
        "parameters": {
            "var_1": "var_1",
            "var_2": "var_2"
        }
    },
    "sandbox": False
}

send_response = requests.post(
    send_url,
    json=body,
    headers={**HEADERS, "Idempotency-Key": "req_abc123_retry1"}
)
send_data = send_response.json()
print("Send response:", send_data)

if not send_data.get("success"):
    raise SystemExit("Send failed, aborting status check.")

message_id = send_data["data"]["recipients"][0]["message_id"]
print(f"\nMessage ID: {message_id}")

# --- Step 2: Check status ---
# Optional small delay since the message needs a moment to move past QUEUED
time.sleep(2)

status_url = f"{BASE_URL}/messages/{message_id}"
status_response = requests.get(status_url, headers=HEADERS)
status_data = status_response.json()

print("\nStatus response:", status_data)

if status_data.get("success"):
    print(f"\nCurrent status: {status_data['data']['status']}")
    print("Event history:")
    for event in status_data["data"].get("events", []):
        print(f"  {event['timestamp']} — {event['status']}: {event['description']}")