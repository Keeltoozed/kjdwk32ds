import re

with open('jupiter.py', 'r') as f:
    content = f.read()

old_logic = '''        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=headers, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        prices = data.get("data", {}).get("attributes", {}).get("token_prices", {})
                        return {mint: float(price) for mint, price in prices.items()}
            except Exception:
                pass'''

new_logic = '''        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=headers, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        prices = data.get("data", {}).get("attributes", {}).get("token_prices", {})
                        return {mint: float(price) for mint, price in prices.items()}
                    return {}
            except Exception:
                return {}'''

content = content.replace(old_logic, new_logic)

with open('jupiter.py', 'w') as f:
    f.write(content)
