import re

with open('pump_fun_sniper.py', 'r') as f:
    content = f.read()

new_logic = '''
                            # Обновляем LIVE цену в трекере, если мы в позиции!
                            if state.is_entered:
                                from tracker import PaperTracker
                                from sol_price import get_sol_price_sync
                                p_tracker = PaperTracker()
                                pos = p_tracker.positions.get(mint)
                                if pos and pos.status == "open":
                                    live_price = (sol_amount / 1_000_000_000.0) * get_sol_price_sync()
                                    pos.current_price_usd = live_price
                                    if live_price > pos.max_price_usd:
                                        pos.max_price_usd = live_price
                                    # Рассчитываем PNL для логов (Stop-Loss все равно сработает в главном цикле, но быстрее)
                                    pnl_pct = (live_price - pos.entry_price_usd) / pos.entry_price_usd
                                    pos.current_pnl_usd = pos.amount_usd * pnl_pct
                                    p_tracker.save_portfolio()
                                    
                            # Если достигли 20%, оцениваем ИИ
'''

content = content.replace("# Если достигли 20%, оцениваем ИИ", new_logic.strip())

with open('pump_fun_sniper.py', 'w') as f:
    f.write(content)
