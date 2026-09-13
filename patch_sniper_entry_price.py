import re

with open('pump_fun_sniper.py', 'r') as f:
    content = f.read()

old_logic = '''            sol_amount = state.trades[-1]["curve_sol"] if state.trades else 0
            actual_price = (sol_amount / 1_000_000_000.0) * get_sol_price_sync()'''

new_logic = '''            sol_amount = state.trades[-1]["curve_sol"] if state.trades else 0
            # ИСПРАВЛЕНИЕ ВХОДНОЙ ЦЕНЫ: берем marketCapSol из последнего трейда
            market_cap_sol = state.trades[-1].get("market_cap_sol", 0) if state.trades else 0
            if market_cap_sol > 0:
                actual_price = (market_cap_sol / 1_000_000_000.0) * get_sol_price_sync()
            else:
                # Фоллбэк (хотя marketCapSol должен быть всегда)
                actual_price = (sol_amount / 1_000_000_000.0) * get_sol_price_sync()'''

content = content.replace(old_logic, new_logic)

old_trade_append = '''                            # Фиксация трейда
                            state.trades.append({
                                'timestamp': time.time(),
                                'type': tx_type,
                                'curve_sol': sol_amount,
                                'wallet': data.get('traderPublicKey')
                            })'''

new_trade_append = '''                            # Фиксация трейда
                            state.trades.append({
                                'timestamp': time.time(),
                                'type': tx_type,
                                'curve_sol': sol_amount,
                                'market_cap_sol': data.get("marketCapSol", 0),
                                'wallet': data.get('traderPublicKey')
                            })'''

content = content.replace(old_trade_append, new_trade_append)

with open('pump_fun_sniper.py', 'w') as f:
    f.write(content)
