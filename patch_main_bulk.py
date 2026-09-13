import re

with open('main.py', 'r') as f:
    content = f.read()

old_loop = '''            open_positions = tracker.get_open_positions()
            for mint, position in list(open_positions.items()):
                # ИСПОЛЬЗУЕМ МГНОВЕННЫЙ PRICE FETCH ОТ JUPITER (Для токенов на Raydium)
# 0. Сначала берем LIVE цену из WebSocket (если её обновил снайпер)
                ws_price = position.current_price_usd if hasattr(position, 'current_price_usd') else 0.0
                
                # 1. Пытаемся получить цену из Юпитера
                current_price = await JupiterAPI.get_price(mint)
                # DEBUG PRINT
                                
                # 2. Если Юпитер слеп, пробуем DexScreener
                if current_price is None or current_price == 0.0:
                    pair_data = await analyzer.fetch_token_data(mint)
                    if pair_data:
                        current_price = float(pair_data.get("priceUsd", 0))
                        
                # 3. Если ВСЕ API слепы (токен слишком свежий), используем цену из WSS!
                if current_price is None or current_price <= 0.0:'''

new_loop = '''            open_positions = tracker.get_open_positions()
            
            # ОПТИМИЗАЦИЯ СКОРОСТИ: Запрашиваем цены для ВСЕХ позиций ОДНИМ запросом
            mints_to_fetch = list(open_positions.keys())
            bulk_prices = await JupiterAPI.get_prices(mints_to_fetch) if mints_to_fetch else {}
            
            for mint, position in list(open_positions.items()):
                # 0. Сначала берем LIVE цену из WebSocket (если её обновил снайпер - это работает за 0 мс!)
                ws_price = position.current_price_usd if hasattr(position, 'current_price_usd') else 0.0
                
                # 1. Берем цену из кэша bulk-запроса Юпитера
                current_price = bulk_prices.get(mint, 0.0)
                                
                # 2. Если Юпитер слеп, используем цену из WSS (Fallback). 
                # Мы БОЛЬШЕ НЕ делаем DexScreener запросы внутри цикла, так как они блокируют проверку стопов на 2-3 секунды!
                if current_price <= 0.0:'''

content = content.replace(old_loop, new_loop)

# Replace DexScreener removal leftovers
content = content.replace('''                    if ws_price > 0.0 and ws_price != position.entry_price_usd:
                        current_price = ws_price
                    else:
                        minutes_held = (time.time() - position.entry_time) / 60
                        if minutes_held > 180:
                            tracker.close_position(mint, 0.0, "Rug Pull / No Liquidity")
                        continue''', '''                    if ws_price > 0.0:
                        current_price = ws_price
                    else:
                        minutes_held = (time.time() - position.entry_time) / 60
                        if minutes_held > 180:
                            tracker.close_position(mint, 0.0, "Rug Pull / No Liquidity")
                        continue''')

# Reduce sleep time from 3 to 0.5
content = content.replace('await asyncio.sleep(3) # ПРОБЛЕМА РЕШЕНА: Проверяем стопы каждые 3 секунды', 'await asyncio.sleep(0.5) # МИКРОСЕКУНДНЫЙ ТРЕКИНГ: Проверяем стопы каждые 0.5 сек для мгновенных экзитов!')

with open('main.py', 'w') as f:
    f.write(content)
