import re

with open('pump_fun_sniper.py', 'r') as f:
    content = f.read()

bad_logic = '''                            # Исключаем аккаунт Bonding Curve (он держит ~80% саплая)
                            non_curve_accounts = [float(acc["uiAmount"]) for acc in accounts if float(acc["uiAmount"]) < 800_000_000]
                            if non_curve_accounts:
                                dev_holding_pct = (non_curve_accounts[0] / total_supply) * 100
                                top_10_holding_pct = (sum(non_curve_accounts[:10]) / total_supply) * 100'''

good_logic = '''                            # Исключаем самый первый аккаунт (это всегда Bonding Curve на Pump.fun)
                            if len(accounts) > 1:
                                non_curve_accounts = [float(acc["uiAmount"]) for acc in accounts[1:]]
                                dev_holding_pct = (non_curve_accounts[0] / total_supply) * 100 if non_curve_accounts else 0
                                top_10_holding_pct = (sum(non_curve_accounts[:10]) / total_supply) * 100
                            else:
                                dev_holding_pct, top_10_holding_pct = 0, 0'''

content = content.replace(bad_logic, good_logic)

with open('pump_fun_sniper.py', 'w') as f:
    f.write(content)
