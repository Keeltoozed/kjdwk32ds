import re

with open('jupiter.py', 'r') as f:
    content = f.read()

old_logic = '''    async def get_prices(mints: list) -> dict:
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
                    return {}
            except Exception:
                return {}'''

new_logic = '''    async def get_prices(mints: list) -> dict:
        """
        Умный балк-запрос цен:
        1. Сначала стучимся в официальный API Raydium V3 (нет лимитов на Google Cloud, работает мгновенно).
        2. Если монета еще не мигрировала на Raydium (находится на Pump.fun), добираем цену из GeckoTerminal.
        """
        if not mints:
            return {}
            
        final_prices = {}
        missing_mints = []
        
        addresses = ",".join(mints)
        
        async with aiohttp.ClientSession() as session:
            # 1. Запрос к Raydium
            raydium_url = f"https://api-v3.raydium.io/mint/price?mints={addresses}"
            try:
                async with session.get(raydium_url, timeout=3) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        ray_data = data.get("data", {})
                        for mint in mints:
                            price = ray_data.get(mint)
                            if price is not None:
                                final_prices[mint] = float(price)
                            else:
                                missing_mints.append(mint)
                    else:
                        missing_mints = mints
            except Exception:
                missing_mints = mints
                
            # 2. Фолбэк на GeckoTerminal для оставшихся Pump.fun монет
            if missing_mints:
                missing_addresses = ",".join(missing_mints)
                gecko_url = f"https://api.geckoterminal.com/api/v2/simple/networks/solana/token_price/{missing_addresses}"
                headers = {"Accept": "application/json"}
                try:
                    async with session.get(gecko_url, headers=headers, timeout=5) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            prices = data.get("data", {}).get("attributes", {}).get("token_prices", {})
                            for mint, price in prices.items():
                                final_prices[mint] = float(price)
                except Exception:
                    pass
                    
        return final_prices'''

content = content.replace(old_logic, new_logic)

with open('jupiter.py', 'w') as f:
    f.write(content)
