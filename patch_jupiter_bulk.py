import re

with open('jupiter.py', 'r') as f:
    content = f.read()

old_get_price = '''    @staticmethod
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
        return 0.0'''

new_get_price = '''    @staticmethod
    async def get_prices(mints: list) -> dict:
        """
        Балк-запрос цен для нескольких токенов через GeckoTerminal.
        Значительно ускоряет цикл трекинга позиций, избавляя от последовательных HTTP-запросов.
        """
        if not mints:
            return {}
            
        addresses = ",".join(mints)
        url = f"https://api.geckoterminal.com/api/v2/simple/networks/solana/token_price/{addresses}"
        headers = {"Accept": "application/json"}
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=headers, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        prices = data.get("data", {}).get("attributes", {}).get("token_prices", {})
                        return {mint: float(price) for mint, price in prices.items()}
            except Exception:
                pass
        return {}

    @staticmethod
    async def get_price(mint: str) -> float:
        prices = await JupiterAPI.get_prices([mint])
        return prices.get(mint, 0.0)'''

content = content.replace(old_get_price, new_get_price)

with open('jupiter.py', 'w') as f:
    f.write(content)
