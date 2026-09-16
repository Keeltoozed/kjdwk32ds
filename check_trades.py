import os
from dotenv import load_dotenv
import requests

load_dotenv()
url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")

headers = {
    "apikey": key,
    "Authorization": f"Bearer {key}"
}

res = requests.get(f"{url}/rest/v1/trades?limit=5", headers=headers)
print("trades table:", res.json())
