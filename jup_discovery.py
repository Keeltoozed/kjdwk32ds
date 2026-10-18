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
CACHE_TTL = 30
SKIP_SYMBOLS = {"SOL", "WSOL", "USDC", "USDT", "USDG", "EURC", "CBBTC", "WBTC", "WETH", "JLP", "PYTH", "JUP", "NEAR"}


def _headers():
    key = getattr(config, "JUP_API_KEY", "") or ""
    return {"x-api-key": key} if key else {}


async def fetch_jup_tokens() -> list:
    """Возвращает [mint, ...] Solana: приоритет взрывным ракетам (toptrending m5) + свежим пулам (recent)."""
    global _cache, _cache_time
    if time.time() - _cache_time < CACHE_TTL and _cache:
        return _cache
    
    trending_url = f"{BASE}/toptrending/5m?limit=100"
    recent_url = f"{BASE}/recent"
    top_vol_url = f"{BASE}/toptraded/1h?limit=50"
    
    headers = _headers()
    s1, d1 = await fetch_json(trending_url, headers=headers, timeout=10)
    s2, d2 = await fetch_json(recent_url, headers=headers, timeout=10)
    s3, d3 = await fetch_json(top_vol_url, headers=headers, timeout=10)
    
    ranked = []
    seen = set()
    
    # 1. Топ-тренды по росту за 5 минут — самые горячие взрывные ракеты первыми!
    if s1 == 200 and isinstance(d1, list):
        for t in d1:
            m = t.get("id", "")
            sym = str(t.get("symbol") or "").upper().strip()
            if not m or m in seen or sym in SKIP_SYMBOLS:
                continue
            seen.add(m)
            st = t.get("stats5m") or {}
            pc = float(st.get("priceChange") or 0.0)
            vol = float(st.get("buyVolume") or 0.0) + float(st.get("sellVolume") or 0.0)
            ranked.append((pc, vol, sym, m, "trending"))
    # Сортируем топ-тренды по максимальному 5м импульсу цены:
    ranked.sort(key=lambda x: x[0], reverse=True)
    
    # 2. Свежие листинги с пулом (recent)
    if s2 == 200 and isinstance(d2, list):
        for t in d2:
            m = t.get("id", "")
            sym = str(t.get("symbol") or "").upper().strip()
            if not m or m in seen or sym in SKIP_SYMBOLS:
                continue
            seen.add(m)
            ranked.append((0.0, 0.0, sym, m, "recent"))
            
    # 3. Топ по объему за 1 час
    if s3 == 200 and isinstance(d3, list):
        for t in d3:
            m = t.get("id", "")
            sym = str(t.get("symbol") or "").upper().strip()
            if not m or m in seen or sym in SKIP_SYMBOLS:
                continue
            seen.add(m)
            ranked.append((-1.0, 0.0, sym, m, "volume"))
            
    out = [m for _, _, _, m, _ in ranked][:120]
    _cache, _cache_time = out, time.time()
    return out


