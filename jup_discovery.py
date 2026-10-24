"""Jupiter Tokens API v2 — дискавери Solana: свежие листинги + топ трендов 5m.

Эндпоинты (docs: developers.jup.ag, нужен бесплатный x-api-key с portal.jup.ag):
- GET /tokens/v2/recent — токены с первым пулом (default 30)
- GET /tokens/v2/toptrending/5m?limit=100 — макс. движение за 5 мин
- GET /tokens/v2/toptraded/1h?limit=50 — макс. объём за час

Без JUP_API_KEY модуль использует public/fallback режим.
"""
import asyncio
import time

import config
from http_client import fetch_json

BASE = "https://api.jup.ag/tokens/v2"

_cache = []
_cache_time = 0.0
CACHE_TTL = 30
SKIP_SYMBOLS = {
    "SOL", "WSOL", "USDC", "USDT", "USDG", "EURC", "CBBTC", "WBTC", "WETH", "JLP", "PYTH", "JUP", "NEAR",
    "ZEC", "WNEAR", "CARDS", "PUMP", "HYPE", "ZEREBRO", "TTWO", "SPCX", "SPYX", "TSLAX", "NVDAX", "GROKIUS",
    "OREO", "RENDER", "BONK", "WIF", "POPCAT", "MEW", "BOME", "DRIFT", "RAY", "ORCA", "JTO", "KMNO"
}

# Реестр токенов на созревании ("flash-rug зона, ждём").
# Хранит mint -> {"ready_at": timestamp, "sym": str, "min_age": float, "added_at": float}
# Быстрый чекер опрашивает их каждые 2с сразу по наступлению ready_at.
_immature_registry: dict = {}
_buy_lock = asyncio.Lock()


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
            if not m or m.startswith("0x") or len(m) < 32 or len(m) > 44 or m in seen or sym in SKIP_SYMBOLS:
                continue
            st = t.get("stats5m") or {}
            pc = float(st.get("priceChange") or 0.0)
            vol = float(st.get("buyVolume") or 0.0) + float(st.get("sellVolume") or 0.0)
            # Отсекаем шум: если за 5м рост < +2.5% или объем < $1500 — это не ракета
            if pc < 2.5 or vol < 1500.0:
                continue
            seen.add(m)
            ranked.append((pc, vol, sym, m, "trending"))
    # Сортируем топ-тренды по максимальному 5м импульсу цены:
    ranked.sort(key=lambda x: x[0], reverse=True)
    
    # 2. Свежие листинги с пулом (recent)
    if s2 == 200 and isinstance(d2, list):
        for t in d2:
            m = t.get("id", "")
            sym = str(t.get("symbol") or "").upper().strip()
            if not m or m.startswith("0x") or len(m) < 32 or len(m) > 44 or m in seen or sym in SKIP_SYMBOLS:
                continue
            seen.add(m)
            ranked.append((0.0, 0.0, sym, m, "recent"))
            
    # 3. Топ по объему за 1 час (только с подтвержденным 5м импульсом!)
    if s3 == 200 and isinstance(d3, list):
        for t in d3:
            m = t.get("id", "")
            sym = str(t.get("symbol") or "").upper().strip()
            if not m or m.startswith("0x") or len(m) < 32 or len(m) > 44 or m in seen or sym in SKIP_SYMBOLS:
                continue
            st = t.get("stats5m") or {}
            pc = float(st.get("priceChange") or 0.0)
            vol = float(st.get("buyVolume") or 0.0) + float(st.get("sellVolume") or 0.0)
            if pc < 3.0 or vol < 3000.0:
                continue
            seen.add(m)
            ranked.append((pc, 0.0, sym, m, "volume"))
            
    if not ranked:
        # Fallback для датацентров (Render), если Jupiter API вернул 401/403/пусто без ключа:
        # добираем 5m тренды и новые пулы Solana через GeckoTerminal
        print(f"⚠️ [JUPITER] API вернул статусы ({s1}, {s2}, {s3}). Включаю fallback GT duration=5m + new_pools...")
        try:
            from market_data import _gt_get
            # Свежие 5m импульсы GT (ловят пампы вроде zonki)
            gt_data5 = await _gt_get("/networks/solana/trending_pools?duration=5m", retries=0, timeout=6)
            for item in ((gt_data5 or {}).get("data") or []):
                bid = item.get("relationships", {}).get("base_token", {}).get("data", {}).get("id", "")
                addr = bid.split("_", 1)[1] if "_" in bid else ""
                attrs = item.get("attributes") or {}
                sym = str(attrs.get("name") or "").upper().strip()
                if not addr or addr.startswith("0x") or len(addr) < 32 or len(addr) > 44 or addr in seen or any(s in sym for s in SKIP_SYMBOLS):
                    continue
                seen.add(addr)
                pc = attrs.get("price_change_percentage") or {}
                m5_c = float(pc.get("m5") or 0)
                ranked.append((m5_c, 0.0, sym, addr, "gt_trending_5m"))
            # Свежие пулы
            gt_data_new = await _gt_get("/networks/solana/new_pools", retries=0, timeout=6)
            for item in ((gt_data_new or {}).get("data") or []):
                bid = item.get("relationships", {}).get("base_token", {}).get("data", {}).get("id", "")
                addr = bid.split("_", 1)[1] if "_" in bid else ""
                attrs = item.get("attributes") or {}
                sym = str(attrs.get("name") or "").upper().strip()
                if not addr or addr.startswith("0x") or len(addr) < 32 or len(addr) > 44 or addr in seen or any(s in sym for s in SKIP_SYMBOLS):
                    continue
                seen.add(addr)
                pc = attrs.get("price_change_percentage") or {}
                m5_c = float(pc.get("m5") or 0)
                ranked.append((m5_c, 0.0, sym, addr, "gt_new_pools"))
        except Exception as e:
            print(f"⚠️ [JUPITER-FALLBACK] ошибка GT: {e}")

    out = [m for _, _, _, m, _ in ranked][:120]
    _cache, _cache_time = out, time.time()
    return out


