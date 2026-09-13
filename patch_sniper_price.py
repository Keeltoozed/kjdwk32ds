import re

with open('pump_fun_sniper.py', 'r') as f:
    content = f.read()

old_logic = '''                            # Обновляем LIVE цену в трекере, если мы в позиции!
                            if state.is_entered:
                                from tracker import PaperTracker
                                from sol_price import get_sol_price_sync
                                p_tracker = self.tracker if self.tracker else PaperTracker()
                                pos = p_tracker.positions.get(mint)
                                if pos and pos.status == "open":
                                    live_price = (sol_amount / 1_000_000_000.0) * get_sol_price_sync()
                                    pos.current_price_usd = live_price'''

new_logic = '''                            # Обновляем LIVE цену в трекере, если мы в позиции!
                            if state.is_entered:
                                from tracker import PaperTracker
                                from sol_price import get_sol_price_sync
                                p_tracker = self.tracker if self.tracker else PaperTracker()
                                pos = p_tracker.positions.get(mint)
                                if pos and pos.status == "open":
                                    # ИСПРАВЛЕНИЕ: берем marketCapSol, а не vSolInBondingCurve, для правильного расчета цены!
                                    market_cap_sol = data.get("marketCapSol", 0)
                                    if market_cap_sol > 0:
                                        live_price = (market_cap_sol / 1_000_000_000.0) * get_sol_price_sync()
                                        pos.current_price_usd = live_price'''

content = content.replace(old_logic, new_logic)

with open('pump_fun_sniper.py', 'w') as f:
    f.write(content)
