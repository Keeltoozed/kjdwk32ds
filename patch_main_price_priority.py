import re

with open('main.py', 'r') as f:
    content = f.read()

old_logic = '''                # 1. Берем цену из кэша bulk-запроса Юпитера
                current_price = bulk_prices.get(mint, 0.0)
                                
                # 2. Если Юпитер слеп, используем цену из WSS (Fallback). 
                # Мы БОЛЬШЕ НЕ делаем DexScreener запросы внутри цикла, так как они блокируют проверку стопов на 2-3 секунды!
                if current_price <= 0.0:
                    if ws_price > 0.0:
                        current_price = ws_price
                    else:
                        minutes_held = (time.time() - position.entry_time) / 60'''

new_logic = '''                # 1. СНАЧАЛА берем LIVE цену из WebSocket, так как она обновляется в реальном времени!
                current_price = ws_price
                                
                # 2. Если WebSocket пуст (токен мигрировал или только что добавлен), берем цену из Raydium/Gecko
                if current_price <= 0.0:
                    current_price = bulk_prices.get(mint, 0.0)
                    
                if current_price <= 0.0:
                    minutes_held = (time.time() - position.entry_time) / 60'''

content = content.replace(old_logic, new_logic)

with open('main.py', 'w') as f:
    f.write(content)