async def _evaluate_and_buy(mint: str, td: dict, analyzer, tracker, source_tag: str = "JUP") -> bool:
    """Полная проверка безопасности, ликвидности и вход в сделку. Потокобезопасна."""
    if not td or len(tracker.get_open_positions()) >= config.MAX_CONCURRENT_POSITIONS:
        return False
    _base_tok = (td.get("baseToken") or {})
    _sym_tmp = str(_base_tok.get("symbol") or mint[:6]).strip().upper()
    
    if td.get("_price_divergence"):
        _div_r = float(td.get("_price_divergence_ratio", 0.0) or 0.0)
        print(f"🚫 [{source_tag}-PRICE-SPLIT] {_sym_tmp} ({mint[:8]}): рассинхрон пулов {_div_r:.1f}x — блок.")
        return False
        
    import goplus as _gp
    _sol_onchain_ok, _sol_onchain_why = await _gp.check_onchain_solana_security(mint)
    if not _sol_onchain_ok:
        print(f"🚫 [{source_tag}-FREEZE] {_sym_tmp} ({mint[:8]}): {_sol_onchain_why} — блок входа.")
        return False
    _g_ok, _g_why = await _gp.check_solana(mint)
    if not _g_ok:
        print(f"🚫 [{source_tag}-GOPLUS] {_sym_tmp} ({mint[:8]}): {_g_why} — блок входа.")
        return False
        
    _tx5_j = ((td or {}).get("txns") or {}).get("m5") or {}
    _b5_j = int(_tx5_j.get("buys", 0) or 0)
    _s5_j = int(_tx5_j.get("sells", 0) or 0)
    if _s5_j == 0 and _b5_j >= 5:
        print(f"🚫 [{source_tag}-HONEYPOT] {_sym_tmp} ({mint[:8]}): 0 продаж при {_b5_j} покупках — Honeypot (нельзя продать).")
        return False
        
    _liq_j = float(((td or {}).get("liquidity") or {}).get("usd", 0) or 0)
    _dex_j = str((td or {}).get("dexId", "")).lower()
    if _dex_j in ("pump", "pumpfun"):
        _fdv_j = float((td or {}).get("fdv", 0) or (td or {}).get("marketCap", 0) or 0)
        _liq_j = max(_liq_j, _fdv_j * 0.40)
    if _liq_j < 8000:
        print(f"🚫 [{source_tag}-LOW-LIQ] {_sym_tmp} ({mint[:8]}): ликвидность ${_liq_j:.0f} < $8,000 — микро-пул/риск слива.")
        return False
    if (_b5_j + _s5_j) < 6:
        print(f"🚫 [{source_tag}-LOW-TXNS] {_sym_tmp} ({mint[:8]}): всего {_b5_j + _s5_j} сделок в m5 — нет активности.")
        return False

    # Защита от миграционного дампа Pump.fun
    if mint.endswith("pump") and _dex_j in ("pumpswap", "raydium", "meteora", "orca"):
        _c_ms_j = (td or {}).get("pairCreatedAt") or 0
        import time as _t
        _age_j = ((_t.time() * 1000 - _c_ms_j) / 60000.0) if _c_ms_j else 0.5
        if _age_j < 3.5:
            print(f"⏳ [{source_tag}-MIGRATION-DUMP] {_sym_tmp} ({mint[:8]}): AMM-пул создан {_age_j:.1f}м назад (< 3.5м) — зона сброса снайперов pump.fun.")
            return False
        _fdv_j = float((td or {}).get("fdv", 0) or (td or {}).get("marketCap", 0) or 0)
        if 0 < _fdv_j < 42000.0:
            print(f"🚫 [{source_tag}-MIGRATION-LOW-FDV] {_sym_tmp} ({mint[:8]}): FDV ${_fdv_j:,.0f} < $42,000 — токен упал ниже уровня выпуска pump.fun.")
            return False
        if _liq_j < 12000.0:
            print(f"🚫 [{source_tag}-MIGRATION-LOW-LIQ] {_sym_tmp} ({mint[:8]}): ликвидность ${_liq_j:,.0f} < $12,000 — дев/снайперы высосали SOL.")
            return False
        
    from jupiter import JupiterAPI
    ds_price = float((td or {}).get("priceUsd", 0) or 0.0)
    jup_price = await JupiterAPI.get_price(mint)
    if ds_price > 0 and jup_price and jup_price > 0:
        ratio = max(ds_price, jup_price) / min(ds_price, jup_price)
        if ratio > 1.25:
            print(f"🚫 [{source_tag}-PRICE-MISMATCH] {_sym_tmp} ({mint[:8]}): Jupiter (${jup_price:.8f}) vs Dex (${ds_price:.8f}) расхождение {ratio:.1f}x — блок.")
            return False
        if jup_price < ds_price * 0.90:
            analyzer._deny(mint, "jup-live-dump >10%", f"🚫 [{source_tag}-LIVE-DUMP] {_sym_tmp} ({mint[:8]}): живая котировка Jupiter (${jup_price:.8f}) уже ниже DexScreener (${ds_price:.8f}) на {(1.0 - jup_price/ds_price)*100:.1f}% [>10%] — слив в процессе!")
            return False
        price = jup_price
    elif ds_price > 0:
        price = ds_price
    elif jup_price and jup_price > 0:
        price = jup_price
    else:
        price = 0.0

    if not price or price <= 0:
        return False

    _sig = analyzer.get_signal(mint)
    _veto = analyzer.rocket_veto(mint, td or {}, _sig)
    if _veto:
        analyzer._deny(mint, f"jup-veto: {_veto[:20]}", f"🚫 [{source_tag}-VETO] {_sym_tmp} ({mint[:8]}): {_veto}")
        return False

    async with _buy_lock:
        if len(tracker.get_open_positions()) >= config.MAX_CONCURRENT_POSITIONS:
            return False
        if getattr(config, "DYNAMIC_REINVEST_ENABLED", True):
            size = tracker.calculate_dynamic_position_size(td or {}, chain="solana")
            if size <= 0:
                return False
        else:
            fixed = float(getattr(config, "TRADE_AMOUNT_USD", 0.0) or 0.0)
            if fixed > 0:
                size = fixed
            else:
                cap = tracker.get_total_capital()
                size *= analyzer.conviction_size_mult(td or {})
        if "VIP-" in _sig:
            size = min(size, float(getattr(config, "TG_VIP_SIZE_USD", 15.0) or 15.0))
        tracker.add_position(
            _sym_tmp, mint, price, min(size, 100.0),
            is_mature=True,
            ml_features=analyzer.pack_features(td or {}),
            ml_confidence=float(getattr(analyzer, "last_score", 0.0)),
            source=f"{source_tag}:{analyzer.get_signal(mint)}",
            pair_address=((td or {}).get("pairAddress", "") or ""))
        print(f"🪐 {source_tag} BUY {_sym_tmp} ({mint[:8]}) @ ${price} [size: ${min(size, 100.0):.1f}]")
        return True


