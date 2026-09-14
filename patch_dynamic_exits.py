import re

with open('main.py', 'r') as f:
    content = f.read()

old_logic = '''                # 3. DIAMOND HANDS TRAILING (только после +80%)
                drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd
                
                if max_pnl_pct >= 0.80:
                    if drop_from_max >= 0.25: 
                        tracker.close_position(mint, current_price, "Diamond Hand Trailing (25% drop)")
                        continue
                
                # 4. ЖЕСТКИЙ Stop Loss -15% (было -25%, но реально исполнялось на -34..-66%)
                # С Jito транзакция пройдет за 400ms, реальный убыток будет ~-18%
                if pnl_pct <= -0.15:
                    tracker.close_position(mint, current_price, "Hard Stop Loss (-15%)")
                    continue
                    
                # 5. Time Exit: 10 минут вместо 30 (было 26 сделок с avg -7.3%)
                if minutes_held >= 10 and pnl_pct < 0.05:
                    tracker.close_position(mint, current_price, "Time-based Exit (Dead Coin)")
                    continue'''

new_logic = '''                # 3. ДИНАМИЧЕСКИЙ ТРЕЙЛИНГ (Фиксация прибыли при падении с пика)
                drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd
                
                if max_pnl_pct >= 0.80:
                    if drop_from_max >= 0.25: 
                        tracker.close_position(mint, current_price, "Trailing: Упустили Туземун (25% drop)")
                        continue
                elif max_pnl_pct >= 0.40:
                    if drop_from_max >= 0.15: # Дошли до +40%, разрешаем упасть только на 15% от пика
                        tracker.close_position(mint, current_price, "Trailing: Средний профит (15% drop)")
                        continue
                elif max_pnl_pct >= 0.15:
                    if drop_from_max >= 0.08: # Дошли до +15%, шаг влево шаг вправо - продаем (защита микро-плюсов)
                        tracker.close_position(mint, current_price, "Trailing: Быстрый профит (8% drop)")
                        continue
                        
                # 3.5 ЗАЩИТА ОТ БЕЗУБЫТКА (Если были в хорошем плюсе, но падаем к цене входа)
                if max_pnl_pct >= 0.20 and pnl_pct <= 0.02:
                    tracker.close_position(mint, current_price, "Break-even: Защита профита")
                    continue
                
                # 4. ЖЕСТКИЙ Stop Loss -15%
                if pnl_pct <= -0.15:
                    tracker.close_position(mint, current_price, "Hard Stop Loss (-15%)")
                    continue
                    
                # 5. УМНЫЙ Time Exit (Застой монеты)
                if minutes_held >= 10:
                    if pnl_pct >= 0.10:
                        # Если монета застыла на 10 минут, но мы в плюсе >10% — забираем деньги!
                        tracker.close_position(mint, current_price, "Time-based: Фиксация застоя (+10%)")
                        continue
                    elif pnl_pct < 0.05:
                        # Если болтается около нуля или в микро-минусе — рубим
                        tracker.close_position(mint, current_price, "Time-based Exit (Dead Coin)")
                        continue'''

content = content.replace(old_logic, new_logic)

with open('main.py', 'w') as f:
    f.write(content)
