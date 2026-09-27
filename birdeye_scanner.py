"""Birdeye-дискавери мультичейн: /defi/token_trending с ротацией сетей.

Эндпоинт поддерживает x-chain: solana, robinhood, base, bsc (проверено по докам).
Ключ: бесплатно на bds.birdeye.so (Standard 30K CU/мес, token_trending = 50 CU).

CU-бюджет: ротация по 1 запросу за тик, интервал BIRDEYE_INTERVAL (30 мин) —
48 запросов/сутки. Free-хватит частично; на Lite ($39, 1.5M) — с запасом.
Без BIRDEYE_API_KEY петля молча спит.

EVM-минты идут в analyze_robinhood_token (не в Solana-анализатор!),
Solana — в analyze_token.
"""
import asyncio
import time

import config
from http_client import fetch_json

URL = ("https://public-api.birdeye.so/defi/token_trending"
       "?sort_by=rank&sort_type=asc&offset=0&limit=50")


async def fetch_birdeye_trending(chain: str) -> list:
    """[(address, chain), ...] трендов сети. Пусто без ключа или при 429."""
    key = getattr(config, "BIRDEYE_API_KEY", "") or ""
    if not key:
        return []
    status, data = await fetch_json(
        URL, headers={"X-API-KEY": key, "x-chain": chain},
        timeout=12, retries=1)
    if status != 200 or not isinstance(data, dict):
        return []
    out = []
    for item in ((data.get("data") or {}).get("tokens") or []):
        a = item.get("address", "")
        if a and a not in [x[0] for x in out]:
            out.append((a, chain))
    return out


async def birdeye_loop(analyzer, tracker):
    """Петля Birdeye: ротация сетей по 1 запросу за тик (бережём CU)."""
    if not (getattr(config, "BIRDEYE_API_KEY", "") or ""):
        return
    chains = list(getattr(config, "BIRDEYE_CHAINS",
                          ["solana", "robinhood", "base", "bsc"]))
    print(f"🦅 Birdeye Scanner запущен: ротация {chains}!")
    processed = {}
    tick = 0
    interval = int(getattr(config, "BIRDEYE_INTERVAL", 1800))
    while True:
        try:
            chain = chains[tick % len(chains)]
            tick += 1
            if len(tracker.get_open_positions()) < config.MAX_CONCURRENT_POSITIONS:
                for addr, ch in await fetch_birdeye_trending(chain):
                    if not addr or addr in tracker.positions:
                        continue
                    if time.time() - processed.get(addr, 0.0) < 3600:
                        continue
                    try:
                        if ch == "solana":
                            ok = await analyzer.analyze_token(addr)
                            td = await analyzer.fetch_token_data(addr) if ok else None
                        else:
                            import evm_data
                            ok = await analyzer.analyze_robinhood_token(addr, ch)
                            td = await evm_data.get_token_data(addr, ch) if ok else None
                    except Exception as e:
                        print(f"🦅 Birdeye analyze err {addr[:8]}: {type(e).__name__}")
                        continue
                    if ok is None:
                        continue
                    processed[addr] = time.time()
                    analyzer.log_scan(addr[:8], addr, "BIRDEYE", ok,
                                      getattr(analyzer, "last_score", 0.0))
                    if ok is True and len(tracker.get_open_positions()) < config.MAX_CONCURRENT_POSITIONS:
                        price = float((td.get("priceUsd") or 0)) if td else 0
                        if price > 0:
                            sym = ((td.get("baseToken") or {}).get("symbol", addr[:8])
                                   if td else addr[:8]) or addr[:8]
                            cap = tracker.get_total_capital()
                            size = max(4.0, min(100.0, cap * (config.REINVEST_PERCENT / 100.0))) if cap > 0 else 4.0
                            tracker.add_position(
                                sym, addr, price, min(size, 100.0), is_mature=True,
                                ml_features=analyzer.pack_features(td or {}),
                                ml_confidence=float(getattr(analyzer, "last_score", 0.0)),
                                source=f"BIRDEYE:{analyzer.get_signal(addr)}",
                                **({"chain": ch} if ch != "solana" else {}))
                            print(f"🦅 BIRDEYE BUY {sym} [{ch}] @ ${price}")
                            break
                    await asyncio.sleep(1.0)
                if len(processed) > 1000:
                    processed.clear()
        except Exception as e:
            print(f"Ошибка в birdeye_loop: {e}")
        await asyncio.sleep(interval)
