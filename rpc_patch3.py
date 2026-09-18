import re
with open("analyzer.py", "r") as f:
    code = f.read()

# Replace the heavy_rpcs block to include exception logging
old_heavy = """            for heavy_url in heavy_rpcs:
                try:
                    async with session.post(heavy_url, json=top10_payload, headers=fake_headers, timeout=5) as resp:
                        if resp.status == 200:
                            bundle_data = await resp.json(content_type=None)
                            if bundle_data and "result" in bundle_data:
                                break
                except Exception:
                    continue"""

new_heavy = """            for heavy_url in heavy_rpcs:
                try:
                    async with session.post(heavy_url, json=top10_payload, headers=fake_headers, timeout=8) as resp:
                        if resp.status == 200:
                            bundle_data = await resp.json(content_type=None)
                            if bundle_data and "result" in bundle_data:
                                break
                except Exception as e:
                    print(f"⚠️ Ошибка Jito RPC {heavy_url}: {type(e).__name__} {e}")
                    continue"""

code = code.replace(old_heavy, new_heavy)
with open("analyzer.py", "w") as f:
    f.write(code)