async def jup_fast_mature_loop(analyzer, tracker):
    """Быстрый чекер созревания токенов (ECAT фикс).
    Опрашивает токены из _immature_registry сразу по готовности (ready_at),
    не дожидаясь 13 минут следующего большого цикла Jupiter.
    """
    print("⚡ [JUPITER] Быстрый чекер созревания молодых токенов запущен (интервал 2с).")
    while True:
        try:
            await asyncio.sleep(2.0)
            if not _immature_registry:
                continue
            now = time.time()
            # Чистим старые записи (>15 минут)
            expired = [m for m, d in _immature_registry.items() if now - d.get("added_at", now) > 900]
            for m in expired:
                _immature_registry.pop(m, None)

            ready_mints = [m for m, d in _immature_registry.items() if now >= d.get("ready_at", 0)]
            if not ready_mints:
                continue

            for mint in ready_mints:
                info = _immature_registry.pop(mint, None)
                if not info:
                    continue
                if len(tracker.get_open_positions()) >= config.MAX_CONCURRENT_POSITIONS:
                    continue
                sym = info.get("sym", mint[:6])
                print(f"⚡ [JUP-MATURE-READY] {sym} ({mint[:8]}): время выдержки вышло, немедленный анализ!")
                td = await analyzer.fetch_token_data(mint)
                if not td:
                    print(f"⏭ [JUP-SKIP] {sym} ({mint[:8]}): нет данных рынка при повторе (no_td)")
                    continue
                try:
                    ok = await analyzer.analyze_token(mint)
                except Exception as e:
                    print(f"🪐 JUP mature analyze err {mint[:8]}: {e}")
                    continue
                if ok is None:
                    print(f"⏭ [JUP-SKIP] {sym} ({mint[:8]}): анализ созревшего токена не дал сигнала (eval_none)")
                    continue
                analyzer.log_scan(mint[:8], mint, "JUP-MATURE", ok, getattr(analyzer, "last_score", 0.0))
                if ok is True:
                    await _evaluate_and_buy(mint, td, analyzer, tracker, source_tag="JUP-MATURE")
        except Exception as e:
            print(f"⚠️ Ошибка в jup_fast_mature_loop: {e}")


