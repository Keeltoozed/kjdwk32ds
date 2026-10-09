import asyncio
import time
import random
import config
from http_client import fetch_json

async def fomo_loop(analyzer, tracker):
    print("📡 Радар DexScreener/Gecko (Расширенный) запущен.")
    
    ds_urls = [
        "https://api.dexscreener.com/token-profiles/latest/v1",
        "https://api.dexscreener.com/token-boosts/latest/v1",
        "https://api.dexscreener.com/token-boosts/top/v1"
    ]
    gt_url = "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools"
    headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
    
    keywords = ["pump", "meme", "dog", "cat", "ai", "trump", "sol", "pepe"]
    
    seen = set()
    
    while True:
        try:
            mints = []
            
            # 1. Свежие профили и бусты
            for url in ds_urls:
                try:
                    status, data = await fetch_json(url, headers=headers, timeout=10, retries=1)
                    if status == 200 and isinstance(data, list):
                        for item in data:
                            if item.get("chainId") == "solana" and "tokenAddress" in item:
                                mints.append(item["tokenAddress"])
                except Exception:
                    pass
            
            # 2. Ротация поиска DexScreener (Тренды по тегам)
            random.shuffle(keywords)
            for kw in keywords[:3]:  # Берем 3 случайных тега за проход
                try:
                    status, data = await fetch_json(f"https://api.dexscreener.com/latest/dex/search?q={kw}", headers=headers, timeout=10, retries=1)
                    if status == 200 and data and "pairs" in data:
                        for pair in data.get("pairs", []):
                            if pair.get("chainId") == "solana":
                                mints.append(pair.get("baseToken", {}).get("address"))
                except Exception:
                    pass

            # 3. GeckoTerminal
            try:
                status, data = await fetch_json(gt_url, headers=headers, timeout=10, retries=1)
                if status == 200 and data and "data" in data:
                    for pool in data.get("data", []):
                        try:
                            mints.append(pool["relationships"]["base_token"]["data"]["id"])
                        except Exception:
                            pass
            except Exception:
                pass
            
            mints = [m for m in mints if m]
            new_mints = list(set(mints) - seen)
            
            for mint in new_mints:
                seen.add(mint)
                if len(seen) > 3000:
                    seen.clear()
                    
                if mint in tracker.positions or len(tracker.get_open_positions()) >= config.MAX_CONCURRENT_POSITIONS:
                    continue
                    
                try:
                    ok = await analyzer.analyze_growth_token(mint)
                except Exception:
                    pass
                await asyncio.sleep(0.5)
                
        except Exception as e:
            print(f"⚠️ Ошибка fomo_loop: {e}")
            
        await asyncio.sleep(60)

if __name__ == "__main__":
    pass
