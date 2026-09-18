import re
with open("analyzer.py", "r") as f:
    code = f.read()

# 1. In analyze_token, after calculating top_10_sum_pct, store it in variables
# Wait, I already added top_10_sum_pct. Let's capture dev_holding.
old_sum_check = "top_10_sum_pct = sum(top_10_amounts) / 10_000_000.0"
new_sum_check = """top_10_sum_pct = sum(top_10_amounts) / 10_000_000.0
                    dev_holding_pct = (top_10_amounts[0] / 10_000_000.0) if top_10_amounts else 0.0
                    self._last_top10 = top_10_sum_pct
                    self._last_dev = dev_holding_pct"""
code = code.replace(old_sum_check, new_sum_check)

# 2. In analyze_token_xgboost, replace the whole fetch block with reading from self
old_fetch_block = """        # Get holders via Helius RPC
        dev_holding_pct, top_10_holding_pct = 0.0, 0.0
        rpc_url = "https://mainnet.helius-rpc.com/?api-key=9efda6f4-fddb-42d3-a2b1-098bbbecd299"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [mint]
        }
        try:
            import aiohttp
            session = await self.get_session()
            rpc_url = getattr(config, "HELIUS_RPC_URL", "https://mainnet.helius-rpc.com/?api-key=9efda6f4-fddb-42d3-a2b1-098bbbecd299")
            
            fallback_rpcs = [
                "https://rpc.ankr.com/solana",
                "https://solana-rpc.publicnode.com",
                "https://api.mainnet-beta.solana.com"
            ]
            
            fake_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://explorer.solana.com",
                "Referer": "https://explorer.solana.com/"
            }
            
            data = None
            try:
                async with session.post(rpc_url, json=payload, timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                    else:
                        raise Exception(f"HTTP {resp.status} - {await resp.text()}")
            except Exception as e:
                for fallback_url in fallback_rpcs:
                    try:
                        async with session.post(fallback_url, json=payload, headers=fake_headers, timeout=10) as resp:
                            if resp.status == 200:
                                data = await resp.json(content_type=None)
                                break
                            else:
                                err_txt = await resp.text()
                                print(f"⚠️ Top-10 Резервный {fallback_url} выдал {resp.status}: {err_txt[:100]}")
                    except Exception as ex:
                        print(f"⚠️ Ошибка Top-10 резервного {fallback_url}: {ex}")
                        continue
                        
            if data:
                accounts = data.get("result", {}).get("value", [])
                total_supply = 1_000_000_000
                if accounts:
                    # Exclude bonding curve account which holds ~80% initially
                    # We just sum the remaining top 9 accounts
                    non_curve_accounts = [float(acc["uiAmount"]) for acc in accounts if float(acc["uiAmount"]) < 800_000_000]
                    top_10_amounts = non_curve_accounts[:10]
                    top_10_holding_pct = (sum(top_10_amounts) / total_supply) * 100
                    if top_10_amounts:
                        dev_holding_pct = (top_10_amounts[0] / total_supply) * 100 
                        
                    # ЖЕСТКАЯ ЗАЩИТА: Топ-10 кошельков (без пула) не должны держать >20% саплая.
                    # Иначе это монополия создателя, готовая к дампу (Rugpull).
                    if top_10_holding_pct > 20.0:
                        print(f"🚫 [Защита от дампа] Топ-10 холдеров держат {top_10_holding_pct:.1f}% > 20% у {mint}.")
                        return False
            else:
                print("⚠️ Не удалось получить список Топ-10 холдеров (RPC недоступны). Блокируем вход.")
                return False
        except Exception as e:
            print(f"⚠️ Ошибка проверки Топ-10 холдеров: {e}")
            return False"""

new_fetch_block = """        # Используем данные, полученные в глобальном analyze_token
        top_10_holding_pct = getattr(self, '_last_top10', 0.0)
        dev_holding_pct = getattr(self, '_last_dev', 0.0)"""

code = code.replace(old_fetch_block, new_fetch_block)
with open("analyzer.py", "w") as f:
    f.write(code)
