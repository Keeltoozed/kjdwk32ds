import re

with open('fomo_scanner.py', 'r') as f:
    content = f.read()

new_func = '''
async def fetch_geckoterminal_trending():
    """Получает реальные тренды с GeckoTerminal (как в Photon)"""
    tokens = []
    url = "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools"
    headers = {"Accept": "application/json"}
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers, timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    for pool in data.get("data", []):
                        try:
                            # GeckoTerminal хранит адрес токена в relationships
                            base_token_id = pool["relationships"]["base_token"]["data"]["id"]
                            # Формат: "solana_MintAddress"
                            mint = base_token_id.split("_")[1]
                            if mint and mint not in tokens:
                                tokens.append(mint)
                        except:
                            pass
        except Exception as e:
            print(f"Ошибка получения трендов GeckoTerminal: {e}")
    return tokens
'''

content = content.replace("async def fetch_dexscreener_trending():", new_func + "\nasync def fetch_dexscreener_trending():")

content = content.replace(
    "trending_mints = await fetch_dexscreener_trending()",
    "trending_mints = await fetch_dexscreener_trending()\n            gecko_mints = await fetch_geckoterminal_trending()\n            for m in gecko_mints:\n                if m not in trending_mints:\n                    trending_mints.append(m)"
)

with open('fomo_scanner.py', 'w') as f:
    f.write(content)
