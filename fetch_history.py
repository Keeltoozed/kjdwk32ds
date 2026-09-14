import requests
import json

mint = "Ekdk4tWyz2dG4SsrKgCSMRHZxNGAgPs6jUxoJWAppump"
url = f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}/ohlcv/minute?limit=60"
headers = {"Accept": "application/json"}
resp = requests.get(url, headers=headers)
print(resp.status_code)
if resp.status_code == 200:
    data = resp.json()
    print("Has data:", "data" in data)
