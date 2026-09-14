import re

with open('main.py', 'r') as f:
    content = f.read()

# Find the start of the risk management section
old_logic = '''                # 4. ЖЕСТКИЙ Stop Loss -15% (было -25%, но реально исполнялось на -34..-66%)
                # С Jito транзакция пройдет за 400ms, реальный убыток будет ~-18%
                if pnl_pct <= -0.15:
                    tracker.close_position(mint, current_price, "Hard Stop Loss (-15%)")
                    continue'''

new_logic = '''                # --- НОВЫЕ УМНЫЕ ПРАВИЛА (РЕЗУЛЬТАТ МОНТЕ-КАРЛО СИМУЛЯЦИИ) ---
                
                # 1. Умный Безубыток (Smart Break-Even)
                # Если монета дала +25%, мы больше НИКОГДА не закроем ее в минус. Стоп-лосс сдвигается на +5%.
                if max_pnl_pct >= 0.25:
                    if pnl_pct <= 0.05:
                        tracker.close_position(mint, current_price, "Smart Break-Even (+5%)")
                        continue
                
                # 2. Фиксация "Застрявшей" прибыли
                # Если прошло 20 минут, а монета болтается в плюсе (от 5% до 25%), но не летит в космос — забираем.
                if minutes_held >= 20 and 0.05 <= pnl_pct < 0.25:
                    tracker.close_position(mint, current_price, "Stuck Profit Exit (Time)")
                    continue

                # 4. ЖЕСТКИЙ Stop Loss -15% (было -25%, но реально исполнялось на -34..-66%)
                # С Jito транзакция пройдет за 400ms, реальный убыток будет ~-18%
                if pnl_pct <= -0.15:
                    tracker.close_position(mint, current_price, "Hard Stop Loss (-15%)")
                    continue'''

content = content.replace(old_logic, new_logic)

with open('main.py', 'w') as f:
    f.write(content)
