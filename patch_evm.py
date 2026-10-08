import re

with open("analyzer.py", "r") as f:
    content = f.read()

# We want to insert the GoPlus check right after fetching pair_data and checking basic liq
# Let's find a good spot.
# Right after: _px_now = float(pair_data.get("priceUsd", 0) or 0)

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
                            # Проверяем Honeypot (нельзя продать)
                            if res.get("is_honeypot") == "1":
                                print(f"🚫 [АНТИСКАМ-EVM] {symbol} — Это Honeypot! Блокируем.")
                                return False
                            # Проверяем Blacklist
                            if res.get("is_blacklisted") == "1":
                                print(f"🚫 [АНТИСКАМ-EVM] {symbol} — В черном списке! Блокируем.")
                                return False
                            # Проверяем возможность продажи
                            if res.get("cannot_sell_all") == "1" or res.get("cannot_buy") == "1":
                                print(f"🚫 [АНТИСКАМ-EVM] {symbol} — Ограничения на торговлю! Блокируем.")
                                return False
                            # Налог на продажу
                            sell_tax = float(res.get("sell_tax", 0) or 0)
                            if sell_tax > 0.1:
                                print(f"🚫 [АНТИСКАМ-EVM] {symbol} — Налог на продажу {sell_tax*100}% (>10%). Блокируем.")
                                return False
                            # Владелец может забрать деньги?
                            if res.get("can_take_back_ownership") == "1":
                                print(f"🚫 [АНТИСКАМ-EVM] {symbol} — Скрытый бэкдор владельца. Блокируем.")
                                return False
            except Exception as e:
                print(f"⚠️ Ошибка GoPlus API для {address[:8]}: {e}")
        # --- КОНЕЦ ФИЛЬТРОВ GOPLUS ---"""

new_content = re.sub(target_pattern, replacement, content)

with open("analyzer.py", "w") as f:
    f.write(new_content)
print("Patch applied to EVM tokens.")
