import re

with open('main.py', 'r') as f:
    content = f.read()

old_logic = '''                # 🚀 MOONBAGS: Частичная фиксация на +100% (продаем 50%)
                if pnl_pct >= 1.0 and getattr(position, "is_moonbag", False) == False:
                    tracker.partial_close_position(mint, current_price, 0.5, "Moonbag 50% (+100%)")
                    continue
                
                # Если это Moonbag (уже забрали х2), трейлинг делаем ОЧЕНЬ широким
                if getattr(position, "is_moonbag", False):
                    drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd
                    if drop_from_max >= 0.40: # Разрешаем падать на 40% от пика (пусть летит до луны)
                        tracker.close_position(mint, current_price, "Moonbag Exit (40% drop)")
                    continue
                
                # 1. Lock Profit — УБРАН (реальный avg PnL = -0.6%, убивал ракеты)
                # 2. Break-even — УБРАН (реальный avg PnL = -8.1%, проскальзывание съедало)
                
                # 3. DIAMOND HANDS TRAILING (только после +80%)
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

new_logic = '''                # 🚀 УМНЫЙ ТЕЙК-ПРОФИТ (Снижаем жадность, забираем кэш)
                # 1. Первая фиксация на +35%: продаем 50% объема
                if max_pnl_pct >= 0.35 and getattr(position, "is_moonbag", False) == False:
                    tracker.partial_close_position(mint, current_price, 0.5, "Take Profit 50% (+35%)")
                    continue
                
                # 2. ТРЕЙЛИНГ-СТОП (Динамическая фиксация)
                drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd
                
                if getattr(position, "is_moonbag", False):
                    # Если уже забрали 50%, даем оставшейся части дышать шире (ждем ракету)
                    if drop_from_max >= 0.20: 
                        tracker.close_position(mint, current_price, "Moonbag Trailing (20% drop)")
                        continue
                else:
                    # Если еще не дошли до Тейк-Профита, защищаем прибыль!
                    # Активируем трейлинг, как только монета дала +15% профита.
                    if max_pnl_pct >= 0.15:
                        if drop_from_max >= 0.10: # Шаг трейлинга 10%
                            tracker.close_position(mint, current_price, f"Smart Trailing (+{max_pnl_pct*100:.0f}% peak)")
                            continue
                            
                # 3. ЖЕСТКИЙ Stop Loss -15%
                if pnl_pct <= -0.15:
                    tracker.close_position(mint, current_price, "Hard Stop Loss (-15%)")
                    continue
                    
                # 4. Time Exit: если за 10 минут профит меньше 5% — это мертвый груз
                if minutes_held >= 10 and pnl_pct < 0.05:
                    tracker.close_position(mint, current_price, "Time-based Exit (Dead Coin)")
                    continue'''

content = content.replace(old_logic, new_logic)

with open('main.py', 'w') as f:
    f.write(content)
