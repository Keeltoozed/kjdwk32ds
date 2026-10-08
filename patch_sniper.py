import re

with open("pump_fun_sniper.py", "r") as f:
    content = f.read()

start_pattern = r'            # --- ВНЕДРЕНИЕ АНТИ-СКАМ ФИЛЬТРОВ ОТ ПОЛЬЗОВАТЕЛЯ ---.*?# --- КОНЕЦ ФИЛЬТРОВ ---'

replacement = """            # --- ВНЕДРЕНИЕ АНТИ-СКАМ ФИЛЬТРОВ (RUGCHECK) ---
            try:
                import aiohttp
                from http_client import get_session
                session = await get_session()
                
                rugcheck_url = f"https://api.rugcheck.xyz/v1/tokens/{state.mint}/report"
                async with session.get(rugcheck_url, timeout=10) as resp:
                    if resp.status == 200:
                        rc_data = await resp.json(content_type=None)
                        holders = rc_data.get("topHolders", [])
                        if holders:
                            non_curve_accounts = []
                            for h in holders:
                                pct = h.get("pct", 0)
                                amt = h.get("uiAmount", 0)
                                owner = h.get("owner", "")
                                if "Raydium" in owner or "Meteora" in owner or pct > 80.0 or owner == "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1":
                                    continue
                                non_curve_accounts.append((amt, pct))
                                
                            top_10 = non_curve_accounts[:10]
                            top_10_sum_pct = sum([x[1] for x in top_10])
                            dev_holding_pct = top_10[0][1] if top_10 else 0.0
                            
                            if dev_holding_pct > 7.0:
                                print(f"🚫 [DEV DUMP RISK] {state.symbol}: Создатель держит {dev_holding_pct:.1f}% (>7%). Пропуск!")
                                state.is_ai_evaluated = True
                                return
                                
                            if top_10_sum_pct > 30.0:
                                print(f"🚫 [SYBIL RISK] {state.symbol}: Топ-10 холдеров держат {top_10_sum_pct:.1f}% (>30%). Пропуск!")
                                state.is_ai_evaluated = True
                                return
            except Exception as e:
                print(f"⚠️ Ошибка проверки холдеров (RugCheck): {e}")
            # --- КОНЕЦ ФИЛЬТРОВ ---"""

new_content = re.sub(start_pattern, replacement, content, flags=re.DOTALL)

with open("pump_fun_sniper.py", "w") as f:
    f.write(new_content)
print("Patch applied to pump_fun_sniper.py")