async def jup_loop(analyzer, tracker):
    """Петля Jupiter-дискавери (Solana). Работает автономно, использует JUP_API_KEY при наличии.
    Поддерживает параллельный анализ (Semaphore 5) и приоритизацию молодых/памп-токенов.
    """
    _has_key = bool(getattr(config, "JUP_API_KEY", "") or "")
    print(f"🪐 Jupiter Discovery запущен: toptrending/5m + recent + toptraded/1h (Solana) [{'API-KEY активен' if _has_key else 'Public'}]!")
    
    # Запускаем быстрый чекер созревающих токенов в фоне
    asyncio.create_task(jup_fast_mature_loop(analyzer, tracker))

    processed = {}
    interval = int(getattr(config, "JUP_INTERVAL", 30))
    sem = asyncio.Semaphore(5)

    while True:
        try:
            if len(tracker.get_open_positions()) < config.MAX_CONCURRENT_POSITIONS:
                import time as _t
                mints = await fetch_jup_tokens()
                if mints:
                    print(f"🪐 [JUPITER] Получено {len(mints)} кандидатов (toptrending + recent). Пакетный анализ...")
                    try:
                        _to_pf = [m for m in mints if m not in tracker.positions and (_t.time() - processed.get(m, 0.0)) >= 35]
                        if _to_pf:
                            await analyzer.prefetch_token_data(_to_pf, ttl=60.0)
                    except Exception:
                        pass

                    # ⚡ ПАКЕТНЫЙ СКРИНИНГ В ПАМЯТИ (Batch Triage):
                    # Отсеиваем весь мусор пачкой за 0.01 сек без спама в лог и без лишних сетевых запросов!
                    hot_candidates = []
                    skipped_flat = 0
                    skipped_ancient = 0
                    skipped_nodata = 0
                    immature_count = 0
                    _now_t = _t.time()
                    _min_age = float(getattr(config, "SOLANA_MIN_AGE_MIN", 2.0))

                    for m in mints:
                        if not m or m in tracker.positions:
                            continue
                        if _now_t - processed.get(m, 0.0) < 35:
                            continue

                        hit = getattr(analyzer, "_prefetch_cache", {}).get(m)
                        td = hit[1] if (hit and hit[1]) else None
                        if not td:
                            skipped_nodata += 1
                            continue

                        _cr = td.get("pairCreatedAt") or 0
                        _ag = (_now_t * 1000 - _cr) / 60000.0 if _cr else 999.0
                        _m5 = float(((td.get("priceChange") or {}).get("m5", 0) or 0))
                        _sym = str(((td.get("baseToken") or {}).get("symbol") or m[:6])).upper()

                        # Зона созревания: отправляем в быстрый чекер
                        if _ag < _min_age:
                            immature_count += 1
                            created_sec = (_cr / 1000.0) if _cr else (_now_t - _ag * 60.0)
                            ready_at = created_sec + (_min_age * 60.0) + 5.0
                            _immature_registry[m] = {
                                "ready_at": ready_at,
                                "sym": _sym,
                                "min_age": _min_age,
                                "added_at": _now_t
                            }
                            continue

                        # Отсекаем древние мамонты без пампа
                        if _ag > 120.0 and _m5 < 15.0:
                            skipped_ancient += 1
                            processed[m] = _now_t + 1800
                            continue

                        # Отсекаем флет m5 < 2.5%
                        if _m5 < 2.5:
                            skipped_flat += 1
                            processed[m] = _now_t + 120
                            continue

                        hot_candidates.append((_m5, _ag, _sym, m, td))

                    # Сортируем по максимальному импульсу m5 (самые мощные ракеты первыми)
                    hot_candidates.sort(key=lambda x: x[0], reverse=True)

                    total_skipped = skipped_flat + skipped_ancient + skipped_nodata
                    print(f"📦 [JUP BATCH] {len(mints)} токенов: отсеяно пачкой {total_skipped} "
                          f"({skipped_flat} флет, {skipped_ancient} старых, {skipped_nodata} без пула), "
                          f"созревают {immature_count}. Отобрано ТОП-{len(hot_candidates)} кандидатов с импульсом!")

                    async def _process_hot_candidate(cand):
                        async with sem:
                            _m5_val, _ag_val, _sym_val, mint, td = cand
                            if not mint or mint in tracker.positions:
                                return
                            if len(tracker.get_open_positions()) >= config.MAX_CONCURRENT_POSITIONS:
                                return

                            # Кулдаун на повторный вход после жесткого стопа (-15%+):
                            _prev = tracker.last_loss_pct(mint)
                            if _prev is not None and _prev <= getattr(config, "RUG_REBUY_MAX_LOSS", -0.15):
                                _old_pos = tracker.positions.get(mint) or tracker.positions.get(mint + "_old_1")
                                _exit_t = getattr(_old_pos, "exit_time", 0.0) or 0.0
                                if _exit_t and (_t.time() - _exit_t) < 7200:
                                    return

                            print(f"🪐 [JUP] Анализирую {_sym_val} ({mint[:8]}): возраст {_ag_val:.1f}м, m5 {_m5_val:+.1f}%...")
                            try:
                                ok = await analyzer.analyze_token(mint)
                            except Exception as e:
                                print(f"🪐 JUP analyze err {mint[:8]}: {type(e).__name__}")
                                return

                            if ok is None:
                                return

                            processed[mint] = _t.time()
                            analyzer.log_scan(mint[:8], mint, "JUP", ok, getattr(analyzer, "last_score", 0.0))

                            if ok is True:
                                await _evaluate_and_buy(mint, td, analyzer, tracker, source_tag="JUP")

                    tasks = [_process_hot_candidate(c) for c in hot_candidates]
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)

                    if len(processed) > 1000:
                        processed.clear()
        except Exception as e:
            print(f"Ошибка в jup_loop: {e}")
        await asyncio.sleep(interval)
