"""Jupiter Tokens API v2 — дискавери Solana: свежие листинги + топ трендов 5m.

Эндпоинты (docs: developers.jup.ag, нужен бесплатный x-api-key с portal.jup.ag):
- GET /tokens/v2/recent — токены с первым пулом (default 30)
- GET /tokens/v2/toptrending/5m?limit=100 — макс. движение за 5 мин
- GET /tokens/v2/toptraded/1h?limit=50 — макс. объём за час

Без JUP_API_KEY модуль использует public/fallback режим.
"""
import asyncio
import re
import time

import config
from http_client import fetch_json

BASE = "https://api.jup.ag/tokens/v2"
LITE_BASE = "https://lite-api.jup.ag/tokens/v2"

_cache = []
_cache_time = 0.0
CACHE_TTL = 40
SKIP_SYMBOLS = {
    "SOL", "WSOL", "USDC", "USDT", "USDG", "EURC", "CBBTC", "WBTC", "WETH", "JLP", "PYTH", "JUP", "NEAR",
    "ZEC", "WNEAR", "CARDS", "PUMP", "HYPE", "ZEREBRO", "TTWO", "SPCX", "SPYX", "TSLAX", "NVDAX", "GROKIUS",
    "OREO", "RENDER", "BONK", "WIF", "POPCAT", "MEW", "BOME", "DRIFT", "RAY", "ORCA", "JTO", "KMNO"
}

# Реестр токенов на созревании ("flash-rug зона, ждём").
# Хранит mint -> {"ready_at": timestamp, "sym": str, "min_age": float, "added_at": float}
# Быстрый чекер опрашивает их каждые 2с сразу по наступлению ready_at.
_immature_registry: dict = {}
_buy_locks: dict = {}
_fetch_locks: dict = {}

def _get_buy_lock():
    try:
        loop = asyncio.get_running_loop()
        k = id(loop)
    except RuntimeError:
        k = 0
    if k not in _buy_locks:
        _buy_locks[k] = asyncio.Lock()
    return _buy_locks[k]


def _get_fetch_lock():
    try:
        loop = asyncio.get_running_loop()
        k = id(loop)
    except RuntimeError:
        k = 0
    if k not in _fetch_locks:
        _fetch_locks[k] = asyncio.Lock()
    return _fetch_locks[k]


def _headers():
    key = getattr(config, "JUP_API_KEY", "") or ""
    return {"x-api-key": key} if key else {}


def _parse_jup_age_min(t: dict) -> float:
    """Вычисляет возраст пула в минутах из graduatedAt (если мигрировал) или firstPool.createdAt ответа Jupiter API v2.
    Корректно обрабатывает миллисекунды (.246Z) в Python 3.9 без ложного фоллбэка в 30.0м."""
    try:
        ca = (
            t.get("graduatedAt")
            or (t.get("firstPool") or {}).get("createdAt")
            or t.get("createdAt")
            or ""
        ).strip()
        if not ca:
            return 999.0
        from datetime import datetime
        s = ca.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            dt = datetime.fromisoformat(re.sub(r"\.\d+", "", s))
        return max(0.0, (time.time() - dt.timestamp()) / 60.0)
    except Exception:
        return 999.0


