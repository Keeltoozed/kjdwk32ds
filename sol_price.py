import aiohttp
import asyncio
import time

# Глобальный кеш цены SOL
_sol_price = 150.0
_last_update = 0.0
_UPDATE_INTERVAL = 300  # Обновляем каждые 5 минут

async def get_sol_price() -> float:
    """Получает актуальную цену SOL (Jupiter Lite -> GeckoTerminal) с кешем на 5 минут"""
    global _sol_price, _last_update

    now = time.time()
    if now - _last_update < _UPDATE_INTERVAL:
        return _sol_price

    price = await asyncio.to_thread(_fetch_sol_price_sync)
    if price > 0:
        _sol_price = price
        _last_update = now
        print(f"💰 Цена SOL обновлена: ${_sol_price:.2f}")
    else:
        print(f"⚠️ Ошибка получения цены SOL. Используем кеш: ${_sol_price:.2f}")

    return _sol_price


def _fetch_sol_price_sync() -> float:
    prices = fetch_bulk_prices_sync(["So11111111111111111111111111111111111111112"])
    try:
        return float(prices.get("So11111111111111111111111111111111111111112", 0))
    except Exception:
        return 0.0

def get_sol_price_sync() -> float:
    """Синхронная версия — возвращает последнюю кешированную цену"""
    return _sol_price

import requests

def fetch_bulk_prices_sync(mints: list) -> dict:
    """Синхронные лайв-цены: Jupiter Lite первым (своя квота), GeckoTerminal как fallback."""
    if not mints: return {}
    out = {}
    # 1. Jupiter Lite (до 50 за запрос)
    try:
        import json as _json
        ms = [m for m in dict.fromkeys(mints) if m]
        for i in range(0, len(ms), 50):
            chunk = ms[i:i + 50]
            url = "https://api.jup.ag/price/v2?ids=" + ",".join(chunk)
            resp = requests.get(url, headers={"Accept": "application/json",
                                              "User-Agent": "Mozilla/5.0"}, timeout=10)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                for m in chunk:
                    try:
                        px = data.get(m, {}).get("price", 0)
                        if px and float(px) > 0:
                            out[m] = float(px)
                    except Exception:
                        continue
    except Exception as e:
        print(f"⚠️ Ошибка Live-цен Jupiter Lite: {e}")
    # 2. Недостающее — через GeckoTerminal
    missing = [m for m in dict.fromkeys(mints) if m and m not in out]
    if missing:
        try:
            url = ("https://api.geckoterminal.com/api/v2/simple/networks/solana/token_price/"
                   + ",".join(missing[:30]))
            resp = requests.get(url, headers={"Accept": "application/json",
                                              "User-Agent": "Mozilla/5.0"}, timeout=5)
            if resp.status_code == 200:
                px = resp.json().get("data", {}).get("attributes", {}).get("token_prices", {})
                for m, p in px.items():
                    out[m] = float(p)
        except Exception as e:
            print(f"⚠️ Ошибка получения Live-цен из GeckoTerminal: {e}")
    return out