async def jup_loop(analyzer, tracker):
    """Петля Jupiter-дискавери (Solana). Работает автономно, использует JUP_API_KEY при наличии."""
    _has_key = bool(getattr(config, "JUP_API_KEY", "") or "")
    print(f"🪐 Jupiter Discovery запущен: toptrending/5m + recent + toptraded/1h (Solana) [{'API-KEY активен' if _has_key else 'Public'}]!")
    processed = {}
    interval = int(getattr(config, "JUP_INTERVAL", 30))
    while True:
        try:
            if len(tracker.get_open_positions()) < config.MAX_CONCURRENT_POSITIONS:
                import time as _t
                mints = await fetch_jup_tokens()
                if mints:
                    print(f"🪐 [JUPITER] Получено {len(mints)} кандидатов (toptrending + recent). Сканирую...")
                for mint in mints:
                    if not mint or mint in tracker.positions:
                        continue
                    # Кулдаун на повторный вход после жесткого стопа (-15%+):
                    _prev = tracker.last_loss_pct(mint)
                    if _prev is not None and _prev <= getattr(config, "RUG_REBUY_MAX_LOSS", -0.15):
                        _old_pos = tracker.positions.get(mint) or tracker.positions.get(mint + "_old_1")
                        _exit_t = getattr(_old_pos, "exit_time", 0.0) or 0.0
                        if _exit_t and (_t.time() - _exit_t) < 7200:
                            continue
                    if _t.time() - processed.get(mint, 0.0) < 45:
                        continue
                    td = await analyzer.fetch_token_data(mint)
                    if not td:
                        continue
                    _created = td.get("pairCreatedAt") or 0
                    _age_min = (_t.time() * 1000 - _created) / 60000.0 if _created else 999.0
                    _min_age = float(getattr(config, "SOLANA_MIN_AGE_MIN", 2.5))
                    _sym_tmp = str(((td.get("baseToken") or {}).get("symbol") or mint[:6])).upper()
                    
                    if _age_min < _min_age:
                        print(f"⏳ [JUP] {_sym_tmp} ({mint[:8]}): возраст {_age_min:.1f}м < {_min_age}м — flash-rug зона дева, ждём.")
                        continue
                        
                    print(f"🪐 [JUP] Анализирую {_sym_tmp} ({mint[:8]}): возраст {_age_min:.1f}м...")
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
                        if td and td.get("_price_divergence"):
                            _div_r = float(td.get("_price_divergence_ratio", 0.0) or 0.0)
                            print(f"🚫 [JUP-PRICE-SPLIT] {_sym_tmp} ({mint[:8]}): рассинхрон пулов {_div_r:.1f}x — блок.")
                            continue
                        import goplus as _gp
                        _g_ok, _g_why = await _gp.check_solana(mint)
                        if not _g_ok:
                            print(f"🚫 [JUP-GOPLUS] {_sym_tmp} ({mint[:8]}): {_g_why} — блок входа.")
                            continue
                        from jupiter import JupiterAPI
                        ds_price = float((td or {}).get("priceUsd", 0) or 0.0)
                        jup_price = await JupiterAPI.get_price(mint)
                        if ds_price > 0 and jup_price and jup_price > 0:
                            ratio = max(ds_price, jup_price) / min(ds_price, jup_price)
                            if ratio > 1.25:
                                print(f"🚫 [JUP-PRICE-MISMATCH] {_sym_tmp} ({mint[:8]}): Jupiter (${jup_price:.8f}) vs Dex (${ds_price:.8f}) расхождение {ratio:.1f}x — блок.")
                                continue
                            if jup_price < ds_price * 0.965:
                                print(f"🚫 [JUP-LIVE-DUMP] {_sym_tmp} ({mint[:8]}): живая котировка Jupiter (${jup_price:.8f}) уже ниже DexScreener (${ds_price:.8f}) на {(jup_price/ds_price - 1)*100:+.1f}% — слив в процессе!")
                                continue
                            price = jup_price
                        elif ds_price > 0:
                            price = ds_price
                        elif jup_price and jup_price > 0:
                            price = jup_price
                        else:
                            price = 0.0

                        if price and price > 0:
                            _sig = analyzer.get_signal(mint)
                            _veto = analyzer.rocket_veto(mint, td or {}, _sig)
                            if _veto:
                                print(f"🚫 [JUP-VETO] {_sym_tmp} ({mint[:8]}): {_veto}")
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
                            tracker.add_position(
                                _sym, mint, price, min(size, 100.0),
                                is_mature=True,
                                ml_features=analyzer.pack_features(td or {}),
                                ml_confidence=float(getattr(analyzer, "last_score", 0.0)),
                                source=f"JUP:{analyzer.get_signal(mint)}",
                                pair_address=((td or {}).get("pairAddress", "") or ""))
                            print(f"🪐 JUP BUY {_sym} ({mint[:8]}) @ ${price} [size: ${min(size, 100.0):.1f}]")
                            break
                    await asyncio.sleep(0.15)
                if len(processed) > 1000:
                    processed.clear()
        except Exception as e:
            print(f"Ошибка в jup_loop: {e}")
        await asyncio.sleep(interval)
