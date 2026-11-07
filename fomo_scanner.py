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
        "https://api.dexscreener.com/token-boosts/top/v1",
        "https://api.dexscreener.com/token-profiles/recent-updates/v1",
        "https://api.dexscreener.com/community-takeovers/latest/v1"
    ]
    headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
    
    seen = set()
    
    while True:
        try:
            mints = []
            
            # 1. Свежие профили и бусты DexScreener (квота 300 req/min, параллельно)
            results = await asyncio.gather(
                *[fetch_json(url, headers=headers, timeout=8, retries=0) for url in ds_urls],
                return_exceptions=True,
            )
            for res in results:
                if isinstance(res, tuple) and len(res) == 2:
                    status, data = res
                    if status == 200 and isinstance(data, list):
                        for item in data:
                            if item.get("chainId") == "solana" and "tokenAddress" in item:
                                mints.append(item["tokenAddress"])

            # 2. GeckoTerminal ТОП-тренды через ОБЩИЙ 60с кэш market_data.get_trending() (0 лишних 429 запросов!)
            try:
                import market_data
                gt_items = await market_data.get_trending()
                for item in (gt_items or []):
                    m = item.get("tokenAddress")
                    if m and len(m) > 30:
                        mints.append(m)
            except Exception:
                pass
            
            mints = [m for m in mints if m and len(m) > 30]
            new_mints = list(set(mints) - seen)
            
            from fomo_api import push_signal
            for mint in new_mints:
                seen.add(mint)
                if len(seen) > 3000:
                    seen.clear()
                    
                if mint in tracker.positions or len(tracker.get_open_positions()) >= config.MAX_CONCURRENT_POSITIONS:
                    continue
                    
                push_signal("solana", mint, "FOMO_RADAR")
                await asyncio.sleep(0.1)
                
        except Exception as e:
            print(f"⚠️ Ошибка fomo_loop: {e}")
            
        await asyncio.sleep(60)

if __name__ == "__main__":
    pass
