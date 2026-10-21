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
    gt_urls = [
        "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?duration=1h",
        "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?duration=5m"
    ]
    headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
    
    seen = set()
    
    while True:
        try:
            mints = []
            
            # 1. Свежие профили и бусты DexScreener
            for url in ds_urls:
                try:
                    status, data = await fetch_json(url, headers=headers, timeout=10, retries=1)
                    if status == 200 and isinstance(data, list):
                        for item in data:
                            if item.get("chainId") == "solana" and "tokenAddress" in item:
                                mints.append(item["tokenAddress"])
                except Exception:
                    pass

            # 2. GeckoTerminal ТОП-тренды 1H и 5M (только с реальным импульсом m5 >= 2.5%)
            for gt_url in gt_urls:
                try:
                    status, data = await fetch_json(gt_url, headers=headers, timeout=10, retries=1)
                    if status == 200 and data and "data" in data:
                        for pool in data.get("data", []):
                            try:
                                attrs = pool.get("attributes", {})
                                chg_m5 = float((attrs.get("price_change_percentage") or {}).get("m5", 0) or 0)
                                # Не шлём во флете: только монеты с реальным импульсом m5 >= 2.5%
                                if chg_m5 < 2.5:
                                    continue
                                bid = pool.get("relationships", {}).get("base_token", {}).get("data", {}).get("id", "")
                                m = bid.split("_", 1)[1] if "_" in bid else bid
                                if m and len(m) > 30:
                                    mints.append(m)
                            except Exception:
                                pass
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
