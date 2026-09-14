import re

with open('main.py', 'r') as f:
    content = f.read()

old_logic = '''                # 3. DIAMOND HANDS TRAILING (только после +80%)
                drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd
                
                if max_pnl_pct >= 0.80:
                    if drop_from_max >= 0.25: 
                        tracker.close_position(mint, current_price, "Diamond Hand Trailing (25% drop)")
                        continue'''

new_logic = '''                # 3. ДИНАМИЧЕСКИЙ ТРЕЙЛИНГ-СТОП (Умная фиксация прибыли)
                drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd
                
                # Уровень 1: Ракета (Профит > 80%). Даем монете дышать, разрешаем откат 25%
                if max_pnl_pct >= 0.80:
                    if drop_from_max >= 0.25: 
                        tracker.close_position(mint, current_price, "Trailing Stop (Profit >80%, 25% drop)")
                        continue
                        
                # Уровень 2: Средний памп (Профит от 35% до 80%). Разрешаем откат 15%
                elif max_pnl_pct >= 0.35:
                    if drop_from_max >= 0.15:
                        tracker.close_position(mint, current_price, "Trailing Stop (Profit >35%, 15% drop)")
                        continue
                        
                # Уровень 3: Защита первого профита (Профит от 15% до 35%). Разрешаем откат всего 6%
                # Если монета дала +15%, мы уже НИКОГДА не закроем ее в минус. Мы заберем хотя бы +9%.
                elif max_pnl_pct >= 0.15:
                    if drop_from_max >= 0.06:
                        tracker.close_position(mint, current_price, "Trailing Stop (Profit >15%, 6% drop)")
                        continue'''

content = content.replace(old_logic, new_logic)

old_time_exit = '''                # 5. Time Exit: 10 минут вместо 30 (было 26 сделок с avg -7.3%)
                if minutes_held >= 10 and pnl_pct < 0.05:
                    tracker.close_position(mint, current_price, "Time-based Exit (Dead Coin)")
                    continue'''

new_time_exit = '''                # 5. Time Exit: Застыла на месте (Dead Coin)
                # Если прошло 4 минуты, а монета не дала хотя бы +7% - сбрасываем балласт. На Pump.fun ракеты взлетают за первые 3 минуты.
                if minutes_held >= 4 and pnl_pct < 0.07:
                    tracker.close_position(mint, current_price, "Time-based Exit (Stalled > 4m)")
                    continue'''

content = content.replace(old_time_exit, new_time_exit)

with open('main.py', 'w') as f:
    f.write(content)
