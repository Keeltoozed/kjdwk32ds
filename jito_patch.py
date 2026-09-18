import re
with open("analyzer.py", "r") as f:
    code = f.read()

jito_block = """
        # === ГЛОБАЛЬНЫЙ JITO BUNDLE (SYBIL) CHECK ===
        top10_payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenLargestAccounts", "params": [mint]}
        try:
            bundle_data = None
            try:
                async with session.post(rpc_url, json=top10_payload, timeout=5) as resp:
                    if resp.status == 200: bundle_data = await resp.json(content_type=None)
            except Exception: pass
            
            if not bundle_data:
                for fallback_url in fallback_rpcs:
                    try:
                        async with session.post(fallback_url, json=top10_payload, headers=fake_headers, timeout=10) as resp:
                            if resp.status == 200:
                                bundle_data = await resp.json(content_type=None)
                                break
                    except Exception: continue

            if bundle_data:
                accounts = bundle_data.get("result", {}).get("value", [])
                if accounts:
                    non_curve_accounts = [float(acc["uiAmount"]) for acc in accounts if float(acc["uiAmount"]) < 800_000_000]
                    top_10_amounts = non_curve_accounts[:10]
                    if len(top_10_amounts) >= 3:
                        rounded_amounts = [round(amt, -6) for amt in top_10_amounts if amt > 1000000]
                        if rounded_amounts:
                            from collections import Counter
                            counts = Counter(rounded_amounts)
                            if counts.most_common(1)[0][1] >= 3:
                                print(f"🚫 [АНТИСКАМ] Обнаружен Jito-бандл (Сивил атака) у {mint}. Блокируем.")
                                return False
            else:
                print(f"⚠️ Не удалось проверить Jito-бандлы (RPC недоступны). Блокируем вход.")
                return False
        except Exception as e:
            print(f"⚠️ Ошибка Jito-bundle: {e}")
            return False
"""

# Insert it after Mint Authority check
code = code.replace("return False\n\n        # === PULLBACK ENTRY", "return False\n" + jito_block + "\n        # === PULLBACK ENTRY")
with open("analyzer.py", "w") as f:
    f.write(code)
