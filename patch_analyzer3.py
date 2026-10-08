import re

with open("analyzer.py", "r") as f:
    content = f.read()

start_pattern = r'rpc_url = getattr\(config, "HELIUS_RPC_URL".*?elif top_10_sum_pct > max_allowed_pct:\s*print\(f"🚫 \[АНТИСКАМ\] Топ-10 держат \{top_10_sum_pct:.1f\}% \(Лимит \{max_allowed_pct\}%\). Блокируем."\)\s*return False\n\s*else:\n\s*print\(f"⚠️ Не удалось проверить Jito-бандлы \(RPC недоступны\)\. Блокируем вход\."\)\s*return False'

replacement = """try:
            import aiohttp
            session = await self.get_session()
            
            rugcheck_url = f"https://api.rugcheck.xyz/v1/tokens/{mint}/report"
            rc_data = None
            async with session.get(rugcheck_url, timeout=8) as resp:
                if resp.status == 200:
                    rc_data = await resp.json(content_type=None)
                else:
                    print(f"⚠️ RugCheck API вернул {resp.status} для {mint[:8]}.")
                    
            if rc_data:
                token_info = rc_data.get("token", {})
                mint_authority = token_info.get("mintAuthority")
                freeze_authority = token_info.get("freezeAuthority")
                
                PUMPFUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
                SYSTEM_PROGRAM = "11111111111111111111111111111111"
                SAFE_AUTHORITIES = {PUMPFUN_PROGRAM, SYSTEM_PROGRAM, None, ""}
                
                if mint_authority and mint_authority not in SAFE_AUTHORITIES:
                    print(f"🚫 [АНТИСКАМ] Mint Authority у ДЕВ-кошелька {mint_authority[:8]} у {mint[:8]} → СКАМ")
                    return False
                
                if freeze_authority and freeze_authority not in SAFE_AUTHORITIES:
                    print(f"🚫 [АНТИСКАМ] Freeze Authority у ДЕВ-кошелька {freeze_authority[:8]} у {mint[:8]} → СКАМ")
                    return False
                    
                holders = rc_data.get("topHolders", [])
                if not holders:
                    print(f"⚠️ Ошибка RugCheck (пустой список аккаунтов). Блокируем вход.")
                    return False
                    
                non_curve_accounts = []
                for h in holders:
                    pct = h.get("pct", 0)
                    amt = h.get("uiAmount", 0)
                    owner = h.get("owner", "")
                    
                    if "Raydium" in owner or "Meteora" in owner or pct > 80.0 or owner == "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1":
                        continue
                    non_curve_accounts.append((amt, pct))
                
                top_10 = non_curve_accounts[:10]
                top_10_amounts = [x[0] for x in top_10]
                
                if len(top_10_amounts) >= 3:
                    rounded_amounts = [round(amt, -6) for amt in top_10_amounts if amt > 1000000]
                    if rounded_amounts:
                        from collections import Counter
                        counts = Counter(rounded_amounts)
                        if counts.most_common(1)[0][1] >= 3:
                            print(f"🚫 [АНТИСКАМ] Обнаружен Jito-бандл (Сивил атака) у {mint}. Блокируем.")
                            return False
                
                top_10_sum_pct = sum([x[1] for x in top_10])
                dev_holding_pct = top_10[0][1] if top_10 else 0.0
                
                self._last_top10 = top_10_sum_pct
                self._last_dev = dev_holding_pct
                
                is_pump = pair_data and pair_data.get("dexId") == "pump"
                max_allowed_pct = 20.0 if is_pump else 45.0
                
                if top_10_sum_pct > 100:
                    print(f"⚠️ [HOLDERS] {mint[:8]}: топ-10 {top_10_sum_pct:.1f}% > 100% — битые данные сапплая.")
                elif dev_holding_pct > 15.0:
                    print(f"🚫 [АНТИСКАМ] Один кошелек (Dev) держит {dev_holding_pct:.1f}% (Лимит 15%). Блокируем.")
                    return False
                elif top_10_sum_pct > max_allowed_pct:
                    print(f"🚫 [АНТИСКАМ] Топ-10 держат {top_10_sum_pct:.1f}% (Лимит {max_allowed_pct}%). Блокируем.")
                    return False
            else:
                print(f"⚠️ Не удалось проверить через RugCheck. Блокируем вход от греха подальше.")
                return False
        except Exception as e:
            print(f"⚠️ Критическая ошибка при проверке RugCheck: {str(e)[:50]}. Блокируем вход.")
            return False"""

new_content = re.sub(start_pattern, replacement, content, flags=re.DOTALL)

with open("analyzer.py", "w") as f:
    f.write(new_content)
print("Patch applied to analyzer.py")
