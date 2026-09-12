import re

with open('sol_price.py', 'r') as f:
    content = f.read()

new_logic = '''
            # Используем GeckoTerminal вместо закрытого Jupiter API v2
            url = "https://api.geckoterminal.com/api/v2/simple/networks/solana/token_price/So11111111111111111111111111111111111111112"
            headers = {"Accept": "application/json"}
            async with session.get(url, headers=headers, timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    prices = data.get("data", {}).get("attributes", {}).get("token_prices", {})
                    price = float(prices.get("So11111111111111111111111111111111111111112", 0))
'''

content = re.sub(r'            # Jupiter Price API v2 — бесплатный, без ключа.*?price = float\(price_data\.get\("price", 0\)\)\n', new_logic, content, flags=re.DOTALL)

with open('sol_price.py', 'w') as f:
    f.write(content)
