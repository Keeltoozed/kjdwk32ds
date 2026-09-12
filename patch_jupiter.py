import re

with open('jupiter.py', 'r') as f:
    content = f.read()

new_func = '''
    @staticmethod
    async def get_price(mint: str) -> float:
        """
        Получает кристально точную цену токена в USD через Jupiter Price API v2.
        """
        url = f"https://api.jup.ag/price/v2?ids={mint}"
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        price_str = data.get("data", {}).get(mint, {}).get("price")
                        if price_str:
                            return float(price_str)
            except Exception as e:
                pass
        return 0.0
'''

content = re.sub(r'    @staticmethod\n    async def get_price\(mint: str\) -> float:.*?(?=\n    @staticmethod|\Z)', new_func.strip() + "\n", content, flags=re.DOTALL)

with open('jupiter.py', 'w') as f:
    f.write(content)