async def fetch_jup_tokens() -> list:
    """Возвращает [mint, ...] Solana: живые ракеты Jupiter v2 (5m + 1h + recent) + DexScreener Active DEX Radar + GT 5m.
    Отсекает древние мамонты (>6ч без крупного прорыва и все >24ч) и сырые немигрировавшие кривые прямо на входе,
    чтобы пачка состояла из реальных выпускников PumpSwap / Meteora / Raydium.
    """
    global _cache, _cache_time
    if time.time() - _cache_time < CACHE_TTL and _cache:
        return _cache

    async with _get_fetch_lock():
        if time.time() - _cache_time < CACHE_TTL and _cache:
            return _cache

        headers = _headers()
        allow_raw_curve = bool(getattr(config, "ALLOW_UNGRADUATED_PUMPFUN", False))

        # 1. Jupiter Tokens API v2 (8 параллельных срезов: 5m, 1h, 6h тренды + органика + объем + recent)
        (s1, d1), (s_org, d_org), (s3, d3), (s1h, d1h), (s2, d2), (s_tr1h, d_tr1h), (s_org1h, d_org1h), (s6h, d6h) = await asyncio.gather(
            fetch_json(f"{BASE}/toptrending/5m?limit=100", headers=headers, timeout=8, retries=0),
            fetch_json(f"{BASE}/toporganicscore/5m?limit=100", headers=headers, timeout=8, retries=0),
            fetch_json(f"{BASE}/toptraded/5m?limit=100", headers=headers, timeout=8, retries=0),
            fetch_json(f"{BASE}/toptrending/1h?limit=100", headers=headers, timeout=8, retries=0),
            fetch_json(f"{BASE}/recent", headers=headers, timeout=8, retries=0),
            fetch_json(f"{BASE}/toptraded/1h?limit=100", headers=headers, timeout=8, retries=0),
            fetch_json(f"{BASE}/toporganicscore/1h?limit=100", headers=headers, timeout=8, retries=0),
            fetch_json(f"{BASE}/toptrending/6h?limit=100", headers=headers, timeout=8, retries=0),
        )
        # Авто-фоллбэк на бесплатный lite-api.jup.ag при 429/401/403 на основном хосте
        if s1 in (429, 401, 403) and s_org in (429, 401, 403):
            (s1, d1), (s_org, d_org), (s3, d3), (s1h, d1h), (s2, d2), (s_tr1h, d_tr1h), (s_org1h, d_org1h), (s6h, d6h) = await asyncio.gather(
                fetch_json(f"{LITE_BASE}/toptrending/5m?limit=100", timeout=8, retries=0),
                fetch_json(f"{LITE_BASE}/toporganicscore/5m?limit=100", timeout=8, retries=0),
                fetch_json(f"{LITE_BASE}/toptraded/5m?limit=100", timeout=8, retries=0),
                fetch_json(f"{LITE_BASE}/toptrending/1h?limit=100", timeout=8, retries=0),
                fetch_json(f"{LITE_BASE}/recent", timeout=8, retries=0),
                fetch_json(f"{LITE_BASE}/toptraded/1h?limit=100", timeout=8, retries=0),
                fetch_json(f"{LITE_BASE}/toporganicscore/1h?limit=100", timeout=8, retries=0),
                fetch_json(f"{LITE_BASE}/toptrending/6h?limit=100", timeout=8, retries=0),
            )

        ranked = []
        seen = set()

        def _add_jup_list(items, tag: str, min_pc5: float = 1.8):
            if not isinstance(items, list):
                return
            for t in items:
                if not isinstance(t, dict):
                    continue
                m = t.get("id", "")
                sym = str(t.get("symbol") or "").upper().strip()
                if not m or m.startswith("0x") or len(m) < 32 or len(m) > 44 or m in seen or sym in SKIP_SYMBOLS:
                    continue
                # На Solana торгуем ТОЛЬКО канонические launchpad-токены с сожжённым LP (pump.fun / moonshot)
                # Блокирует 100% кастомных рагпуллов (FRONK, SWJ, SIRIUS, HI, FLY, CRO, SI) прямо на входе в дискавери!
                if not m.endswith(("pump", "moon")):
                    continue
                liq = float(t.get("liquidity") or 0.0)
                is_grad = bool(t.get("graduatedAt") or t.get("graduatedPool") or (not m.endswith("pump") and liq >= 7500.0))
                if not allow_raw_curve and not is_grad:
                    continue
                if liq < (3500.0 if allow_raw_curve else 7500.0):
                    continue
                st5 = t.get("stats5m") or {}
                st1h = t.get("stats1h") or {}
                pc5 = float(st5.get("priceChange") or 0.0)
                pc1h = float(st1h.get("priceChange") or 0.0)
                vol5 = float(st5.get("buyVolume") or 0.0) + float(st5.get("sellVolume") or 0.0)
                b5 = int(st5.get("numBuys") or 0)
                s5 = int(st5.get("numSells") or 0)
                age_m = _parse_jup_age_min(t)
                # Жесткое отсечение древних мамонтов (>12ч полностью, >6ч только при реальном пробое ликвидного пула):
                if age_m > 720.0:
                    continue
                if age_m > 360.0 and (vol5 < 6000.0 or pc5 < 5.0 or liq < 25000.0):
                    continue
                if age_m > 60.0 and pc5 < 2.0:
                    continue
                _is_fresh_jup = age_m <= 4.0
                _max_pc1h = 2500.0 if (_is_fresh_jup and pc5 >= 2.0 and b5 >= s5) else float(getattr(config, "MAX_ENTRY_H1_PCT", 350.0))
                if (pc1h > _max_pc1h and age_m > 4.0) or (pc1h < -25.0 and pc5 < 12.0):
                    continue
                _min_v5_req = 4500.0 if _is_fresh_jup else float(getattr(config, "MIN_VOLUME_M5", 7500.0))
                if pc5 < min_pc5 or vol5 < _min_v5_req:
                    continue
                seen.add(m)
                ranked.append((pc5, vol5, sym, m, tag))

        if s_org == 200:
            _add_jup_list(d_org, "organic_5m", min_pc5=1.0)
        if s1 == 200:
            _add_jup_list(d1, "trending_5m", min_pc5=1.0)
        if s1h == 200:
            _add_jup_list(d1h, "trending_1h", min_pc5=1.2)
        if s3 == 200:
            _add_jup_list(d3, "traded_5m", min_pc5=1.0)
        if s2 == 200:
            _add_jup_list(d2, "recent", min_pc5=0.5)
        if s_tr1h == 200:
            _add_jup_list(d_tr1h, "traded_1h", min_pc5=1.2)
        if s_org1h == 200:
            _add_jup_list(d_org1h, "organic_1h", min_pc5=1.2)
        if s6h == 200:
            _add_jup_list(d6h, "trending_6h", min_pc5=1.8)

        # 2. Прямой DexScreener Rocket Radar (300 RPM, 0 нагрузки на GT):
        # Ловит живые выпускники PumpSwap / Meteora / Raydium / Bonk, когда топ-100 Jupiter забит старыми мейджорами!
        try:
            ds_queries = (
                "https://api.dexscreener.com/latest/dex/search?q=pumpswap",
                "https://api.dexscreener.com/latest/dex/search?q=SOL%20pump",
                "https://api.dexscreener.com/latest/dex/search?q=meteora%20SOL",
                "https://api.dexscreener.com/latest/dex/search?q=bonk%20SOL",
                "https://api.dexscreener.com/latest/dex/search?q=raydium%20SOL",
                "https://api.dexscreener.com/latest/dex/search?q=orca%20SOL",
            )
            ds_results = await asyncio.gather(
                *[fetch_json(u, timeout=6, retries=0) for u in ds_queries],
                return_exceptions=True,
            )
            now_ms_ds = time.time() * 1000.0
            for res in ds_results:
                if not isinstance(res, tuple) or len(res) != 2:
                    continue
                st_q, d_q = res
                if st_q != 200 or not isinstance(d_q, dict):
                    continue
                for p in (d_q.get("pairs") or []):
                    if not isinstance(p, dict) or str(p.get("chainId") or "").lower() != "solana":
                        continue
                    dex_p = str(p.get("dexId") or "").lower()
                    if not allow_raw_curve and dex_p in ("pump", "pumpfun", "moonshot", "meteoradbc"):
                        continue
                    bt = p.get("baseToken") or {}
                    addr = str(bt.get("address") or "").strip()
                    sym_p = str(bt.get("symbol") or "").upper().strip()
                    if not addr or addr.startswith("0x") or len(addr) < 32 or len(addr) > 44 or addr in seen or sym_p in SKIP_SYMBOLS:
                        continue
                    liq_p = float((p.get("liquidity") or {}).get("usd", 0) or 0)
                    if liq_p < 7500.0:
                        continue
                    vol5_p = float((p.get("volume") or {}).get("m5", 0) or 0)
                    if vol5_p < 2000.0:
                        continue
                    tx5_p = (p.get("txns") or {}).get("m5") or {}
                    b5_p = int(tx5_p.get("buys", 0) or 0)
                    s5_p = int(tx5_p.get("sells", 0) or 0)
                    if (b5_p + s5_p) < 12 or (s5_p > 0 and (b5_p / s5_p) < 1.08):
                        continue
                    pc_p = p.get("priceChange") or {}
                    m5_p = float(pc_p.get("m5", 0) or 0)
                    h1_p = float(pc_p.get("h1", 0) or 0)
                    if m5_p < 1.8:
                        continue
                    ca_p = float(p.get("pairCreatedAt") or 0)
                    age_p = ((now_ms_ds - ca_p) / 60000.0) if ca_p > 0 else 999.0
                    if age_p > 720.0:
                        continue
                    if age_p > 360.0 and (vol5_p < 6000.0 or m5_p < 5.0 or liq_p < 25000.0):
                        continue
                    _is_fresh_ds = age_p <= 4.0
                    _max_h1_ds = 2500.0 if (_is_fresh_ds and b5_p >= s5_p * 1.15) else float(getattr(config, "MAX_ENTRY_H1_PCT", 350.0))
                    if h1_p > _max_h1_ds and age_p > 4.0:
                        continue
                    seen.add(addr)
                    ranked.append((m5_p, vol5_p, sym_p, addr, "ds_radar"))
        except Exception:
            pass

        # 3. Добор живых трендов и свежих пулов PumpSwap из общего кэша market_data (без дублирования запросов и 429)
        try:
            import market_data
            gt_items = await market_data.get_trending()
            for item in (gt_items or []):
                addr = item.get("tokenAddress") or ""
                if not addr or addr.startswith("0x") or len(addr) < 32 or len(addr) > 44 or addr in seen:
                    continue
                seen.add(addr)
                ranked.append((5.0, 0.0, addr[:6], addr, "gt_trending"))
        except Exception:
            pass

        # 4. DexScreener Top Boosts & Latest Profiles ВСЕГДА (не только fallback!):
        # Именно здесь крутятся вирусные хайп-монеты с реальным маркетингом и вниманием рынка!
        try:
            ds_boost_urls = (
                "https://api.dexscreener.com/token-boosts/top/v1",
                "https://api.dexscreener.com/token-boosts/latest/v1",
                getattr(config, "DEXSCREENER_LATEST", "https://api.dexscreener.com/token-boosts/latest/v1"),
            )
            ds_b_res = await asyncio.gather(
                *[fetch_json(u, timeout=5, retries=0) for u in ds_boost_urls],
                return_exceptions=True
            )
            for res in ds_b_res:
                if isinstance(res, tuple) and len(res) == 2 and res[0] == 200 and isinstance(res[1], list):
                    for item in res[1]:
                        if item.get("chainId") != "solana":
                            continue
                        addr = str(item.get("tokenAddress") or "").strip()
                        if not addr or addr.startswith("0x") or len(addr) < 32 or len(addr) > 44 or addr in seen or str(item.get("symbol") or "").upper() in SKIP_SYMBOLS:
                            continue
                        seen.add(addr)
                        # Топ-бусты получают высокий базовый моментум для попадания в топ очереди
                        ranked.append((8.0, 25000.0, addr[:6], addr, "ds_top_boost"))
        except Exception:
            pass

        # 5. Свежие мигрировавшие листинги (recent) — для отправки молодых выпускников в реестр созревания
        if s2 == 200 and isinstance(d2, list):
            for t in d2:
                if not isinstance(t, dict):
                    continue
                m = t.get("id", "")
                sym = str(t.get("symbol") or "").upper().strip()
                if not m or m.startswith("0x") or len(m) < 32 or len(m) > 44 or m in seen or sym in SKIP_SYMBOLS:
                    continue
                # На Solana торгуем ТОЛЬКО канонические launchpad-токены с сожжённым LP (pump.fun / moonshot)
                # Блокирует 100% кастомных рагпуллов (FRONK, SWJ, SIRIUS, HI, FLY, CRO, SI) прямо на входе в дискавери!
                if not m.endswith(("pump", "moon")):
                    continue
                liq = float(t.get("liquidity") or 0.0)
                is_grad_r = bool(t.get("graduatedAt") or t.get("graduatedPool") or (not m.endswith("pump") and liq >= 7500.0))
                if not allow_raw_curve and not is_grad_r:
                    continue
                if liq < 5000.0:
                    continue
                st5 = t.get("stats5m") or {}
                pc5 = float(st5.get("priceChange") or 0.0)
                seen.add(m)
                ranked.append((pc5, 0.0, sym, m, "recent"))

        # Сортируем все импульсные монеты по РОКЕТ-ВЕЛОСИТИ (импульс свечи * логарифм объема * тег-бонус):
        # Настоящие ракеты с объёмом $50k-$500k и бустами ВСЕГДА идут первыми перед тихими $1k щиткоинами!
        import math
        def _rocket_velocity(x):
            # x = (pc5, vol5, sym, m, tag)
            pc = max(float(x[0]), 0.1)
            vol = max(float(x[1]), 100.0)
            tag = str(x[4])
            tag_mult = 2.0 if ("top_boost" in tag or "ds_radar" in tag) else 1.0
            return pc * math.log10(vol) * tag_mult

        ranked.sort(key=_rocket_velocity, reverse=True)

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
    _dex_j = str((td or {}).get("dexId", "")).lower()
    _is_raw_curve_j = _dex_j in ("pump", "pumpfun", "moonshot", "meteoradbc") or mint.endswith("moon")
    _raw_liq_j = float(((td or {}).get("liquidity") or {}).get("usd", 0) or 0)
    _cr_j = (td or {}).get("pairCreatedAt") or 0
    _age_j = (time.time() * 1000 - _cr_j) / 60000.0 if _cr_j else 999.0
    if _is_raw_curve_j:
        if not getattr(config, "ALLOW_UNGRADUATED_PUMPFUN", False):
            print(f"🚫 [{source_tag}-RAW-CURVE] {_sym_tmp} ({mint[:8]}): сырая кривая bonding curve без миграции на DEX — вход запрещён.")
            return False
        _fdv_j = float((td or {}).get("fdv", 0) or (td or {}).get("marketCap", 0) or 0)
        _min_c_mc = float(getattr(config, "MIN_PUMP_CURVE_MCAP_USD", 7500.0))
        _max_c_mc = float(getattr(config, "MAX_PUMP_CURVE_MCAP_USD", 42000.0))
        if _fdv_j > 0 and _fdv_j < _min_c_mc:
            print(f"🚫 [{source_tag}-CURVE-FLASH-RUG] {_sym_tmp} ({mint[:8]}): FDV ${_fdv_j:,.0f} < ${_min_c_mc:,.0f} (пустой кёрв, flash-rug риск) — блок.")
            return False
        if _fdv_j > _max_c_mc:
            print(f"🚫 [{source_tag}-CURVE-CLIFF] {_sym_tmp} ({mint[:8]}): FDV ${_fdv_j:,.0f} > ${_max_c_mc:,.0f} (обрыв перед миграцией) — блок.")
            return False
        _liq_j = max(_raw_liq_j, _fdv_j * 0.40)
    else:
        _liq_j = _raw_liq_j
    _min_liq_req = 1000.0 if _is_raw_curve_j else float(getattr(config, "MIN_GRADUATED_LIQ_USD", 7500.0))
    if _liq_j < _min_liq_req:
        print(f"🚫 [{source_tag}-LOW-LIQ] {_sym_tmp} ({mint[:8]}): реальная ликвидность ${_liq_j:.0f} < ${_min_liq_req:.0f} — микро-пул/риск слива.")
        return False
    _tot_tx_j = _b5_j + _s5_j
    if _tot_tx_j < 16:
        print(f"🚫 [{source_tag}-LOW-TXNS] {_sym_tmp} ({mint[:8]}): всего {_tot_tx_j} сделок в m5 (<16) — вялый боковой стакан.")
        return False
    _vol5_j = float(((td or {}).get("volume") or {}).get("m5", 0) or 0)
    _min_vol_j = 2000.0 if (_b5_j >= int(_s5_j * 1.05) or _age_j <= 15.0) else 4000.0
    if _vol5_j < _min_vol_j:
        print(f"🚫 [{source_tag}-LOW-VOL] {_sym_tmp} ({mint[:8]}): 5м объем ${_vol5_j:,.0f} < ${_min_vol_j:,.0f} — слабый стоячий токен (кейс BRO).")
        return False

    # Защита от перегретого хая, падающего ножа и слива от пика
    _pc_j = (td or {}).get("priceChange") or {}
    _m5_j = float(_pc_j.get("m5", 0) or 0)
    _m1_j = float(_pc_j.get("m1", 0) or 0)
    if abs(_m1_j) > max(180.0, abs(_m5_j) * 3.0 + 40.0):
        _m1_j = _m5_j / 5.0
    _max_m1_cfg_j = float(getattr(config, "MAX_ENTRY_M1_PCT", 28.0))
    if _m1_j > _max_m1_cfg_j and _age_j >= 3.0:
        if not (_b5_j >= int(_s5_j * 1.15) and _m1_j <= 45.0):
            print(f"🚫 [{source_tag}-M1-SPIKE] {_sym_tmp} ({mint[:8]}): m1 +{_m1_j:.1f}% > +{_max_m1_cfg_j:.1f}% без перевеса покупателей — отмена входа.")
            return False
    _h1_j = float(_pc_j.get("h1", 0) or 0)
    _is_fresh_j = _age_j <= 4.0
    _max_h1_j = 2500.0 if (_is_fresh_j and _m5_j >= 2.5 and _b5_j >= _s5_j * 1.15) else float(getattr(config, "MAX_ENTRY_H1_PCT", 350.0))
    if _h1_j > _max_h1_j and _age_j > 4.0:
        print(f"🚫 [{source_tag}-H1-EXHAUSTED] {_sym_tmp} ({mint[:8]}): h1 {_h1_j:+.0f}% > +{_max_h1_j:.0f}% — памп уже отработал, вход = вершина.")
        return False
    _min_m1_j = -2.8 if _m5_j < 12.0 else -6.0
    if _m5_j < 1.5 or _m1_j < _min_m1_j:
        print(f"🚫 [{source_tag}-MOMENTUM-LOST] {_sym_tmp} ({mint[:8]}): импульс пропал (m5 {_m5_j:+.1f}%, m1 {_m1_j:+.1f}%) — отмена входа.")
        return False
    _pk_drop_j = float((td or {}).get("_drop_from_peak_5m", 0.0) or 0.0)
    _lq_drop_j = float((td or {}).get("_liq_drop_from_peak", 0.0) or 0.0)
    if _pk_drop_j <= -18.0 or _lq_drop_j <= -18.0:
        print(f"🚫 [{source_tag}-POST-PEAK-CRASH] {_sym_tmp} ({mint[:8]}): токен уже слился от пика (цена {_pk_drop_j:+.1f}%, ликва {_lq_drop_j:+.1f}%) — дохлая кошка.")
        return False

    # 🛑 CANONICAL PUMP-MINT FIREWALL: на Solana торгуем ТОЛЬКО канонические launchpad-токены с сожжённым LP
    if not mint.endswith(("pump", "moon")):
        print(f"🚫 [{source_tag}-CANONICAL-FIREWALL] {_sym_tmp} ({mint[:8]}): кастомный не-pump токен на Solana — 100% Remove LP риск (кейсы FRONK, SWJ, SIRIUS, HI, FLY, CRO, SI -99.99%)!")
        return False

    # Защита от кастомных фейк-пулов с разблокированным LP (Meteora, PumpSwap, кастомные пулы без .endswith("pump"))
    _is_grad_jup = False
    try:
        from birth_tracker import birth_tracker as _bt_j
        _is_grad_jup = _bt_j.is_graduated(mint)
    except Exception:
        pass
    # ОСОБОЕ правило: Meteora DYN2 всегда проверяем LP даже для pump.fun токенов!
    # Pump.fun → Meteora DYN2: дев может вытащить ликву одной транзакцией в любой момент.
    # Это самый опасный тип рага (BREAD, PINCHE, DEMOCAT, 内个一 — все Meteora DYN2).
    # ЖЁСТКИЙ БЛОК кастомных не-pump токенов на PumpSwap:
    # Все канонические токены Pump.fun заканчиваются на 'pump'.
    # Токены на PumpSwap без 'pump' — это кастомные фальшивки со скрытым Remove LP дева (кейсы CRO, NEX AI, 42, SI)!
    if _dex_j == "pumpswap" and not mint.endswith("pump"):
        print(f"🚫 [{source_tag}-FAKE-PUMPSWAP] {_sym_tmp} ({mint[:8]}): кастомный токен на PumpSwap без окончания pump — фальшивый пул с Remove LP!")
        return False

    # Meteora Dynamic Pools (DYN2, DAMM, DLMM) — дев ВСЕГДА может сделать Remove LP независимо от lock%!
    # Залоченный LP не означает защиту: "Remove LP" работает через смарт-контракт пула, минуя локер.
    # Кейсы: HI (-99.99% за 4м), FLY (-99.99% за 6м) — оба Meteora DYN2.
    # ИСКЛЮЧЕНИЕ: канонический pump.fun токен (...pump) — LP там реально сожжён навсегда протоколом pump.fun.
    if _dex_j in ("meteora", "meteoradbc", "meteoradlmm", "meteoradyn2", "meteoradamm") and not mint.endswith("pump"):
        print(f"🚫 [{source_tag}-METEORA-RUG] {_sym_tmp} ({mint[:8]}): кастомный не-pump токен на Meteora DYN2 — дев выдёргивает LP одной tx (кейсы HI/FLY -99.99%)!")
        return False

    _is_meteora_dyn = False  # уже заблокировали выше, дальнейший LP-чек только для не-pump/не-meteora токенов
    _skip_lp_check = mint.endswith("moon")  # moon токены пропускаем
    if not _skip_lp_check and (not mint.endswith("pump") and not _is_grad_jup):
        try:
            import goplus as _gp_jup
            _rc_st, _rc_d = await _gp_jup.fetch_rugcheck_report_cached(mint, ttl=120.0, timeout=5)
            if _rc_d:
                # 1. Проверка критической концентрации монет у одного кошелька (кейсы CRO, NEX AI)
                for _rk in (_rc_d.get("risks") or []):
                    _rkn = str(_rk.get("name", "")).lower()
                    _rkl = str(_rk.get("level", "")).lower()
                    if _rkl == "danger" and any(k in _rkn for k in ("single holder", "high ownership", "creator holding")):
                        print(f"🚫 [{source_tag}-SINGLE-HOLDER] {_sym_tmp} ({mint[:8]}): RugCheck خطر '{_rk.get('name')}' — дев контролирует эмиссию!")
                        return False
                # 2. Очищенный score RugCheck
                _raw_sc = float(_rc_d.get("score") or 0.0)
                _ign_sc = sum(float(rk.get("score", 0) or 0) for rk in (_rc_d.get("risks") or []) if any(k in str(rk.get("name", "")).lower() for k in ("low liquidity", "creator history", "copycat", "symbol mimic", "large amount of lp unlocked", "lp not locked", "unlocked lp")))
                if (_raw_sc - _ign_sc) >= 1200:
                    print(f"🚫 [{source_tag}-HIGH-RISK] {_sym_tmp} ({mint[:8]}): RugCheck Score {(_raw_sc - _ign_sc):.0f} >= 1200 — блок скама!")
                    return False

            _lp_ok = False
            if _rc_d:
                for _m in (_rc_d.get("markets") or []):
                    if isinstance(_m, dict):
                        _lp_inf = _m.get("lp") or {}
                        if float(_lp_inf.get("lpLockedPct", 0) or 0) >= 80.0 or float(_lp_inf.get("lpBurnedPct", 0) or 0) >= 80.0:
                            _lp_ok = True
            if _rc_d and not _lp_ok:
                # Для Meteora DYN2 блокируем всегда — LP drain слишком быстрый
                # Для других dex: блокируем только если ругчек вернул данные (fail-open на таймаут)
                _lp_deny_reason = "Meteora DYN2 — LP не заблокирован, дев может вывести в любой момент!" if _is_meteora_dyn else f"кастомный не-pump токен на {_dex_j} без подтвержденной блокировки LP — рагпул!"
                print(f"🚫 [{source_tag}-UNLOCKED-LP] {_sym_tmp} ({mint[:8]}): {_lp_deny_reason}")
                return False
        except Exception:
            pass

    # Защита от анонимных бандлер-пулов без соцсетей на DEX
    _info_j = (td or {}).get("info") or {}
    _soc_j = _info_j.get("socials") or []
    _web_j = _info_j.get("websites") or []
    if not (td or {}).get("_socials_unknown", False) and len(_soc_j) == 0 and len(_web_j) == 0 and _liq_j < 75000:
        print(f"🚫 [{source_tag}-NO-SOCIALS] {_sym_tmp} ({mint[:8]}): 0 соцсетей/сайтов при ликвидности ${_liq_j:,.0f} (<$75k) — анонимный бандлер-раг.")
        return False

    # Защита от миграционного дампа Pump.fun: первые 90с на отстрел instant-снайперов, затем свободный вход
    if mint.endswith("pump") and _dex_j in ("pumpswap", "raydium", "meteora", "orca"):
        if _age_j < 1.0:
            print(f"⏳ [{source_tag}-MIGRATION-DUMP] {_sym_tmp} ({mint[:8]}): AMM-пул создан {_age_j:.1f}м назад (< 1.0м) — зона сброса снайперов pump.fun.")
            return False
        _fdv_j = float((td or {}).get("fdv", 0) or (td or {}).get("marketCap", 0) or 0)
        if 0 < _fdv_j < 12000.0:
            print(f"🚫 [{source_tag}-MIGRATION-LOW-FDV] {_sym_tmp} ({mint[:8]}): FDV ${_fdv_j:,.0f} < $12,000 — токен слит ниже уровня миграции pump.fun.")
            return False
        if _liq_j < 3500.0:
            print(f"🚫 [{source_tag}-MIGRATION-LOW-LIQ] {_sym_tmp} ({mint[:8]}): ликвидность ${_liq_j:,.0f} < $3,500 — дев/снайперы высосали SOL.")
            return False
        
    from jupiter import JupiterAPI
    ds_price = float((td or {}).get("priceUsd", 0) or 0.0)
    jup_price = await JupiterAPI.get_price(mint)
    if ds_price > 0 and jup_price and jup_price > 0:
        ratio = max(ds_price, jup_price) / min(ds_price, jup_price)
        if ratio > 1.09:
            print(f"🚫 [{source_tag}-PRICE-MISMATCH] {_sym_tmp} ({mint[:8]}): Jupiter (${jup_price:.8f}) vs Dex (${ds_price:.8f}) расхождение {ratio:.2f}x (>1.09x) — блок от ложного стопа.")
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
    # Гонка сигналов: jup_loop / jup_fast_mature_loop / scanner анализируют один mint параллельно, и метка
    # per-mint перезаписывается. Если любой из анализов уже поставил SKIP (напр. post-peak-crash -59% у PAW),
    # покупать нельзя, даже если наш собственный analyze_token вернул True.
    if str(_sig).startswith("SKIP"):
        print(f"🚫 [{source_tag}-SKIP-RACE] {_sym_tmp} ({mint[:8]}): параллельный анализ отклонил токен ({_sig}) — вход отменён.")
        return False
    _veto = analyzer.rocket_veto(mint, td or {}, _sig)
    if _veto:
        analyzer._deny(mint, f"jup-veto: {_veto[:20]}", f"🚫 [{source_tag}-VETO] {_sym_tmp} ({mint[:8]}): {_veto}")
        return False

    # Anti-FOMO и защита от 0-соцсеточных рагпулов
    _m5_j = float(((td or {}).get("priceChange") or {}).get("m5", 0) or 0)
    _m1_j = float(((td or {}).get("priceChange") or {}).get("m1", 0) or 0)
    _h1_j = float(((td or {}).get("priceChange") or {}).get("h1", 0) or 0)
    _c_at_j = (td or {}).get("pairCreatedAt") or 0
    _age_j = ((time.time() * 1000 - _c_at_j) / 60000.0) if _c_at_j else 30.0
    _max_h1_cfg = float(getattr(config, "MAX_ENTRY_H1_PCT", 350.0))
    _max_m5_cfg = float(getattr(config, "MAX_ENTRY_M5_PCT", 120.0))
    _max_age_cfg = float(getattr(config, "SOLANA_MAX_AGE_MIN", 60.0))
    _req_soc_age = float(getattr(config, "REQUIRE_SOCIALS_MIN_AGE_MIN", 20.0))
    _vm5_j = float(((td or {}).get("volume") or {}).get("m5", 0) or 0)
    _b5_j = int((((td or {}).get("txns") or {}).get("m5") or {}).get("buys", 0) or 0)
    _s5_j = int((((td or {}).get("txns") or {}).get("m5") or {}).get("sells", 0) or 0)

    # 0. ЖЕСТКИЙ КАРАНТИН ПЕРВОЙ СВЕЧИ (минимум 2.0 минуты / 120 секунд):
    # В первые 90-120 секунд девы и снайперы сливают свои бандлы (кейсы BREASTCOIN, MAYA, DACAT, HANK, AUTISM).
    # Любой обход по b5 >= 35 был ловушкой, т.к. бандл дева сам генерирует 35+ покупок!
    # Настоящие ракеты (WILL, UP, WISHMAS, BELIEVE) бегут 15-60+ минут и легко ловятся на 2.5-10м.
    _min_age_jup = float(getattr(config, "SOLANA_MIN_AGE_MIN", 2.0))
    if _age_j < _min_age_jup:
        print(f"⏳ [{source_tag}-AGE-WARMUP] {_sym_tmp}: возраст {_age_j*60:.0f}с < {_min_age_jup*60:.0f}с — карантин 1-й свечи (защита от снайперов и бандл-рага дева)")
        return False

    # 0.1 Защита от падающего ножа (кейс AMC: m5 -37.6% летит вниз)
    if _m5_j < 1.0:
        print(f"🚫 [{source_tag}-DOWNTREND] {_sym_tmp}: m5 {_m5_j:+.1f}% < +1.0% — падающий нож, отмена входа!")
        return False
    if _m1_j < -4.0:
        print(f"🚫 [{source_tag}-1M-DUMP] {_sym_tmp}: 1м свеча {_m1_j:+.1f}% летит вниз — отмена входа!")
        return False
    # Блок: токен уже на полу — h1 сильно отрицательный при возрасте > 5 мин (кейс JENSEN)
    if _h1_j < -60.0 and _age_j > 5.0:
        print(f"🚫 [{source_tag}-FLOOR-TOKEN] {_sym_tmp} ({mint[:8]}): h1 {_h1_j:.0f}% — токен уже на полу, входить нельзя!")
        return False


    # 0.2 Защита от накрутки микро-покупками / wash-trading (кейс AMC: 5370 покупок по $0.15)
    _tot_tx_j = _b5_j + _s5_j
    _avg_tx_j = (_vm5_j / max(1, _tot_tx_j)) if _tot_tx_j > 0 else 0.0
    if _tot_tx_j >= 50 and _avg_tx_j < 4.0:
        print(f"🚫 [{source_tag}-WASH-TRADING] {_sym_tmp}: средний чек ${_avg_tx_j:.1f} < $4 (микро-пыль спам-ботов) — отмена!")
        return False
    _liq_j = float(((td or {}).get("liquidity") or {}).get("usd", 0) or 0)

    # 1. Защита от уставших зомби-монет (кейс GIGAHUG, INVEST, HABIBI)
    if _age_j > _max_age_cfg:
        _is_active_breakout_j = (_liq_j >= 40000.0 and _vm5_j >= 25000.0 and _b5_j >= int(_s5_j * 1.20))
        if not _is_active_breakout_j:
            print(f"🚫 [{source_tag}-ZOMBIE-AGE] {_sym_tmp}: возраст {_age_j:.0f}м > {_max_age_cfg:.0f}м — уставший пул, отмена!")
            return False

    # 2. Anti-FOMO m5: разрешаем сильный пробой покупателей до +450% (кейсы X +1891%, MAX +973%)
    if _m5_j > _max_m5_cfg:
        _is_strong_buyer_breakout_j = (_m5_j <= 450.0 and _b5_j >= int(_s5_j * 1.10) and _vm5_j >= 4000.0 and (_b5_j + _s5_j) >= 15)
        _is_cooling_pullback = (_m1_j <= -1.5 and _m5_j <= 200.0 and _b5_j >= _s5_j * 1.05)
        if not (_is_strong_buyer_breakout_j or _is_cooling_pullback):
            print(f"🚫 [{source_tag}-FOMO-M5] {_sym_tmp}: m5 +{_m5_j:.1f}% > +{_max_m5_cfg:.0f}% без отката (m1={_m1_j:+.1f}%) — покупка хая, отмена!")
            return False

    # 2.1 Spike Detection: зрелый токен (> 10 мин) вдруг спайкует на m5 >= 150% — классика pump-and-dump координированного!
    # STARSHIP: старый токен, кто-то разогнал за 1 свечу, бот покупает на пике — всё рушится (кейс -96%).
    # Свежие монеты (< 10 мин) могут законно показывать такие свечи — они на старте.
    if _age_j >= 10.0 and _m5_j >= 150.0:
        # Разрешаем только если это подтверждённый органический прорыв: большой объём, много байеров
        _organic_spike_j = (_vm5_j >= 20000.0 and _b5_j >= 80 and _b5_j >= _s5_j * 1.5 and (_b5_j + _s5_j) >= 100)
        if not _organic_spike_j:
            print(f"🚫 [{source_tag}-OLD-SPIKE] {_sym_tmp} ({mint[:8]}): зрелый токен {_age_j:.0f}м вдруг +{_m5_j:.0f}% за 5м (b5={_b5_j}, vol=${_vm5_j:.0f}) — координированный памп-дамп, отмена!")
            return False


    # 3. Защита от давления продавцов: блокируем ТОЛЬКО если продавцы реально преобладают
    _tot_tx_j = _b5_j + _s5_j
    if _tot_tx_j >= 25 and _s5_j > 0:
        _sell_ratio_j = _s5_j / _tot_tx_j
        _vl_ratio_j = (_vm5_j / max(_liq_j, 1.0)) if _liq_j > 0 else 0.0
        if _b5_j < _s5_j and (_sell_ratio_j >= 0.52 or (_sell_ratio_j >= 0.48 and _vl_ratio_j >= 3.5)):
            print(f"🚫 [{source_tag}-SELL-PRESSURE] {_sym_tmp}: продавцов {_s5_j}/{_tot_tx_j} ({_sell_ratio_j*100:.0f}%) при v/l {_vl_ratio_j:.1f}x — сброс китов, отмена!")
            return False

    _eff_max_h1_j = 2500.0 if (_age_j <= 4.0 and _vm5_j >= 4000.0 and _b5_j >= _s5_j * 1.15) else _max_h1_cfg
    if _h1_j > _eff_max_h1_j and _age_j > 4.0:
        print(f"🚫 [{source_tag}-FOMO-H1] {_sym_tmp}: h1 +{_h1_j:.0f}% > +{_eff_max_h1_j:.0f}% — перегретый часовой памп, отмена!")
        return False
    _links_j = len((td or {}).get("info", {}).get("socials", []) or []) + len((td or {}).get("info", {}).get("websites", []) or [])
    _soc_unk_j = bool((td or {}).get("_socials_unknown", False))
    if _age_j >= _req_soc_age and _links_j == 0 and not _soc_unk_j:
        _avg_j = _vm5_j / max(1, _b5_j + _s5_j)
        _vl_j = _vm5_j / max(_liq_j, 1.0)
        _is_organic_stealth = (_vm5_j >= 4000.0 and _b5_j >= 20 and _b5_j >= _s5_j * 1.05 and _liq_j >= 8000.0 and _avg_j >= 8.0 and _vl_j <= 8.0)
        if not _is_organic_stealth:
            print(f"🚫 [{source_tag}-NO-SOCIALS] {_sym_tmp}: 0 соцсетей в зрелом возрасте {_age_j:.0f}м (>={_req_soc_age:.0f}м) без чистой органики — отмена!")
            return False
        else:
            print(f"💎 [{source_tag}-ORGANIC-STEALTH] {_sym_tmp}: 0 соцсетей, но подтвержден органический прорыв — пропуск.")

    # === Analyzer уже провёл ROCKET-XGB S1+S2 и 8-слойный ончейн-аудит: проверяем только 0ms анти-клон и точку входа/стоп ===
    from grok_jev import grok_jev
    _cc_ok, _cc_why = grok_jev.check_copycat_runner(mint, _sym_tmp, td or {})
    if not _cc_ok:
        print(f"🚫 [{source_tag}-COPYCAT] {_sym_tmp} ({mint[:8]}): {_cc_why} — отказ входа.")
        return False
    _tc_ok, _tc_why = await grok_jev.check_trending_clone(mint, _sym_tmp, td or {})
    if not _tc_ok:
        print(f"🚫 [{source_tag}-CLONE] {_sym_tmp} ({mint[:8]}): {_tc_why} — отказ входа.")
        return False

    _flush_ok, _wick_pct, _wick_stop_price, _flush_msg = grok_jev.check_flush_and_wick(td or {}, price)
    if not _flush_ok:
        print(f"⏳ [JEV-FILLS] {_sym_tmp} ({mint[:8]}): {_flush_msg}")
        return False
    else:
        print(f"🎯 [JEV-FILLS] {_sym_tmp} ({mint[:8]}): {_flush_msg}")

    # Предвходовой анализ на мгновенный раг первой свечи (FCR Guard)
    if getattr(config, "FCR_ENABLED", True):
        try:
            from first_candle_rug_guard import pre_entry_rug_check
            _fcr = await pre_entry_rug_check(mint, pair_data=td or {}, symbol=_sym_tmp)
            if _fcr.get("verdict") == "BLOCK":
                print(f"🚫 [{source_tag}-FCR] {_sym_tmp} ({mint[:8]}): {_fcr.get('report', '')} — вход заблокирован (мгновенный раг)!")
                return False
        except Exception:
            pass

    async with _get_buy_lock():
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
                size = cap * (float(getattr(config, "REINVEST_PERCENT", 15.0)) / 100.0) * analyzer.conviction_size_mult(td or {})
        _j_liq = float(((td or {}).get("liquidity") or {}).get("usd", 0) or 0)
        if _j_liq > 0:
            size = min(size, max(5.0, _j_liq * float(getattr(config, "MAX_POOL_LIQ_FRAC", 0.02) or 0.02)))
        if "VIP-" in _sig and not getattr(config, "VIP_ENTRIES_ENABLED", False):
            print(f"🚫 [VIP-BLOCKED] {_sym_tmp}: VIP входы отключены в config.py.")
            return False
        if "PUMP-XGB" in _sig and not getattr(config, "PUMP_XGB_ENABLED", False):
            print(f"🚫 [PUMP-XGB-BLOCKED] {_sym_tmp}: PUMP-XGB входы отключены в config.py.")
            return False
        if "RAY-XGB" in _sig and not getattr(config, "RAY_XGB_ENABLED", False):
            print(f"🚫 [RAY-XGB-BLOCKED] {_sym_tmp}: старые RAY-XGB входы отключены в config.py.")
            return False
        if "ROCKET-XGB" in _sig and not getattr(config, "ROCKET_XGB_ENABLED", True):
            print(f"🚫 [ROCKET-XGB-BLOCKED] {_sym_tmp}: ROCKET-XGB отключен в config.py.")
            return False
        if "VIP-" in _sig:
            size = min(size, float(getattr(config, "TG_VIP_SIZE_USD", 15.0) or 15.0))
        _mint_ml_conf = float(getattr(analyzer, "rocket_scores", {}).get(mint, getattr(analyzer, "last_score", 0.0)) or 0.0)

        # Pre-flight live Jupiter price check (защита от покупки падающей свечи)
        try:
            from jupiter import JupiterAPI
            _j_ok, _j_why = await JupiterAPI.is_token_sellable(mint)
            if not _j_ok and any(k in str(_j_why or "").lower() for k in ("no routes", "cannot sell", "honeypot", "freeze", "0 sol returned")):
                print(f"🚫 [{source_tag}-UNSELLABLE] {_sym_tmp} ({mint[:8]}): токен непродаваем на DEX ({_j_why}) — отмена входа!")
                return False
            _jup_live_px = await JupiterAPI.get_price(mint)
            if _jup_live_px and _jup_live_px > 0 and price > 0:
                if _jup_live_px < price * 0.975:
                    print(f"🚫 [{source_tag}-LIVE-DUMP] {_sym_tmp} ({mint[:8]}): живая цена Jupiter (${_jup_live_px:.8f}) уже на {(1.0 - _jup_live_px / price) * 100:.1f}% ниже DexScreener (${price:.8f}) — обвал в моменте, отмена!")
                    return False
                _jup_ratio = max(_jup_live_px, price) / min(_jup_live_px, price)
                if _jup_ratio > 1.055:
                    print(f"🚫 [{source_tag}-PRICE-MISMATCH] {_sym_tmp} ({mint[:8]}): Jupiter vs сигнал расхождение {_jup_ratio:.2f}x (>1.055x) — отмена!")
                    return False
                price = _jup_live_px
                if _wick_stop_price and _wick_pct:
                    _wick_stop_price = price * (1.0 + _wick_pct)
        except Exception:
            pass

        tracker.add_position(
            _sym_tmp, mint, price, min(size, 100.0),
            is_mature=True,
            ml_features=analyzer.pack_features(td or {}),
            ml_confidence=_mint_ml_conf,
            source=f"{source_tag}:{analyzer.get_signal(mint)}",
            pair_address=((td or {}).get("pairAddress", "") or ""),
            stop_under_wick=_wick_stop_price,
            wick_stop_loss_pct=_wick_pct,
            initial_liquidity_usd=_j_liq,
            pair_data=td)
        grok_jev.FUNNEL_STATS["filled"] += 1
        print(grok_jev.get_funnel_summary())
        print(f"🪐 {source_tag} BUY {_sym_tmp} ({mint[:8]}) @ ${price} [size: ${min(size, 100.0):.1f}] | Wick Stop: ${_wick_stop_price:.8f} ({_wick_pct*100:.1f}%)")
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
                    ok = await asyncio.wait_for(analyzer.analyze_token(mint), timeout=20.0)
                except asyncio.TimeoutError:
                    print(f"⏱️ [JUP-MATURE-TIMEOUT] {mint[:8]}: анализ превысил 20с, пропуск")
                    continue
                except Exception as e:
                    print(f"🪐 JUP mature analyze err {mint[:8]}: {e}")
                    continue
                if ok is None:
                    # FIX: analyzer держит карантин 3.0м (для не-early токенов), а ready_at = 0.8м + 5с.
                    # Раньше токен выкидывался из очереди после первого же отказа. Теперь один раз
                    # возвращаем его в реестр на момент снятия карантина.
                    if not info.get("retried"):
                        info["retried"] = True
                        info["ready_at"] = float(info.get("created_sec") or time.time()) + 3.0 * 60.0 + 3.0
                        _immature_registry[mint] = info
                        print(f"🔁 [JUP-RETRY] {sym} ({mint[:8]}): карантин analyzer ещё идёт — повтор после 3.0м.")
                    else:
                        print(f"⏭ [JUP-SKIP] {sym} ({mint[:8]}): анализ созревшего токена не дал сигнала (eval_none)")
                    continue
                _m_sc = float(getattr(analyzer, "rocket_scores", {}).get(mint, getattr(analyzer, "last_score", 0.0)) or 0.0)
                analyzer.log_scan(sym, mint, "JUP-MATURE", ok, _m_sc)
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
                    open_jup = set(tracker.get_open_positions().keys())
                    try:
                        _to_pf = [m for m in mints if m not in open_jup and (_t.time() - processed.get(m, 0.0)) >= 35]
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
                        if not m or m in open_jup:
                            continue
                        if m in tracker.positions:
                            _cp = tracker.positions.get(m)
                            _cpnl = float(getattr(_cp, "pnl_usd", 0.0) or 0.0)
                            _cext = float(getattr(_cp, "exit_time", 0.0) or 0.0)
                            if (_now_t - _cext) < (300.0 if _cpnl > 0 else 14400.0):
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
                        _dex_cand = str(td.get("dexId", "")).lower()
                        if _dex_cand in ("pump", "pumpfun", "moonshot", "meteoradbc"):
                            _fdv_cand = float(td.get("fdv", 0) or td.get("marketCap", 0) or 0)
                            _min_c = float(getattr(config, "MIN_PUMP_CURVE_MCAP_USD", 7500.0))
                            _max_c = float(getattr(config, "MAX_PUMP_CURVE_MCAP_USD", 42000.0))
                            if not getattr(config, "ALLOW_UNGRADUATED_PUMPFUN", False) or (0 < _fdv_cand < _min_c) or (_fdv_cand > _max_c):
                                processed[m] = _now_t
                                continue

                        # Зона созревания: отправляем в быстрый чекер
                        if _ag < _min_age:
                            immature_count += 1
                            created_sec = (_cr / 1000.0) if _cr else (_now_t - _ag * 60.0)
                            ready_at = created_sec + (_min_age * 60.0) + 5.0
                            _immature_registry[m] = {
                                "ready_at": ready_at,
                                "sym": _sym,
                                "min_age": _min_age,
                                "added_at": _now_t,
                                "created_sec": created_sec,  # FIX: нужно для повторной проверки после карантина analyzer (3.0м)
                                "retried": False
                            }
                            continue

                        _lq_cand = float((td.get("liquidity") or {}).get("usd", 0) or 0)
                        _v5_cand = float((td.get("volume") or {}).get("m5", 0) or 0)

                        # Отсекаем уставшие зомби-монеты синхронно с ZOMBIE-AGE в analyzer.py
                        _max_age_cand = float(getattr(config, "SOLANA_MAX_AGE_MIN", 240.0))
                        if _ag > _max_age_cand:
                            _is_big_cand = (_ag <= 360.0 and _lq_cand >= 25000.0 and _v5_cand >= 15000.0)
                            if not _is_big_cand:
                                skipped_ancient += 1
                                processed[m] = _now_t + 600
                                continue

                        # Отсекаем флет m5 < 1.8%
                        if _m5 < 1.8:
                            skipped_flat += 1
                            processed[m] = _now_t + 90
                            continue
                        _turnover = (_v5_cand / max(_lq_cand, 1.0))
                        # Оценка ракеты: если включена Rocket XGBoost v2, ранжируем строго по ML-скору
                        if getattr(config, "ROCKET_XGB_ENABLED", True) and hasattr(analyzer, "score_rocket_xgb"):
                            _rkt_score = analyzer.score_rocket_xgb(m, td)
                        else:
                            _rkt_score = (_m5 * 1.5) + min(_turnover * 30.0, 60.0) + (35.0 if m.endswith('pump') or _ag < 45.0 else 0.0)
                        hot_candidates.append((_rkt_score, _m5, _ag, _sym, m, td))

                    # Сортируем по максимальному импульсу ракеты (скорость, оборот, свежесть)
                    hot_candidates.sort(key=lambda x: x[0], reverse=True)

                    total_skipped = skipped_flat + skipped_ancient + skipped_nodata
                    print(f"📦 [JUP BATCH] {len(mints)} токенов: отсеяно пачкой {total_skipped} "
                          f"({skipped_flat} флет, {skipped_ancient} старых, {skipped_nodata} без пула), "
                          f"созревают {immature_count}. Отобрано ТОП-{len(hot_candidates)} кандидатов с импульсом!")

                    async def _process_hot_candidate(cand):
                        async with sem:
                            _score, _m5_val, _ag_val, _sym_val, mint, td = cand
                            if not mint or mint in tracker.get_open_positions():
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
                                ok = await asyncio.wait_for(analyzer.analyze_token(mint), timeout=20.0)
                            except asyncio.TimeoutError:
                                print(f"⏱️ [JUP-TIMEOUT] {mint[:8]}: анализ превысил 20с, пропуск")
                                return
                            except Exception as e:
                                print(f"🪐 JUP analyze err {mint[:8]}: {type(e).__name__} ({e})")
                                return

                            if ok is None:
                                return

                            _sig_j = str(analyzer.get_signal(mint) or "").lower()
                            _is_term_j = any(k in _sig_j for k in (
                                "rugcheck", "scam", "unlocked-lp", "bundle", "honeypot", 
                                "freeze", "mimic", "dead-token", "мёртвый токен", "sybil", 
                                "delegation", "post-mig-dumped", "zombie", "low-liq", 
                                "deadcat", "dead-cat", "dumped", "ghost-pool", "wash-spike", 
                                "wash-trap", "mcap-too-large", "price-split"
                            ))
                            if ok is False:
                                processed[mint] = _t.time() + (3600 if _is_term_j else 90)
                                if _is_term_j:
                                    try:
                                        from birth_tracker import birth_tracker as _bt_j
                                        _bt_j.remove_graduated(mint)
                                    except Exception:
                                        pass
                            else:
                                processed[mint] = _t.time()
                            _j_sc = float(getattr(analyzer, "rocket_scores", {}).get(mint, getattr(analyzer, "last_score", 0.0)) or 0.0)
                            analyzer.log_scan(_sym_val, mint, "JUP", ok, _j_sc)

                            if ok is True:
                                await _evaluate_and_buy(mint, td, analyzer, tracker, source_tag="JUP")

                    tasks = [_process_hot_candidate(c) for c in hot_candidates[:30]]
                    if tasks:
                        try:
                            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=35.0)
                        except asyncio.TimeoutError:
                            print("⏱️ [JUP-BATCH-TIMEOUT] Пакетный анализ кандидатов превысил 35с!")

                    if len(processed) > 1000:
                        processed.clear()
        except Exception as e:
            print(f"Ошибка в jup_loop: {e}")
        await asyncio.sleep(interval)
