import requests

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


# Example usage
if __name__ == "__main__":
    # result = test_whatsapp_webhook(
    #     agent_id="n.Agent.91d36bd5921a46ed8759440d"
    # )
    # print(result)


    payload = {
        "body": "Hello bot. What is the meaning of life?",
        "from": "5926001234",
        "to": "5926001235",
        "name": "John Doe",
    }

    result = test_whatsapp_webhook(
        "n.Agent.9a80784867374a9498f2088c",
        # "n.Whatsapp.64e6fcd7e8524704acf91745",
        payload=payload
    )
    print("result")
    print(result)
