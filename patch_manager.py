import re

with open('main.py', 'r') as f:
    content = f.read()

new_logic = '''
                # 0. Сначала берем LIVE цену из WebSocket (если её обновил снайпер)
                ws_price = position.current_price_usd if hasattr(position, 'current_price_usd') else 0.0
                
                # 1. Пытаемся получить цену из Юпитера
                current_price = await JupiterAPI.get_price(mint)
                
                # 2. Если Юпитер слеп, пробуем DexScreener
                if current_price is None or current_price == 0.0:
                    pair_data = await analyzer.fetch_token_data(mint)
                    if pair_data:
                        current_price = float(pair_data.get("priceUsd", 0))
                        
                # 3. Если ВСЕ API слепы (токен слишком свежий), используем цену из WSS!
                if current_price is None or current_price <= 0.0:
                    if ws_price > 0.0 and ws_price != position.entry_price_usd:
                        current_price = ws_price
                    else:
                        minutes_held = (time.time() - position.entry_time) / 60
                        if minutes_held > 180:
                            tracker.close_position(mint, 0.0, "Rug Pull / No Liquidity")
                        continue
'''

content = re.sub(r'                current_price = await JupiterAPI\.get_price\(mint\).*?continue\n', new_logic.lstrip(), content, flags=re.DOTALL)

with open('main.py', 'w') as f:
    f.write(content)
