import re
with open("analyzer.py", "r") as f:
    code = f.read()

# Replace the Top 10 RPC fetching logic to include the Alchemy key as a primary fallback
old_top10_fetch = """        # === ГЛОБАЛЬНЫЙ JITO BUNDLE (SYBIL) CHECK ===
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
                    except Exception: continue"""

new_top10_fetch = """        # === ГЛОБАЛЬНЫЙ JITO BUNDLE (SYBIL) CHECK ===
        top10_payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenLargestAccounts", "params": [mint]}
        try:
            bundle_data = None
            
            # 1. Helius 2. Alchemy (пользовательский)
            heavy_rpcs = [
                rpc_url,
                "https://solana-mainnet.g.alchemy.com/v2/alch_wwSmrv5RZmrq66-lSNekM"
            ]
            
            for heavy_url in heavy_rpcs:
                try:
                    async with session.post(heavy_url, json=top10_payload, headers=fake_headers, timeout=5) as resp:
                        if resp.status == 200:
                            bundle_data = await resp.json(content_type=None)
                            if bundle_data and "result" in bundle_data:
                                break
                except Exception:
                    continue
            
            # Если платные/выделенные ключи отвалились, пробуем публичные (но они часто банят)
            if not bundle_data or "result" not in bundle_data:
                for fallback_url in fallback_rpcs:
                    try:
                        async with session.post(fallback_url, json=top10_payload, headers=fake_headers, timeout=5) as resp:
                            if resp.status == 200:
                                res = await resp.json(content_type=None)
                                if res and "result" in res:
                                    bundle_data = res
                                    break
                    except Exception: continue"""

code = code.replace(old_top10_fetch, new_top10_fetch)
with open("analyzer.py", "w") as f:
    f.write(code)
