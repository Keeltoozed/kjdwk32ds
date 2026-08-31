import aiohttp

class JupiterAPI:
    @staticmethod
    async def get_price(mint: str) -> float:
        """
        Получает кристально точную цену токена в USD через Jupiter Price API v2.
        Это позволяет моментально реагировать на изменение цены для TP/SL.
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
                print(f"Jupiter API Error: {e}")
        return 0.0
