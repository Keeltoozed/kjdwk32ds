import requests
res = requests.get("https://api.dexscreener.com/latest/dex/search?q=solana")
if res.status_code == 200:
    data = res.json()
    for p in data.get("pairs", [])[:5]:
        print(p.get("baseToken", {}).get("symbol"), p.get("pairCreatedAt"))
