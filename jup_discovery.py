"""Jupiter Tokens API v2 — дискавери Solana: свежие листинги + топ трендов 5m.

Эндпоинты (docs: developers.jup.ag, нужен бесплатный x-api-key с portal.jup.ag):
- GET /tokens/v2/recent — токены с первым пулом (default 30)
- GET /tokens/v2/toptrending/5m?limit=100 — макс. движение за 5 мин
- GET /tokens/v2/toptraded/1h?limit=50 — макс. объём за час

Без JUP_API_KEY модуль спит. Кэш 120 сек: 2 запроса за цикл в пределах любого тира.
"""
import asyncio
import time

import config
from http_client import fetch_json

BASE = "https://api.jup.ag/tokens/v2"

_cache = []
_cache_time = 0.0
CACHE_TTL = 120


def _headers():
    key = getattr(config, "JUP_API_KEY", "") or ""
    return {"x-api-key": key} if key else {}


async def fetch_jup_tokens() -> list:
    """Возвращает [mint, ...] Solana: recent + toptrending/5m (дедуп, топ по organicScore)."""
    global _cache, _cache_time
    if time.time() - _cache_time < CACHE_TTL and _cache:
        return _cache
    out, seen, scored = [], set(), []
    for path in ("/recent",
                 "/toptrending/5m?limit=100",
                 "/toptraded/1h?limit=50"):
        status, data = await fetch_json(BASE + path, headers=_headers(),
                                        timeout=12, retries=1)
        if status != 200 or not isinstance(data, list):
            continue
        for t in data:
            m = t.get("id", "")
            if not m or m in seen:
                continue
            seen.add(m)
            scored.append((float(t.get("organicScore", 0) or 0), m))
    # органику выше — раньше в очередь (мусор все равно режут гейты анализатора)
    scored.sort(reverse=True)
    out = [m for _, m in scored][:120]
    _cache, _cache_time = out, time.time()
    return out


async def jup_loop(analyzer, tracker):
    """Петля Jupiter-дискавери (Solana). Работает автономно, использует JUP_API_KEY при наличии."""
    _has_key = bool(getattr(config, "JUP_API_KEY", "") or "")
    print(f"🪐 Jupiter Discovery запущен: recent + toptrending/5m + toptraded/1h (Solana) [{'API-KEY подключен' if _has_key else 'Public'}]!")
    processed = {}
    interval = int(getattr(config, "JUP_INTERVAL", 120))
    while True:
        try:
            if len(tracker.get_open_positions()) < config.MAX_CONCURRENT_POSITIONS:
                import time as _t
                mints = await fetch_jup_tokens()
                for mint in mints:
                    if not mint or mint in tracker.positions:
                        continue
                    # Кулдаун на повторный вход после жесткого стопа (-15%+):
                    # Ждём 2 часа, чтобы не ловить повторный дев-дамп, но если монета ожила позже — даём шанс!
                    _prev = tracker.last_loss_pct(mint)
                    if _prev is not None and _prev <= getattr(config, "RUG_REBUY_MAX_LOSS", -0.15):
                        _old_pos = tracker.positions.get(mint) or tracker.positions.get(mint + "_old_1")
                        _exit_t = getattr(_old_pos, "exit_time", 0.0) or 0.0
                        if _exit_t and (_t.time() - _exit_t) < 7200:
                            continue
                    if _t.time() - processed.get(mint, 0.0) < 300:
                        continue
                    td = await analyzer.fetch_token_data(mint)
                    if not td:
                        continue
                    _created = td.get("pairCreatedAt") or 0
                    _age_min = (_t.time() * 1000 - _created) / 60000.0 if _created else 999.0
                    _min_age = float(getattr(config, "SOLANA_MIN_AGE_MIN", 2.5))
                    if _age_min < _min_age:
                        print(f"⏳ [JUP] {mint[:8]}: возраст {_age_min:.1f}м < {_min_age}м — flash-rug зона дева, ждём созревания.")
                        continue
                    # Старые монеты НЕ блокируем! Если монета старше суток, но делает взрывной памп на Jupiter — берём!
                    try:
                        ok = await analyzer.analyze_token(mint)
                    except Exception as e:
                        print(f"🪐 JUP analyze err {mint[:8]}: {type(e).__name__}")
                        continue
                    if ok is None:
                        continue
                    processed[mint] = _t.time()
                    analyzer.log_scan(mint[:8], mint, "JUP",
                                      ok, getattr(analyzer, "last_score", 0.0))
                    if ok is True and len(tracker.get_open_positions()) < config.MAX_CONCURRENT_POSITIONS:
                        import goplus as _gp
                        _g_ok, _g_why = await _gp.check_solana(mint)
                        if not _g_ok:
                            print(f"🚫 [JUP-GOPLUS] {mint[:8]}: {_g_why} — блок входа.")
                            continue
                        from jupiter import JupiterAPI
                        price = await JupiterAPI.get_price(mint)
                        if price and price > 0:
                            _sig = analyzer.get_signal(mint)
                            _veto = analyzer.rocket_veto(mint, td or {}, _sig)
                            if _veto:
                                print(f"🚫 [JUP-VETO] {mint[:8]}: {_veto}")
                                continue
                            fixed = float(getattr(config, "TRADE_AMOUNT_USD", 0.0) or 0.0)
                            if fixed > 0:
                                size = fixed
                            else:
                                cap = tracker.get_total_capital()
                                size = max(4.0, min(100.0, cap * (config.REINVEST_PERCENT / 100.0))) if cap > 0 else 4.0
                                size *= analyzer.conviction_size_mult(td or {})
                            _base_tok = (td or {}).get("baseToken", {}) or {}
                            _sym = str(_base_tok.get("symbol") or mint[:6]).strip().upper()
                            _is_mature = _age_min >= 30.0
                            tracker.add_position(
                                _sym, mint, price, min(size, 100.0),
                                is_mature=_is_mature,
                                ml_features=analyzer.pack_features(td or {}),
                                ml_confidence=float(getattr(analyzer, "last_score", 0.0)),
                                source=f"JUP:{analyzer.get_signal(mint)}")
                            print(f"🪐 JUP BUY {_sym} ({mint[:8]}) @ ${price}")
                            break
                    await asyncio.sleep(1.0)
                if len(processed) > 1000:
                    processed.clear()
        except Exception as e:
            print(f"Ошибка в jup_loop: {e}")
        await asyncio.sleep(interval)
