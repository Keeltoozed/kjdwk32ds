import re

with open('pump_fun_sniper.py', 'r') as f:
    content = f.read()

# 1. Изменяем порог прогресса с 20% до 60% (эквивалент ~$12k-$15k MC)
content = content.replace("progress >= 20.0", "progress >= 60.0")
content = content.replace("Если достигли 20%", "Если достигли 60% (Proof of Traction ~ $12k MC)")

# 2. Добавляем проверку холдеров в evaluate_and_enter
holder_check_logic = '''
            df['tx_count'] = 1
            
            # --- ВНЕДРЕНИЕ АНТИ-СКАМ ФИЛЬТРОВ ОТ ПОЛЬЗОВАТЕЛЯ ---
            try:
                import aiohttp
                rpc_url = "https://mainnet.helius-rpc.com/?api-key=9efda6f4-fddb-42d3-a2b1-098bbbecd299"
                payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenLargestAccounts", "params": [state.mint]}
                async with aiohttp.ClientSession() as session:
                    async with session.post(rpc_url, json=payload, timeout=5) as resp:
                        data = await resp.json()
                        accounts = data.get("result", {}).get("value", [])
                        total_supply = 1_000_000_000
                        if accounts:
                            # Исключаем аккаунт Bonding Curve (он держит ~80% саплая)
                            non_curve_accounts = [float(acc["uiAmount"]) for acc in accounts if float(acc["uiAmount"]) < 800_000_000]
                            if non_curve_accounts:
                                dev_holding_pct = (non_curve_accounts[0] / total_supply) * 100
                                top_10_holding_pct = (sum(non_curve_accounts[:10]) / total_supply) * 100
                                
                                if dev_holding_pct > 7.0:
                                    print(f"🚫 [DEV DUMP RISK] {state.symbol}: Создатель держит {dev_holding_pct:.1f}% (>7%). Пропуск!")
                                    state.is_ai_evaluated = True
                                    return
                                    
                                if top_10_holding_pct > 30.0:
                                    print(f"🚫 [SYBIL RISK] {state.symbol}: Топ-10 холдеров держат {top_10_holding_pct:.1f}% (>30%). Пропуск!")
                                    state.is_ai_evaluated = True
                                    return
            except Exception as e:
                print(f"⚠️ Ошибка проверки холдеров: {e}")
            # --- КОНЕЦ ФИЛЬТРОВ ---

            pro_res = await ask_pro_oracle(df)
'''

content = content.replace("            df['tx_count'] = 1\n            \n            pro_res = await ask_pro_oracle(df)", holder_check_logic.strip("\n"))

with open('pump_fun_sniper.py', 'w') as f:
    f.write(content)
