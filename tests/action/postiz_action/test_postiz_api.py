import httpx
import asyncio

API_KEY = "6429088b0c19ed59a75c8f43bbf367cd3a86d5995a14a012f492f5bafe20cf61"
# BASE_URL = "http://localhost:4007/api/public/v1"
# Try both /api/public/v1 and just /public/v1 depending on how the proxy is set up
BASE_URL = "http://localhost:4007/api/public/v1"

async def test_postiz():
    headers = {
        "Authorization": f"Bearer {API_KEY}", # Postiz docs say API Key in Authorization header, might need Bearer or not.
        "Content-Type": "application/json"
    }
    
    async with httpx.AsyncClient(timeout=10) as client:
        print(f"Testing GET {BASE_URL}/integrations...")
        try:
            # First try with Bearer
            response = await client.get(f"{BASE_URL}/integrations", headers=headers)
            if response.status_code == 401:
                print("Unauthorized with Bearer, trying without Bearer prefix...")
                headers["Authorization"] = API_KEY
                response = await client.get(f"{BASE_URL}/integrations", headers=headers)
            
            print(f"Status Code: {response.status_code}")
            if response.status_code == 200:
                print("Success!")
                print("Integrations:")
                import json
                print(json.dumps(response.json(), indent=2))
            else:
                print(f"Error: {response.text}")
        except Exception as e:
            print(f"Exception: {e}")

if __name__ == "__main__":
    asyncio.run(test_postiz())
