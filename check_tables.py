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

resp = requests.get(f"{url}/rest/v1/", headers=headers)
if resp.status_code == 200:
    data = resp.json()
    tables = data.get('definitions', {}).keys()
    print("Tables:", list(tables))
else:
    print(resp.text)
