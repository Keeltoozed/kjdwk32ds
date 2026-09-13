import re

with open('main.py', 'r') as f:
    content = f.read()

old_logic = '''                # 0. Сначала берем LIVE цену из WebSocket (если её обновил снайпер - это работает за 0 мс!)
                ws_price = position.current_price_usd if hasattr(position, 'current_price_usd') else 0.0
                
                # 1. СНАЧАЛА берем LIVE цену из WebSocket, так как она обновляется в реальном времени!
                current_price = ws_price
                                
                # 2. Если WebSocket пуст (токен мигрировал или только что добавлен), берем цену из Raydium/Gecko
                if current_price <= 0.0:
                    current_price = bulk_prices.get(mint, 0.0)'''

new_logic = '''                # 1. Берем цену из Raydium/Gecko (запросили разом для всех)
                api_price = bulk_prices.get(mint, 0.0)
                
                # 2. Берем цену из WebSocket (если она свежая)
                # В tracker.py current_price_usd изначально равна entry_price. Нам нужно понять, обновилась ли она.
                # Если она изменилась с момента покупки, значит websocket ее обновил!
                ws_price = position.current_price_usd if hasattr(position, 'current_price_usd') else 0.0
                
                # ИСПОЛЬЗУЕМ СВЕЖУЮ ЦЕНУ:
                # Если websocket поменял цену (она не равна ровно цене входа), то верим websocket!
                # Иначе, если Raydium/Gecko вернули цену > 0, верим им.
                if ws_price > 0.0 and abs(ws_price - position.entry_price_usd) > 0.00000001:
                    current_price = ws_price
                elif api_price > 0.0:
                    current_price = api_price
                else:
                    # Если никто не вернул цену (Гецко еще не знает, ВСС еще не прислал сделку), оставляем ту, что была
                    current_price = ws_price'''

content = content.replace(old_logic, new_logic)

with open('main.py', 'w') as f:
    f.write(content)
