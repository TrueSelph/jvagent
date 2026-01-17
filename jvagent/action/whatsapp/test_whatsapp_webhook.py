import requests

BASE_URL = "http://localhost:8000"
EMAIL = "admin@jvagent.example"
PASSWORD = "your-admin-password-here"
TARGETED_AGENT = "resolv_demo"
SELECTED_AGENT = None


def authenticate(email: str, password: str) -> str:
    endpoint = f"{BASE_URL}/auth/login"
    payload = {"email": email, "password": password}
    response = requests.post(endpoint, json=payload)
    if response.status_code != 200:
        raise Exception(f"Failed to authenticate: {response.text}")
    return response.json()["access_token"]


def list_agents(token: str) -> list:
    endpoint = f"{BASE_URL}/api/agents?page=1&per_page=10"
    headers = {"accept": "application/json", "Authorization": f"Bearer {token}"}
    response = requests.get(endpoint, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Failed to list agents: {response.text}")
    return response.json()["agents"]


def test_whatsapp_webhook(
    agent_id: str,
    # action_id: str,
    base_url: str = "http://localhost:8000",
    payload: dict | None = None,
    timeout: int = 30,
):
    """
    Test the WhatsApp webhook endpoint.

    :param agent_id: Agent ID in the path (e.g. n.Agent.91d36bd5921a46ed8759440d)
    :param base_url: Base API URL
    :param payload: Optional JSON payload to send
    :param timeout: Request timeout in seconds
    :return: Response JSON
    """

    url = f"{base_url}/api/whatsapp/interact/webhook/{agent_id}"

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload or {},
        timeout=timeout,
    )

    response.raise_for_status()  # fail fast if not 2xx

    return response.json()


############ token ############
token = authenticate(EMAIL, PASSWORD)
############ agents ############
agents = list_agents(token)

for agent in agents:
    if agent["context"]["name"] == TARGETED_AGENT:
        SELECTED_AGENT = agent
        break
############ test_whatsapp_webhook ############


payload = {
    "body": "What did I just ask you?",
    "from": "5926001234",
    "to": "5926001235",
    "name": "John Doe",
}

result = test_whatsapp_webhook(
    SELECTED_AGENT["id"],
    # "n.Whatsapp.64e6fcd7e8524704acf91745",
    payload=payload,
)
print("result")
print(result)
