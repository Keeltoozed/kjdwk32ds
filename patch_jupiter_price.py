import re

with open('jupiter.py', 'r') as f:
    content = f.read()

new_get_price = '''    @staticmethod
    async def get_price(mint: str) -> float:
        """
        Получает кристально точную цену токена в USD через GeckoTerminal API.
        (Jupiter v2 закрыл публичный бесплатный доступ).
        """
        url = f"https://api.geckoterminal.com/api/v2/simple/networks/solana/token_price/{mint}"
        headers = {"Accept": "application/json"}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=headers, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        prices = data.get("data", {}).get("attributes", {}).get("token_prices", {})
                        if mint in prices:
                            return float(prices[mint])
            except Exception:
                pass
        return 0.0
'''

# We need to replace the old get_price.
content = re.sub(r'    @staticmethod\n    async def get_price\(mint: str\) -> float:.*?return 0\.0\n', new_get_price, content, flags=re.DOTALL)

with open('jupiter.py', 'w') as f:
    f.write(content)
