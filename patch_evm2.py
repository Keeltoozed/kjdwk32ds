import re

with open("analyzer.py", "r") as f:
    content = f.read()

# Re-apply carefully
# Remove previous patch first to be clean
content = re.sub(r'\n        # --- ВНЕДРЕНИЕ АНТИ-СКАМ ФИЛЬТРОВ ОТ GOPLUS LABS ДЛЯ EVM ---.*?# --- КОНЕЦ ФИЛЬТРОВ GOPLUS ---', '', content, flags=re.DOTALL)

target_pattern = r'        _px_now = float\(pair_data\.get\("priceUsd", 0\) or 0\)'

replacement = """        _px_now = float(pair_data.get("priceUsd", 0) or 0)

        # --- ВНЕДРЕНИЕ АНТИ-СКАМ ФИЛЬТРОВ ОТ GOPLUS LABS ДЛЯ EVM ---
        if liq >= 2000 and (chain == "bsc" or chain == "base" or chain == "ethereum" or chain == "robinhood"):
            chain_map = {"bsc": "56", "base": "8453", "ethereum": "1", "robinhood": "4663"}
            cid = chain_map.get(chain, "1")
            try:
                import aiohttp
                session = await self.get_session()
                goplus_url = f"https://api.gopluslabs.io/api/v1/token_security/{cid}?contract_addresses={address}"
                async with session.get(goplus_url, timeout=5) as resp:
                    if resp.status == 200:
                        gp_data = await resp.json(content_type=None)
                        res = gp_data.get("result", {}).get(address.lower(), {})
                        if res:
                            # Honeypot / ограничения
                            if res.get("is_honeypot") == "1":
                                print(f"🚫 [АНТИСКАМ-EVM] {symbol} — Это Honeypot! Блокируем.")
                                return False
                            if res.get("cannot_sell_all") == "1" or res.get("cannot_buy") == "1" or res.get("is_blacklisted") == "1":
                                print(f"🚫 [АНТИСКАМ-EVM] {symbol} — Ограничения на торговлю! Блокируем.")
                                return False
                                
                            # Налоги
                            sell_tax = float(res.get("sell_tax", 0) or 0)
                            if sell_tax > 0.1:
                                print(f"🚫 [АНТИСКАМ-EVM] {symbol} — Скрытый налог {sell_tax*100}% (>10%). Блокируем.")
                                return False
                                
                            # Бэкдоры
                            if res.get("is_mintable") == "1":
                                print(f"🚫 [АНТИСКАМ-EVM] {symbol} — Контракт позволяет печатать новые токены (is_mintable). Блокируем.")
                                return False
                            if res.get("can_take_back_ownership") == "1":
                                print(f"🚫 [АНТИСКАМ-EVM] {symbol} — Скрытый бэкдор владельца (take back). Блокируем.")
                                return False
                            if res.get("trading_cooldown") == "1":
                                print(f"🚫 [АНТИСКАМ-EVM] {symbol} — Торговый кулдаун (манипуляция). Блокируем.")
                                return False
                                
                            # Кошелек создателя
                            creator_pct = float(res.get("creator_percent", 0) or 0)
                            if creator_pct > 0.15:
                                print(f"🚫 [АНТИСКАМ-EVM] {symbol} — Создатель держит {creator_pct*100}% (>15%). Блокируем.")
                                return False
                                
            except Exception as e:
                print(f"⚠️ Ошибка GoPlus API для {address[:8]}: {e}")
        # --- КОНЕЦ ФИЛЬТРОВ GOPLUS ---"""

new_content = re.sub(target_pattern, replacement, content)

with open("analyzer.py", "w") as f:
    f.write(new_content)
print("Patch applied to EVM tokens.")
