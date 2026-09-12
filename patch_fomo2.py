import re

with open('fomo_scanner.py', 'r') as f:
    content = f.read()

new_func = '''
async def fetch_pumpfun_top():
    """Получает топ монет Pump.fun по капе (близкие к миграции на Raydium)"""
    tokens = []
    url = "https://frontend-api.pump.fun/coins?offset=0&limit=20&sort=market_cap&order=DESC&includeNsfw=false"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers, timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    for coin in data:
                        mint = coin.get("mint")
                        if mint and mint not in tokens:
                            tokens.append(mint)
        except Exception as e:
            print(f"Ошибка получения топ-монет Pump.fun: {e}")
    return tokens
'''

content = content.replace("async def fetch_dexscreener_trending():", new_func + "\nasync def fetch_dexscreener_trending():")

content = content.replace(
    "gecko_mints = await fetch_geckoterminal_trending()",
    "gecko_mints = await fetch_geckoterminal_trending()\n            pump_mints = await fetch_pumpfun_top()\n            for m in pump_mints:\n                if m not in trending_mints:\n                    trending_mints.append(m)"
)

with open('fomo_scanner.py', 'w') as f:
    f.write(content)
