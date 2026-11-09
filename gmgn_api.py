"""
GMGN API Discovery — легковесный стрим траншей и трендов GMGN.AI
(Архитектура GMGNAI/gmgn-skills OpenAPI, без тяжелого Playwright/Chromium).

Поддерживает:
1. Прямой OpenAPI при наличии GMGN_API_KEY (выдается на gmgn.ai/ai)
2. Безопасный режим ожидания (0MB RAM на Render вместо 1GB Chromium)
3. Автоматический пуш кандидатов в fomo_signals.txt и прямой скоринг
"""
import asyncio
import os
import time
import json
from http_client import fetch_json
import config

BASE_API = "https://gmgn.ai/defi/quotation/v1"
AI_OPENAPI_BASE = "https://gmgn.ai/ai/v1"

_cache = []
_cache_time = 0.0
CACHE_TTL = 15.0
_warned_no_key = False

def get_api_key() -> str:
    return getattr(config, "GMGN_API_KEY", "") or os.environ.get("GMGN_API_KEY", "") or os.environ.get("GMGNAI_API_KEY", "")

async def fetch_gmgn_trending(chain: str = "sol", time_window: str = "5m", limit: int = 50) -> list:
    """Получает топ трендов GMGN через OpenAPI."""
    api_key = get_api_key()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["x-api-key"] = api_key
        url = f"{AI_OPENAPI_BASE}/market/trending/{chain}?time_window={time_window}&limit={limit}"
        status, data = await fetch_json(url, headers=headers, timeout=8, retries=0)
        if status == 200 and isinstance(data, dict):
            tokens = data.get("data", {}).get("tokens", []) or []
            return [t.get("address") for t in tokens if t.get("address")]

    # Фоллбэк без ключа (если открыт)
    url = f"{BASE_API}/rank/{chain}/swaps/{time_window}?orderby=swaps&direction=desc&limit={limit}"
    status, data = await fetch_json(url, headers=headers, timeout=6, retries=0)
    if status == 200 and isinstance(data, dict):
        rank = (data.get("data") or {}).get("rank") or []
        return [t.get("address") for t in rank if t.get("address")]
    return []

async def fetch_gmgn_trenches(chain: str = "sol", filter_type: str = "almost_bonded") -> list:
    """Получает монеты на 80-95% бондинг-кёрва (almost_bonded) или только что мигрировавшие (migrated)."""
    api_key = get_api_key()
    if not api_key:
        return []
    headers = {
        "Authorization": f"Bearer {api_key}",
        "x-api-key": api_key,
        "Accept": "application/json",
    }
    url = f"{AI_OPENAPI_BASE}/trenches?chain={chain}&filter={filter_type}"
    status, data = await fetch_json(url, headers=headers, timeout=8, retries=0)
    if status == 200 and isinstance(data, dict):
        trenches = (data.get("data") or {}).get("list") or []
        return [t.get("address") for t in trenches if t.get("address")]
    return []

async def gmgn_api_loop(analyzer, tracker):
    """Легковесный фоновый цикл GMGN-стрима без браузера."""
    global _warned_no_key
    print("📡 [GMGN-API] Легковесный модуль GMGN OpenAPI инициализирован (0MB RAM, без Chromium).")
    processed = {}
    
    while True:
        try:
            api_key = get_api_key()
            if not api_key:
                if not _warned_no_key:
                    print("ℹ️ [GMGN-API] Для прямого стрима траншей (almost_bonded/migrated) добавьте GMGN_API_KEY в .env (получить на gmgn.ai/ai).")
                    _warned_no_key = True
                await asyncio.sleep(60.0)
                continue
            
            # Если ключ есть — опрашиваем траншеи и 5м тренды
            almost_bonded, trending = await asyncio.gather(
                fetch_gmgn_trenches("sol", "almost_bonded"),
                fetch_gmgn_trending("sol", "5m", 30),
                return_exceptions=True
            )
            candidates = []
            if isinstance(almost_bonded, list):
                candidates.extend(almost_bonded)
            if isinstance(trending, list):
                candidates.extend(trending)
            
            candidates = list(dict.fromkeys(candidates))
            now = time.time()
            fresh = [m for m in candidates if (now - processed.get(m, 0)) > 45]
            
            if fresh:
                print(f"📡 [GMGN-API] Получено {len(fresh)} кандидатов из траншей/трендов GMGN! Предварительный прогрев...")
                try:
                    await analyzer.prefetch_token_data(fresh[:15], ttl=60.0)
                except Exception:
                    pass
                for m in fresh[:10]:
                    processed[m] = now
                    try:
                        from fomo_api import push_signal
                        push_signal("solana", m, f"GMGN-OPENAPI")
                    except Exception:
                        pass
                    
            await asyncio.sleep(12.0)
        except asyncio.CancelledError:
            break
        except Exception as e:
            await asyncio.sleep(15.0)
