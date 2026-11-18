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
CACHE_TTL = 10
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
            return 1.5
        from datetime import datetime
        s = ca.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            dt = datetime.fromisoformat(re.sub(r"\.\d+", "", s))
        return max(0.0, (time.time() - dt.timestamp()) / 60.0)
    except Exception:
        return 1.5


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

        # 1. Jupiter Tokens API v2 (5 главных срезов, чередуем pro/lite + stagger 0.22s без 429 на recent):
        _jup_sem = asyncio.Semaphore(2)

        async def _jup_get(subpath: str, prefer_lite: bool = False):
            async with _jup_sem:
                primary = f"{LITE_BASE}/{subpath}" if prefer_lite else f"{BASE}/{subpath}"
                primary_h = None if prefer_lite else headers
                fallback = f"{BASE}/{subpath}" if prefer_lite else f"{LITE_BASE}/{subpath}"
                fallback_h = headers if prefer_lite else None
                st, data = await fetch_json(primary, headers=primary_h, timeout=8, retries=0)
                if st != 200 or not isinstance(data, list):
                    await asyncio.sleep(0.25)
                    st_l, data_l = await fetch_json(fallback, headers=fallback_h, timeout=8, retries=0)
                    if st_l == 200 and isinstance(data_l, list):
                        return st_l, data_l
                await asyncio.sleep(0.22)
                return st, data

        (s1, d1), (s_org, d_org), (s3, d3), (s2, d2), (s1h, d1h), (s_tr1h, d_tr1h), (s_org1h, d_org1h) = await asyncio.gather(
            _jup_get("toptrending/5m?limit=100", prefer_lite=False),
            _jup_get("toporganicscore/5m?limit=100", prefer_lite=True),
            _jup_get("toptraded/5m?limit=100", prefer_lite=False),
            _jup_get("recent", prefer_lite=False),
            _jup_get("toptrending/1h?limit=100", prefer_lite=False),
            _jup_get("toptraded/1h?limit=100", prefer_lite=False),
            _jup_get("toporganicscore/1h?limit=100", prefer_lite=True),
        )
        s6h, d6h = 0, []

        ranked = []
        seen = set()
        _age_by_mint = {}
        _max_disc_age = float(getattr(config, "SOLANA_MAX_AGE_MIN", 240.0))
        _max_disc_fdv = float(getattr(config, "JEV_MAX_MCAP_USD", 1500000.0))

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
                # Пускаем все валидные Solana токены (безопасность и LP проверяются строго в аудите)
                liq = float(t.get("liquidity") or 0.0)
                fdv_j = float(t.get("fdv") or t.get("mcap") or 0.0)
                if fdv_j > _max_disc_fdv:
                    continue
                is_grad = bool(t.get("graduatedAt") or t.get("graduatedPool") or liq >= 4500.0)
                if not allow_raw_curve and not is_grad:
                    continue
                _min_jup_l = 1500.0 if allow_raw_curve else float(getattr(config, "MIN_GRADUATED_LIQ_USD", 2500.0))
                if liq < _min_jup_l:
                    continue
                st5 = t.get("stats5m") or {}
                st1h = t.get("stats1h") or {}
                pc5 = float(st5.get("priceChange") or 0.0)
                pc1h = float(st1h.get("priceChange") or 0.0)
                vol5 = float(st5.get("buyVolume") or 0.0) + float(st5.get("sellVolume") or 0.0)
                b5 = int(st5.get("numBuys") or 0)
                s5 = int(st5.get("numSells") or 0)
                age_m = _parse_jup_age_min(t)
                # Жесткий фокус на свежих 2x-10x ракетах <= 90м (никаких 3-12ч топов BILL):
                if age_m > _max_disc_age:
                    continue
                if age_m > 45.0 and pc5 < 4.0 and vol5 < 15000.0:
                    continue
                _max_pc1h = config.get_max_entry_h1_pct(age_m, vol5, b5, s5, liq, None)
                if pc1h > _max_pc1h or (pc1h < -15.0 and pc5 < 15.0):
                    continue
                _cfg_v5_req = float(getattr(config, "MIN_VOLUME_M5", 600.0))
                _min_v5_req = min(_cfg_v5_req, 800.0) if age_m <= 15.0 else _cfg_v5_req
                if vol5 < _min_v5_req:
                    continue
                _is_turned_green_dip = (
                    age_m <= 45.0
                    and vol5 >= 8000.0
                    and b5 >= int(s5 * 1.35)
                    and pc5 >= -1.5
                    and pc1h >= -8.0
                )
                if pc5 < min_pc5 and not _is_turned_green_dip:
                    continue
                seen.add(m)
                _age_by_mint[m] = age_m
                try:
                    import market_data as _md_jcache
                    _md_jcache.normalize_jup_token(t)
                except Exception:
                    pass
                ranked.append((max(pc5, 3.0 if _is_turned_green_dip else pc5), vol5, sym, m, tag))

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
        # Ловит живые выпускники PumpSwap / Meteora / Raydium / Bonk, а также свежие бусты и профили!
        try:
            ds_queries = (
                "https://api.dexscreener.com/token-boosts/latest/v1",
                "https://api.dexscreener.com/token-boosts/top/v1",
                "https://api.dexscreener.com/token-profiles/latest/v1",
                "https://api.dexscreener.com/latest/dex/search?q=pumpswap",
                "https://api.dexscreener.com/latest/dex/search?q=SOL%20pump",
                "https://api.dexscreener.com/latest/dex/search?q=pumpswap%20SOL",
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
                if st_q != 200:
                    continue
                # Обработка списков бустов/профилей:
                if isinstance(d_q, list):
                    for item_b in d_q:
                        if not isinstance(item_b, dict) or str(item_b.get("chainId") or "").lower() != "solana":
                            continue
                        addr_b = str(item_b.get("tokenAddress") or "").strip()
                        if addr_b and not addr_b.startswith("0x") and 32 <= len(addr_b) <= 44 and addr_b not in seen:
                            seen.add(addr_b)
                            ranked.append((3.0, 5000.0, "", addr_b, "dex_boost"))
                    continue
                if not isinstance(d_q, dict):
                    continue
                for p in (d_q.get("pairs") or []):
                    if not isinstance(p, dict) or str(p.get("chainId") or "").lower() != "solana":
                        continue
                    bt = p.get("baseToken") or {}
                    addr = str(bt.get("address") or "").strip()
                    sym_p = str(bt.get("symbol") or "").upper().strip()
                    if not addr or addr.startswith("0x") or len(addr) < 32 or len(addr) > 44 or addr in seen or sym_p in SKIP_SYMBOLS:
                        continue
                    if not allow_raw_curve and config.is_unmigrated_curve_token(addr, p):
                        continue
                    liq_p = float((p.get("liquidity") or {}).get("usd", 0) or 0)
                    if liq_p < float(getattr(config, "MIN_GRADUATED_LIQ_USD", 2500.0)):
                        continue
                    ca_p = float(p.get("pairCreatedAt") or 0)
                    age_p = ((now_ms_ds - ca_p) / 60000.0) if ca_p > 0 else 1.5
                    if age_p > _max_disc_age:
                        continue
                    vol5_p = float((p.get("volume") or {}).get("m5", 0) or 0)
                    _min_v5_ds = 700.0 if age_p <= 2.5 else (850.0 if age_p <= 5.0 else float(getattr(config, "MIN_VOLUME_M5", 1000.0)))
                    if vol5_p < _min_v5_ds:
                        continue
                    tx5_p = (p.get("txns") or {}).get("m5") or {}
                    b5_p = int(tx5_p.get("buys", 0) or 0)
                    s5_p = int(tx5_p.get("sells", 0) or 0)
                    _min_tx_ds = 7 if age_p <= 2.5 else (8 if age_p <= 5.0 else int(getattr(config, "MIN_TXNS_M5", 10)))
                    _min_bs_ds = 1.05 if age_p <= 5.0 else 1.02
                    _is_clean_fresh_buyer = (age_p <= 3.0 and b5_p >= 4 and s5_p <= 1)
                    if not _is_clean_fresh_buyer and ((b5_p + s5_p) < _min_tx_ds or (s5_p > 0 and (b5_p / s5_p) < _min_bs_ds)):
                        continue
                    pc_p = p.get("priceChange") or {}
                    m5_p = float(pc_p.get("m5", 0) or 0)
                    h1_p = float(pc_p.get("h1", 0) or 0)
                    _min_m5_ds = 2.0 if age_p <= 5.0 else 2.5
                    if m5_p < _min_m5_ds:
                        continue
                    fdv_p = float(p.get("fdv") or p.get("marketCap") or 0)
                    if fdv_p > _max_disc_fdv:
                        continue
                    _max_h1_ds = config.get_max_entry_h1_pct(age_p, vol5_p, b5_p, s5_p, liq_p, m5_p)
                    if h1_p > _max_h1_ds:
                        continue
                    seen.add(addr)
                    _age_by_mint[addr] = age_p
                    try:
                        import market_data as _md_dscache
                        _md_dscache._JUP_TD_CACHE[addr] = (time.time(), p)
                    except Exception:
                        pass
                    ranked.append((m5_p, vol5_p, sym_p, addr, "ds_radar"))
        except Exception:
            pass

        # 3. Добор живых трендов и свежих пулов PumpSwap из общего кэша market_data (без дублирования запросов и 429)
        try:
            import market_data
            gt_items = await market_data.get_trending()
            _td_cache_gt = getattr(market_data, "_TD_CACHE", {}) or {}
            for item in (gt_items or []):
                addr = str(item.get("tokenAddress") or "").strip()
                if not addr or addr.startswith("0x") or len(addr) < 32 or len(addr) > 44 or addr in seen:
                    continue
                _gt_hit = _td_cache_gt.get(addr)
                _gt_td = _gt_hit[1] if (isinstance(_gt_hit, tuple) and len(_gt_hit) >= 2 and isinstance(_gt_hit[1], dict)) else {}
                _gt_fdv = float((_gt_td.get("fdv") or _gt_td.get("marketCap") or item.get("fdvUsd") or 0) if _gt_td else (item.get("fdvUsd") or 0))
                if _gt_fdv > _max_disc_fdv:
                    continue
                _gt_cr = float(_gt_td.get("pairCreatedAt") or 0) if _gt_td else 0.0
                _gt_age = ((time.time() * 1000.0 - _gt_cr) / 60000.0) if _gt_cr > 0 else float(item.get("_gt_age_min") or 5.0)
                if _gt_age > _max_disc_age:
                    continue
                _gt_m5 = float(((_gt_td.get("priceChange") or {}).get("m5", 0) or 0)) if _gt_td else 5.0
                _gt_v5 = float(((_gt_td.get("volume") or {}).get("m5", 0) or 0)) if _gt_td else 5000.0
                _gt_sym = str(((_gt_td.get("baseToken") or {}).get("symbol") or addr[:6])).upper() if _gt_td else addr[:6]
                if _gt_td and (_gt_v5 < float(getattr(config, "MIN_VOLUME_M5", 1000.0)) or _gt_m5 < 0.0):
                    continue
                seen.add(addr)
                _age_by_mint[addr] = _gt_age
                ranked.append((max(_gt_m5, 4.0), max(_gt_v5, 5000.0), _gt_sym, addr, "gt_trending"))
        except Exception:
            pass

        # 4. DexScreener Token Boosts + Profiles + Takeovers (с пакетной предвалидацией пулов без мусора)
        try:
            ds_boost_urls = (
                "https://api.dexscreener.com/token-boosts/top/v1",
                "https://api.dexscreener.com/token-boosts/latest/v1",
                "https://api.dexscreener.com/token-profiles/latest/v1",
                "https://api.dexscreener.com/community-takeovers/latest/v1",
            )
            ds_b_res = await asyncio.gather(
                *[fetch_json(u, timeout=5, retries=0) for u in ds_boost_urls],
                return_exceptions=True
            )
            boost_mints = []
            for res in ds_b_res:
                if isinstance(res, tuple) and len(res) == 2 and res[0] == 200 and isinstance(res[1], list):
                    for item in res[1]:
                        if item.get("chainId") != "solana":
                            continue
                        addr = str(item.get("tokenAddress") or "").strip()
                        if not addr or addr.startswith("0x") or len(addr) < 32 or len(addr) > 44 or addr in seen or str(item.get("symbol") or "").upper() in SKIP_SYMBOLS:
                            continue
                        seen.add(addr)
                        boost_mints.append(addr)
            min_boost_liq = float(getattr(config, "MIN_GRADUATED_LIQ_USD", 2500.0))
            now_ms_b = time.time() * 1000
            for i_b in range(0, min(len(boost_mints), 90), 30):
                chunk_b = boost_mints[i_b:i_b + 30]
                st_b, data_b = await fetch_json(f"https://api.dexscreener.com/latest/dex/tokens/{','.join(chunk_b)}", timeout=6, retries=0)
                pairs_b = []
                if st_b == 200 and isinstance(data_b, dict):
                    pairs_b = data_b.get("pairs") or []
                elif st_b == 200 and isinstance(data_b, list):
                    pairs_b = data_b
                else:
                    continue
                best_b = {}
                for p in pairs_b:
                    if not isinstance(p, dict) or str(p.get("chainId", "")).lower() != "solana":
                        continue
                    bm = str(((p.get("baseToken") or {}).get("address")) or "").strip()
                    if not bm:
                        continue
                    liq_b = float((p.get("liquidity") or {}).get("usd", 0) or 0)
                    vol_b = float((p.get("volume") or {}).get("m5", 0) or 0)
                    prev_b = best_b.get(bm)
                    if not prev_b or (liq_b, vol_b) > (float((prev_b.get("liquidity") or {}).get("usd", 0) or 0), float((prev_b.get("volume") or {}).get("m5", 0) or 0)):
                        best_b[bm] = p
                for bm, p in best_b.items():
                    if not allow_raw_curve and config.is_unmigrated_curve_token(bm, p):
                        continue
                    liq_b = float((p.get("liquidity") or {}).get("usd", 0) or 0)
                    if liq_b < min_boost_liq:
                        continue
                    fdv_b = float(p.get("fdv") or p.get("marketCap") or 0)
                    if fdv_b > _max_disc_fdv:
                        continue
                    ca_b = float(p.get("pairCreatedAt") or 0)
                    age_b = ((now_ms_b - ca_b) / 60000.0) if ca_b > 0 else 999.0
                    if age_b > _max_disc_age:
                        continue
                    vol5_b = float((p.get("volume") or {}).get("m5", 0) or 0)
                    m5_b = float((p.get("priceChange") or {}).get("m5", 0) or 0)
                    if vol5_b < float(getattr(config, "MIN_VOLUME_M5", 1000.0)) or m5_b < 2.0:
                        continue
                    sym_b = str(((p.get("baseToken") or {}).get("symbol")) or bm[:6]).upper().strip()
                    _age_by_mint[bm] = age_b
                    ranked.append((m5_b, vol5_b, sym_b, bm, "ds_top_boost"))
        except Exception:
            pass

        # 5. Свежие мигрировавшие листинги (recent) — для отправки молодых выпускников и seed birth_tracker
        if s2 == 200 and isinstance(d2, list):
            for t in d2:
                if not isinstance(t, dict):
                    continue
                m = t.get("id", "")
                sym = str(t.get("symbol") or "").upper().strip()
                if not m or m.startswith("0x") or len(m) < 32 or len(m) > 44:
                    continue
                # Подпитываем birth_tracker свежими launchpad-минтами (.pump/.bonk/.bags)
                if m.lower().endswith(("pump", "bonk", "bags")):
                    try:
                        from birth_tracker import birth_tracker as _bt_seed
                        _bt_seed.add_token(m)
                    except Exception:
                        pass
                if m in seen or sym in SKIP_SYMBOLS:
                    continue
                # Пускаем все валидные Solana токены (безопасность и LP проверяются в аудите)
                liq = float(t.get("liquidity") or 0.0)
                fdv_r = float(t.get("fdv") or t.get("marketCap") or 0.0)
                if fdv_r > _max_disc_fdv:
                    continue
                age_r = _parse_jup_age_min(t)
                if age_r > _max_disc_age:
                    continue
                is_grad_r = bool(t.get("graduatedAt") or t.get("graduatedPool") or (not m.endswith("pump") and liq >= 7500.0))
                if not allow_raw_curve and not is_grad_r:
                    continue
                if liq < float(getattr(config, "MIN_GRADUATED_LIQ_USD", 2500.0)):
                    continue
                st5 = t.get("stats5m") or {}
                pc5 = float(st5.get("priceChange") or 0.0)
                vol5_r = float(st5.get("buyVolume") or 0.0) + float(st5.get("sellVolume") or 0.0)
                seen.add(m)
                _age_by_mint[m] = age_r
                ranked.append((max(pc5, 2.0), max(vol5_r, 2000.0), sym, m, "recent"))

        # Сортируем все импульсные монеты по РОКЕТ-ВЕЛОСИТИ с бонусом за свежесть (<=15м x2.5, <=45м x1.8):
        import math
        def _rocket_velocity(x):
            # x = (pc5, vol5, sym, m, tag)
            pc = max(float(x[0]), 0.1)
            vol = max(float(x[1]), 100.0)
            m_id = str(x[3])
            tag = str(x[4])
            _ag_x = _age_by_mint.get(m_id, 60.0)
            fresh_mult = 6.0 if _ag_x <= 2.0 else (4.0 if _ag_x <= 5.0 else (2.2 if _ag_x <= 15.0 else (0.5 if _ag_x > 30.0 else 1.2)))
            tag_mult = 2.0 if ("ds_radar" in tag or "gt_trending" in tag or "organic_5m" in tag or "trending_5m" in tag) else 1.0
            return pc * math.log10(vol) * tag_mult * fresh_mult

        ranked.sort(key=_rocket_velocity, reverse=True)

        out = [m for _, _, _, m, _ in ranked][:120]
        _cache, _cache_time = out, time.time()
        return out


async def _evaluate_and_buy(mint: str, td: dict, analyzer, tracker, source_tag: str = "JUP") -> bool:
    """Полная проверка безопасности, ликвидности и вход в сделку. Потокобезопасна."""
    if not td or len(tracker.get_open_positions()) >= config.MAX_CONCURRENT_POSITIONS:
        return False
    _scan_px_j = float((td or {}).get("priceUsd", 0) or 0)
    # Сброс prefetch-кэша перед входом, чтобы не покупать по старой котировке 15-30с давности после дампа во время аудита:
    getattr(analyzer, "_prefetch_cache", {}).pop(mint, None)
    _fresh_td = await analyzer.fetch_token_data(mint)
    if isinstance(_fresh_td, dict) and float(_fresh_td.get("priceUsd", 0) or 0) > 0:
        _fresh_px_j = float(_fresh_td.get("priceUsd", 0) or 0)
        if _scan_px_j > 0:
            if _fresh_px_j < _scan_px_j * 0.92:
                print(f"🚫 [{source_tag}-FINAL-RECHECK] {mint[:8]}: обвал цены на {(_fresh_px_j/_scan_px_j - 1)*100:+.1f}% за время аудита — отмена входа!")
                return False
            if _fresh_px_j > _scan_px_j * 1.30:
                print(f"🚫 [{source_tag}-FINAL-RECHECK] {mint[:8]}: цена улетела на {(_fresh_px_j/_scan_px_j - 1)*100:+.1f}% за время аудита — не догоняем хай!")
                return False
        td = _fresh_td
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
        _min_c_mc = float(getattr(config, "MIN_PUMP_CURVE_MCAP_USD", 3500.0))
        _max_c_mc = float(getattr(config, "MAX_PUMP_CURVE_MCAP_USD", 65000.0))
        if _fdv_j > 0 and _fdv_j < _min_c_mc:
            print(f"🚫 [{source_tag}-CURVE-FLASH-RUG] {_sym_tmp} ({mint[:8]}): FDV ${_fdv_j:,.0f} < ${_min_c_mc:,.0f} (пустой кёрв, flash-rug риск) — блок.")
            return False
        if _fdv_j > _max_c_mc:
            print(f"🚫 [{source_tag}-CURVE-CLIFF] {_sym_tmp} ({mint[:8]}): FDV ${_fdv_j:,.0f} > ${_max_c_mc:,.0f} (обрыв перед миграцией) — блок.")
            return False
        _liq_j = max(_raw_liq_j, _fdv_j * 0.40)
    else:
        _liq_j = _raw_liq_j
    _min_liq_req = 1000.0 if _is_raw_curve_j else float(getattr(config, "MIN_GRADUATED_LIQ_USD", 2500.0))
    if _liq_j < _min_liq_req:
        print(f"🚫 [{source_tag}-LOW-LIQ] {_sym_tmp} ({mint[:8]}): реальная ликвидность ${_liq_j:.0f} < ${_min_liq_req:.0f} — микро-пул/риск слива.")
        return False
    _tot_tx_j = _b5_j + _s5_j
    _cfg_min_tx_j = int(getattr(config, "MIN_TXNS_M5", 10))
    _min_tx_req_j = min(_cfg_min_tx_j, 7) if (_age_j <= 2.5 and _b5_j >= max(6, int(_s5_j * 1.15))) else (min(_cfg_min_tx_j, 8) if _age_j <= 5.0 else _cfg_min_tx_j)
    if _tot_tx_j < _min_tx_req_j:
        print(f"🚫 [{source_tag}-LOW-TXNS] {_sym_tmp} ({mint[:8]}): всего {_tot_tx_j} сделок в m5 (<{_min_tx_req_j}) — вялый боковой стакан.")
        return False
    _vol5_j = float(((td or {}).get("volume") or {}).get("m5", 0) or 0)
    _cfg_min_v5_j = float(getattr(config, "MIN_VOLUME_M5", 1000.0))
    if _age_j <= 2.5 and _b5_j >= max(6, int(_s5_j * 1.15)):
        _min_vol_j = min(_cfg_min_v5_j, 700.0)
    elif _age_j <= 5.0 and _b5_j >= max(7, _s5_j):
        _min_vol_j = min(_cfg_min_v5_j, 850.0)
    else:
        _min_vol_j = _cfg_min_v5_j
    if _vol5_j < _min_vol_j:
        print(f"🚫 [{source_tag}-LOW-VOL] {_sym_tmp} ({mint[:8]}): 5м объем ${_vol5_j:,.0f} < ${_min_vol_j:,.0f} — слабый стоячий токен (кейс BRO).")
        return False

    # Защита от перегретого хая, падающего ножа и слива от пика
    _pc_j = (td or {}).get("priceChange") or {}
    _m5_j = float(_pc_j.get("m5", 0) or 0)
    _m1_j = float(_pc_j.get("m1", 0) or 0)
    if abs(_m1_j) > max(180.0, abs(_m5_j) * 3.0 + 40.0):
        _m1_j = _m5_j / 5.0
    _max_m1_cfg_j = float(getattr(config, "MAX_ENTRY_M1_PCT", 22.0))
    _eff_max_m1_j = max(_max_m1_cfg_j, 30.0) if (_b5_j >= int(_s5_j * 1.15) and _vol5_j >= 3000.0 and _m5_j >= 4.0) else _max_m1_cfg_j
    if _m1_j > _eff_max_m1_j:
        print(f"🚫 [{source_tag}-M1-SPIKE] {_sym_tmp} ({mint[:8]}): m1 +{_m1_j:.1f}% > +{_eff_max_m1_j:.1f}% — вершина свечи, отмена входа.")
        return False
    _h1_j = float(_pc_j.get("h1", 0) or 0)
    _max_h1_j = config.get_max_entry_h1_pct(_age_j, _vol5_j, _b5_j, _s5_j, _liq_j, _m5_j)
    if _h1_j > _max_h1_j:
        print(f"🚫 [{source_tag}-H1-EXHAUSTED] {_sym_tmp} ({mint[:8]}): h1 {_h1_j:+.0f}% > +{_max_h1_j:.0f}% — памп уже отработал, вход = вершина.")
        return False
    _h6_j_raw = _pc_j.get("h6")
    _fdv_j1 = float((td or {}).get("fdv", 0) or (td or {}).get("marketCap", 0) or 0)
    _mom_j_ok, _mom_j_why = config.evaluate_entry_momentum(
        m5=_m5_j, m1=_m1_j, h1=_h1_j,
        h6=(float(_h6_j_raw) if _h6_j_raw is not None else None),
        age_min=_age_j, vol5_usd=_vol5_j, liq_usd=_liq_j,
        buys5=_b5_j, sells5=_s5_j,
        min_h1=float(getattr(config, "JEV_MIN_H1_PCT", -15.0)),
        min_h6=float(getattr(config, "JEV_MIN_H6_PCT", -25.0)),
        fdv_usd=_fdv_j1,
    )
    _min_m1_j = float(getattr(config, "JEV_MIN_M1_ENTRY_FLOOR_PCT", -3.5))
    if _age_j <= 15.0 and (_b5_j >= _s5_j or _m5_j >= 0.5):
        _min_m1_j = min(_min_m1_j, -5.0)
    elif (_vol5_j >= 10000.0 and _b5_j >= int(_s5_j * 1.15)) or (_h1_j >= 25.0 and _b5_j >= int(_s5_j * 1.25) and _vol5_j >= 4500.0):
        _min_m1_j = min(_min_m1_j, -4.5)
    if not _mom_j_ok or _m1_j < _min_m1_j:
        print(f"🚫 [{source_tag}-MOMENTUM-LOST] {_sym_tmp} ({mint[:8]}): импульс отклонён ({_mom_j_why}, m5 {_m5_j:+.1f}%, m1 {_m1_j:+.1f}%) — отмена входа.")
        return False
    _pk_drop_j = float((td or {}).get("_drop_from_peak_5m", 0.0) or 0.0)
    _lq_drop_j = float((td or {}).get("_liq_drop_from_peak", 0.0) or 0.0)
    if _pk_drop_j <= -38.0 or _lq_drop_j <= -45.0:
        print(f"🚫 [{source_tag}-POST-PEAK-CRASH] {_sym_tmp} ({mint[:8]}): токен уже слился от пика (цена {_pk_drop_j:+.1f}%, ликва {_lq_drop_j:+.1f}%) — дохлая кошка.")
        return False

    # Защита от кастомных фейк-пулов и Remove-LP ловушек (PumpSwap/Meteora/Raydium без launchpad-суффикса)
    _is_grad_jup = False
    try:
        from birth_tracker import birth_tracker as _bt_j
        _is_grad_jup = _bt_j.is_graduated(mint)
    except Exception:
        pass
    _dex_j_low = str((td or {}).get("dexId", "")).lower()
    if config.is_pumpswap_liq_fdv_trap(td):
        print(f"🚫 [JUP-PUMPSWAP-TRAP] {mint[:8]}: custom CreatePool liq/fdv trap — отмена!")
        return False
    _ps_j_canon = False
    if _dex_j_low == "pumpswap":
        import goplus as _gp_j
        _ps_j_ok, _ps_j_canon, _ps_j_why = await _gp_j.verify_pumpswap_pool_onchain(
            mint,
            pair_address=str((td or {}).get("pairAddress", "") or ""),
            pair_data=td,
        )
        if not _ps_j_ok:
            print(f"🚫 [JUP-PUMPSWAP] {mint[:8]}: {_ps_j_why} — отмена!")
            return False
    _is_native_pump_jup = (
        (_ps_j_canon and _dex_j_low == "pumpswap")
        or (mint.lower().endswith("pump") and _dex_j_low in ("pump", "pumpfun"))
    )
    _lp_verified_jup = _is_native_pump_jup
    if not _is_native_pump_jup:
        try:
            import goplus as _gp_jup
            _rc_st, _rc_d = await _gp_jup.fetch_rugcheck_report_cached(mint, ttl=120.0, timeout=5)
            _allow_unl_lp_j = bool(getattr(config, "ALLOW_UNLOCKED_LP_POOLS", True))
            if _rc_st != 200 or not isinstance(_rc_d, dict):
                if _allow_unl_lp_j:
                    print(f"🔓 [{source_tag}-RC-SKIP] {_sym_tmp} ({mint[:8]}): RugCheck HTTP {_rc_st} — разрешаем быстрый вход без задержки (режим 0d2b1ae).")
                    _rc_d = {}
                else:
                    print(f"⏳ [{source_tag}-RC-WAIT] {_sym_tmp} ({mint[:8]}): кастомный токен ожидает индексации RugCheck (HTTP {_rc_st}).")
                    return False
            _tok_rc = _rc_d.get("token") or {}
            _SAFE_AUTHS = {"6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P", "11111111111111111111111111111111", None, ""}
            if _tok_rc and (_tok_rc.get("mintAuthority") not in _SAFE_AUTHS or _tok_rc.get("freezeAuthority") not in _SAFE_AUTHS):
                print(f"🚫 [{source_tag}-AUTH-SCAM] {_sym_tmp} ({mint[:8]}): активный mint/freeze authority — блок входа!")
                return False
            # 1. Проверка критической концентрации монет у одного кошелька (кейсы CRO, NEX AI) и серийных рагеров
            for _rk in (_rc_d.get("risks") or []):
                _rkn = str(_rk.get("name", "")).lower()
                _rkl = str(_rk.get("level", "")).lower()
                if _rkl == "danger" and any(k in _rkn for k in ("single holder", "high ownership", "creator holding", "creator history")):
                    print(f"🚫 [{source_tag}-SINGLE-HOLDER] {_sym_tmp} ({mint[:8]}): RugCheck '{_rk.get('name')}' — дев-рагер или контролирует эмиссию!")
                    return False
            # 2. Очищенный score RugCheck
            _raw_sc = float(_rc_d.get("score") or 0.0)
            _ign_sc = sum(float(rk.get("score", 0) or 0) for rk in (_rc_d.get("risks") or []) if any(k in str(rk.get("name", "")).lower() for k in ("low liquidity", "copycat", "symbol mimic", "large amount of lp unlocked", "lp not locked", "unlocked lp")))
            if (_raw_sc - _ign_sc) >= 1200:
                print(f"🚫 [{source_tag}-HIGH-RISK] {_sym_tmp} ({mint[:8]}): RugCheck Score {(_raw_sc - _ign_sc):.0f} >= 1200 — блок скама!")
                return False

            _target_pair_j = str((td or {}).get("pairAddress", "") or "").strip()
            _ps_rpc_unv_jup = bool((td or {}).get("_pumpswap_rpc_unverified")) and config.is_launchpad_mint(mint) and not config.is_pumpswap_liq_fdv_trap(td or {})
            _allow_ps_lock_j = bool((td or {}).get("_pumpswap_canonical")) or _ps_rpc_unv_jup or (_dex_j_low != "pumpswap")
            _lp_ok = config.verify_rugcheck_lp_markets(
                _rc_d.get("markets") or [],
                target_pair=_target_pair_j,
                raw_liq=_liq_j,
                symbol=_sym_tmp,
                mint=mint,
                log_unlocked=True,
                allow_pump_amm_lock=_allow_ps_lock_j,
            ) if _rc_d else False
            _lp_verified_jup = bool(_lp_ok)
            if not _allow_unl_lp_j and mint.endswith("pump") and ("meteora" in _dex_j_low or _dex_j_low == "meteoradbc"):
                print(f"🚫 [{source_tag}-FAKE-METEORA] {_sym_tmp} ({mint[:8]}): .pump токен на пуле Meteora — неофициальный скам-пул мимо PumpSwap (кейсы MULT/SURVIVE -98%)!")
                return False

            if not _lp_ok:
                _b5_j = int((((td or {}).get("txns") or {}).get("m5") or {}).get("buys", 0) or 0)
                _s5_j = int((((td or {}).get("txns") or {}).get("m5") or {}).get("sells", 0) or 0)
                _is_unl_safe_j = config.is_safe_unlocked_custom_runner(
                    liq_usd=_liq_j,
                    age_min=_age_j,
                    vol5_usd=_vol5_j,
                    buys5=_b5_j,
                    sells5=_s5_j,
                    has_socials=bool(len(((td or {}).get("info") or {}).get("socials", []) or [])),
                    is_trap=config.is_pumpswap_liq_fdv_trap(td or {}),
                )
                if _is_unl_safe_j:
                    print(f"🔓 [{source_tag}-UNLOCKED-LP-ALLOWED] {_sym_tmp} ({mint[:8]}): свежий DEX-раннер (age {_age_j:.1f}м, liq ${_liq_j:,.0f}, vol5 ${_vol5_j:,.0f}) — лок LP не требуется (режим 0d2b1ae).")
                    _lp_ok = True
                    _lp_verified_jup = True
                else:
                    print(f"🚫 [{source_tag}-UNLOCKED-LP] {_sym_tmp} ({mint[:8]}): незалоченный/несожжённый LP на {_dex_j_low or 'DEX'} (liq ${_liq_j:,.0f}, age {_age_j:.1f}m) — риск Remove LP!")
                    return False
        except Exception as _lp_ex:
            if getattr(config, "ALLOW_UNLOCKED_LP_POOLS", True) and _liq_j >= float(getattr(config, "MIN_GRADUATED_LIQ_USD", 3000.0)):
                print(f"🔓 [{source_tag}-RC-ERR-SKIP] {_sym_tmp} ({mint[:8]}): ошибка RugCheck ({_lp_ex}) — разрешаем быстрый вход (режим 0d2b1ae).")
            else:
                print(f"⏳ [{source_tag}-RC-ERR] {_sym_tmp} ({mint[:8]}): ошибка проверки RugCheck ({_lp_ex}) — пропуск цикла.")
                return False
    if isinstance(td, dict) and _lp_verified_jup:
        td["_lp_verified"] = True

    # Защита от анонимных бандлер-пулов без соцсетей на DEX
    # Канонические launchpad-токены (pump, moon, bonk, BAGS) и ликвидные пулы >$25k допускаются
    _info_j = (td or {}).get("info") or {}
    _soc_j = _info_j.get("socials") or []
    _web_j = _info_j.get("websites") or []
    _req_soc_min_j = float(getattr(config, "REQUIRE_SOCIALS_MIN_AGE_MIN", 9999.0))
    if getattr(config, "REQUIRE_SOCIALS_ENABLED", False) and not _is_launchpad_jup and not (td or {}).get("_socials_unknown", False) and len(_soc_j) == 0 and len(_web_j) == 0 and _liq_j < 25000 and _age_j > _req_soc_min_j:
        print(f"🚫 [{source_tag}-NO-SOCIALS] {_sym_tmp} ({mint[:8]}): 0 соцсетей/сайтов при ликвидности ${_liq_j:,.0f} (<$25k) — анонимный бандлер-раг.")
        return False

    # Защита от миграционного дампа Pump.fun: выдерживаем минимум SOLANA_MIN_AGE_MIN, далее вход под полным ончейн-аудитом
    if mint.endswith("pump") and _dex_j in ("pumpswap", "raydium", "meteora", "orca"):
        _min_mig_age_j = float(getattr(config, "SOLANA_MIN_AGE_MIN", 0.15))
        if _age_j < _min_mig_age_j:
            print(f"⏳ [{source_tag}-MIGRATION-DUMP] {_sym_tmp} ({mint[:8]}): AMM-пул создан {_age_j:.1f}м назад (< {_min_mig_age_j:.1f}м) — зона сброса снайперов pump.fun.")
            return False
        _fdv_j = float((td or {}).get("fdv", 0) or (td or {}).get("marketCap", 0) or 0)
        _min_mig_fdv_j = float(getattr(config, "JEV_MIN_MCAP_USD", 4000.0))
        if 0 < _fdv_j < _min_mig_fdv_j:
            print(f"🚫 [{source_tag}-MIGRATION-LOW-FDV] {_sym_tmp} ({mint[:8]}): FDV ${_fdv_j:,.0f} < ${_min_mig_fdv_j:,.0f} — токен слит ниже уровня миграции pump.fun.")
            return False
        _min_mig_liq_j = float(getattr(config, "MIN_GRADUATED_LIQ_USD", 3000.0))
        if _liq_j < _min_mig_liq_j:
            print(f"🚫 [{source_tag}-MIGRATION-LOW-LIQ] {_sym_tmp} ({mint[:8]}): ликвидность ${_liq_j:,.0f} < ${_min_mig_liq_j:,.0f} — дев/снайперы высосали SOL.")
            return False
        
    from jupiter import JupiterAPI
    ds_price = float((td or {}).get("priceUsd", 0) or 0.0)
    jup_price = await JupiterAPI.get_price(mint)
    if ds_price > 0 and jup_price and jup_price > 0:
        if jup_price < ds_price * 0.925:
            analyzer._deny(mint, "jup-live-dump >7.5%", f"🚫 [{source_tag}-LIVE-DUMP] {_sym_tmp} ({mint[:8]}): живая котировка Jupiter (${jup_price:.8f}) уже ниже DexScreener (${ds_price:.8f}) на {(1.0 - jup_price/ds_price)*100:.1f}% [>7.5%] — слив в процессе!")
            return False
        _up_ratio = jup_price / ds_price
        if _up_ratio > 1.25:
            print(f"🚫 [{source_tag}-PRICE-MISMATCH] {_sym_tmp} ({mint[:8]}): Jupiter (${jup_price:.8f}) vs Dex (${ds_price:.8f}) опережение {_up_ratio:.2f}x (>1.25x) — блок от вертикального входа.")
            return False
        # На сырых кривых pump/pumpfun НЕ перезаписываем ds_price завышенной котировкой роутера Jupiter (исключает искусственные стопы -25% на 1-м тике):
        price = ds_price if _dex_j_low in ("pump", "pumpfun") else jup_price
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
    _age_j = ((time.time() * 1000 - _c_at_j) / 60000.0) if _c_at_j else float((td or {}).get("_gt_age_min") or 0.0)
    _max_m5_cfg = float(getattr(config, "MAX_ENTRY_M5_PCT", 120.0))
    _max_age_cfg = float(getattr(config, "SOLANA_MAX_AGE_MIN", 720.0))
    _req_soc_age = float(getattr(config, "REQUIRE_SOCIALS_MIN_AGE_MIN", 20.0))
    _vm5_j = float(((td or {}).get("volume") or {}).get("m5", 0) or 0)
    _b5_j = int((((td or {}).get("txns") or {}).get("m5") or {}).get("buys", 0) or 0)
    _s5_j = int((((td or {}).get("txns") or {}).get("m5") or {}).get("sells", 0) or 0)

    # 0. КАРАНТИН БЛОКА 0 (минимум 24 секунды со 2-й свечи):
    # Защищает от снайперских бандлов блока 0, но не задерживает вход со 2-й свечи ракеты.
    _min_age_jup = float(getattr(config, "SOLANA_MIN_AGE_MIN", 0.40))
    if _age_j < _min_age_jup:
        print(f"⏳ [{source_tag}-AGE-WARMUP] {_sym_tmp}: возраст {_age_j*60:.0f}с < {_min_age_jup*60:.0f}с — карантин 1-й свечи (защита от снайперов и бандл-рага дева)")
        return False

    # 0.1 Защита от падающего ножа (единый классификатор 2x-ракет)
    _h6_j2_raw = ((td or {}).get("priceChange") or {}).get("h6")
    _liq_j2 = float(((td or {}).get("liquidity") or {}).get("usd", 0) or 0)
    _mom_j2_ok, _mom_j2_why = config.evaluate_entry_momentum(
        m5=_m5_j, m1=_m1_j, h1=_h1_j,
        h6=(float(_h6_j2_raw) if _h6_j2_raw is not None else None),
        age_min=_age_j, vol5_usd=_vm5_j, liq_usd=_liq_j2,
        buys5=_b5_j, sells5=_s5_j,
        min_h1=float(getattr(config, "JEV_MIN_H1_PCT", -12.0)),
        min_h6=float(getattr(config, "JEV_MIN_H6_PCT", -20.0)),
        fdv_usd=_fdv_j,
    )
    if not _mom_j2_ok:
        print(f"🚫 [{source_tag}-DOWNTREND] {_sym_tmp}: {_mom_j2_why} — отмена входа!")
        return False
    _min_jup_m1 = float(getattr(config, "JEV_MIN_M1_ENTRY_FLOOR_PCT", -3.5))
    if _age_j <= 15.0 and (_b5_j >= _s5_j or _m5_j >= 0.5):
        _min_jup_m1 = min(_min_jup_m1, -5.0)
    if _m1_j < _min_jup_m1:
        print(f"🚫 [{source_tag}-1M-DUMP] {_sym_tmp}: 1м свеча {_m1_j:+.1f}% < {_min_jup_m1:+.1f}% — отмена входа!")
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


    # 3. Защита от давления продавцов: блокируем ТОЛЬКО если продавцы реально преобладают (не режем скоростные пробойные ракеты GREGORY b/s >= 0.90)
    _tot_tx_j = _b5_j + _s5_j
    _m1_j = float((_pc_j or {}).get("m1", 0) or 0)
    _is_hi_vel_j = (_vm5_j >= 20000.0 and _m5_j >= 8.0 and _b5_j >= 100 and _m1_j >= -4.0 and (_s5_j == 0 or _b5_j >= _s5_j * 0.90))
    if _tot_tx_j >= 25 and _s5_j > 0 and not _is_hi_vel_j:
        _sell_ratio_j = _s5_j / _tot_tx_j
        _vl_ratio_j = (_vm5_j / max(_liq_j, 1.0)) if _liq_j > 0 else 0.0
        if _b5_j < _s5_j and (_sell_ratio_j >= 0.52 or (_sell_ratio_j >= 0.48 and _vl_ratio_j >= 3.5)):
            print(f"🚫 [{source_tag}-SELL-PRESSURE] {_sym_tmp}: продавцов {_s5_j}/{_tot_tx_j} ({_sell_ratio_j*100:.0f}%) при v/l {_vl_ratio_j:.1f}x — сброс китов, отмена!")
            return False

    _eff_max_h1_j = config.get_max_entry_h1_pct(_age_j, _vm5_j, _b5_j, _s5_j, _liq_j, _m5_j)
    if _h1_j > _eff_max_h1_j:
        print(f"🚫 [{source_tag}-FOMO-H1] {_sym_tmp}: h1 +{_h1_j:.0f}% > +{_eff_max_h1_j:.0f}% — перегретый часовой памп, отмена!")
        return False
    _info_j = (td or {}).get("info") or {}
    _links_j = len(_info_j.get("socials", []) or []) + len(_info_j.get("websites", []) or [])
    _soc_unk_j = bool((td or {}).get("_socials_unknown", False))
    if getattr(config, "REQUIRE_SOCIALS_ENABLED", False) and _age_j >= _req_soc_age and _links_j == 0 and not _soc_unk_j:
        _avg_j = _vm5_j / max(1, _b5_j + _s5_j)
        _vl_j = _vm5_j / max(_liq_j, 1.0)
        _is_organic_stealth = (_vm5_j >= 4000.0 and (_vl_j >= 0.50 or _vm5_j >= 20000.0) and _b5_j >= 20 and _b5_j >= _s5_j * 1.05 and _liq_j >= 8000.0 and _avg_j >= 8.0 and _vl_j <= 8.0)
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

    # Ончейн аудит бандлов/холдеров/дева (синхронизировано со scanner_loop)
    if getattr(config, "RUG_WALLET_AUDIT_ENABLED", getattr(config, "RUG_WALLET_ANALYSIS_ENABLED", True)):
        try:
            from rug_wallet_analysis import quick_rug_check
            _is_ok_rw, _rw_reason = await quick_rug_check(mint)
            if not _is_ok_rw:
                _rw_l = str(_rw_reason).lower()
                _rw_rpc_err = any(e in _rw_l for e in ("429", "403", "timeout", "error", "none", "rpc", "http", "retry"))
                if not _rw_rpc_err:
                    print(f"🚫 [{source_tag}-RUG-WALLET] {_sym_tmp} ({mint[:8]}): {_rw_reason} — вход заблокирован.")
                    return False
        except Exception as _rw_e:
            print(f"⚠️ [{source_tag}-RUG-WALLET] {_sym_tmp} ({mint[:8]}): audit err {type(_rw_e).__name__}: {_rw_e}")

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
                _ref_dump_px = min(price, ds_price) if ds_price > 0 else price
                if _jup_live_px < _ref_dump_px * 0.925:
                    print(f"🚫 [{source_tag}-LIVE-DUMP] {_sym_tmp} ({mint[:8]}): живая цена Jupiter (${_jup_live_px:.8f}) уже на {(1.0 - _jup_live_px / _ref_dump_px) * 100:.1f}% ниже ориентира (${_ref_dump_px:.8f}) — обвал в моменте, отмена!")
                    return False
                _jup_up_ratio = _jup_live_px / price
                if _jup_up_ratio > 1.30:
                    print(f"🚫 [{source_tag}-PRICE-MISMATCH] {_sym_tmp} ({mint[:8]}): Jupiter vs сигнал опережение {_jup_up_ratio:.2f}x (>1.30x) — отмена!")
                    return False
                price = _jup_live_px
                if _wick_stop_price and _wick_pct:
                    _wick_stop_price = price * (1.0 + _wick_pct)
        except Exception:
            pass

        if not tracker.add_position(
            _sym_tmp, mint, price, min(size, 100.0),
            is_mature=True,
            ml_features=analyzer.pack_features(td or {}),
            ml_confidence=_mint_ml_conf,
            source=f"{source_tag}:{analyzer.get_signal(mint)}",
            pair_address=((td or {}).get("pairAddress", "") or ""),
            stop_under_wick=_wick_stop_price,
            wick_stop_loss_pct=_wick_pct,
            initial_liquidity_usd=_j_liq,
            pair_data=td):
            return False
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
                    ok = await asyncio.wait_for(analyzer.analyze_token(mint), timeout=25.0)
                except asyncio.TimeoutError:
                    print(f"⏱️ [JUP-MATURE-TIMEOUT] {mint[:8]}: анализ превысил 25с, пропуск")
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
                        _to_pf = [m for m in mints if m not in open_jup and (_t.time() - processed.get(m, 0.0)) >= 18]
                        if _to_pf:
                            await analyzer.prefetch_token_data(_to_pf, ttl=12.0)
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
                    _min_age = float(getattr(config, "SOLANA_MIN_AGE_MIN", 0.40))

                    for m in mints:
                        if not m or m in open_jup or m.startswith("0x"):
                            continue
                        if m in tracker.positions:
                            _cp = tracker.positions.get(m)
                            _cpnl = float(getattr(_cp, "pnl_usd", 0.0) or 0.0)
                            _cext = float(getattr(_cp, "exit_time", 0.0) or 0.0)
                            if (_now_t - _cext) < (300.0 if _cpnl > 0 else 600.0):
                                continue
                        if _now_t - processed.get(m, 0.0) < 18:
                            continue

                        hit = getattr(analyzer, "_prefetch_cache", {}).get(m)
                        td = hit[1] if (hit and hit[1]) else None
                        if not td:
                            try:
                                import market_data as _md_jfb
                                _jhit = getattr(_md_jfb, "_JUP_TD_CACHE", {}).get(m)
                                if _jhit and (_now_t - _jhit[0]) < 90.0 and isinstance(_jhit[1], dict):
                                    td = dict(_jhit[1])
                                    if hasattr(analyzer, "_prefetch_cache"):
                                        analyzer._prefetch_cache[m] = (_jhit[0], td)
                            except Exception:
                                pass
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
                            _min_c = float(getattr(config, "MIN_PUMP_CURVE_MCAP_USD", 3500.0))
                            _max_c = float(getattr(config, "MAX_PUMP_CURVE_MCAP_USD", 65000.0))
                            if not getattr(config, "ALLOW_UNGRADUATED_PUMPFUN", False) or (0 < _fdv_cand < _min_c) or (_fdv_cand > _max_c):
                                processed[m] = _now_t
                                continue

                        # Зона созревания: отправляем в быстрый чекер
                        if _ag < _min_age:
                            immature_count += 1
                            created_sec = (_cr / 1000.0) if _cr else (_now_t - _ag * 60.0)
                            ready_at = created_sec + (_min_age * 60.0) + 1.0
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
                        _fdv_dex = float(td.get("fdv", 0) or td.get("marketCap", 0) or 0)
                        _h6_cand = (td.get("priceChange") or {}).get("h6")
                        _m1_cand = float((td.get("priceChange") or {}).get("m1", 0) or 0)
                        _tx_cand = (td.get("txns") or {}).get("m5") or {}
                        _b5_cand = int(_tx_cand.get("buys", 0) or 0)
                        _s5_cand = int(_tx_cand.get("sells", 0) or 0)

                        # Отсекаем тяжёлые (> $450k FDV) и отработанные 6h (+500%+) топы до оценки ML:
                        if _fdv_dex > float(getattr(config, "JEV_MAX_MCAP_USD", 450000.0)):
                            skipped_ancient += 1
                            processed[m] = _now_t + 180
                            continue
                        if _ag >= 20.0 and _h6_cand is not None and float(_h6_cand or 0) > float(getattr(config, "MAX_ENTRY_H6_PCT", 500.0)):
                            skipped_ancient += 1
                            processed[m] = _now_t + 180
                            continue

                        # Anti-FOMO: блокируем покупку вертикального пампа на вершине свечи (кейсы GOSSIP +1330%)
                        _max_entry_m5 = float(getattr(config, "MAX_ENTRY_M5_PCT", 180.0))
                        _is_strong_breakout = (_m5 <= max(_max_entry_m5 * 1.35, 250.0) and _b5_cand >= int(_s5_cand * 1.2) and _v5_cand >= 8000.0)
                        if _m5 > _max_entry_m5 and not _is_strong_breakout:
                            skipped_ancient += 1
                            processed[m] = _now_t + 120
                            continue

                        # Режем старые токены > SOLANA_MAX_AGE_MIN:
                        _max_age_cand = float(getattr(config, "SOLANA_MAX_AGE_MIN", 75.0))
                        if _ag > _max_age_cand:
                            skipped_ancient += 1
                            processed[m] = _now_t + 600
                            continue

                        # Ультра-ранний захват старта + живой восходящий импульс:
                        # Защита от явного преобладания продавцов (сброс когда продавцы давят сильнее покупок на 35%+):
                        if _s5_cand >= 8 and (_b5_cand / _s5_cand) < 0.65 and _v5_cand < 25000.0:
                            skipped_flat += 1
                            processed[m] = _now_t + 30
                            continue
                        _is_turned_green_cand = (
                            -1.5 <= _m5 < 2.0
                            and _ag <= 30.0
                            and _m1_cand >= 0.5
                            and _lq_cand >= float(getattr(config, "MIN_GRADUATED_LIQ_USD", 2500.0))
                            and _v5_cand >= 1500.0
                            and _b5_cand >= max(3, _s5_cand)
                        )
                        _min_m5_cand = 0.4 if _ag <= 3.0 else 1.2
                        if (_m5 < _min_m5_cand and not _is_turned_green_cand) or _m1_cand < -3.0:
                            skipped_flat += 1
                            processed[m] = _now_t
                            continue
                        _turnover = (_v5_cand / max(_lq_cand, 1.0))
                        _age_fdv_boost = (50.0 if _ag <= 3.0 else (25.0 if _ag <= 10.0 else (-20.0 if _ag > 30.0 else 0.0))) + (20.0 if 0 < _fdv_dex <= 45000.0 else 0.0)
                        # Оценка ракеты: если включена Rocket XGBoost v2, ранжируем с жёстким приоритетом ультра-свежести
                        if getattr(config, "ROCKET_XGB_ENABLED", True) and hasattr(analyzer, "score_rocket_xgb"):
                            _rkt_score = analyzer.score_rocket_xgb(m, td) + _age_fdv_boost
                        else:
                            _rkt_score = (_m5 * 1.5) + min(_turnover * 30.0, 60.0) + _age_fdv_boost
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

                            # Баним повторный вход на 4 часа после ЛЮБОЙ убыточной сделки по минту (предотвращает 3x сливы BILL):
                            if hasattr(tracker, "is_loss_cooldown_active") and tracker.is_loss_cooldown_active(mint, float(getattr(config, "MINT_LOSS_COOLDOWN_SEC", 1800.0))):
                                return

                            print(f"🪐 [JUP] Анализирую {_sym_val} ({mint[:8]}): возраст {_ag_val:.1f}м, m5 {_m5_val:+.1f}%...")
                            try:
                                ok = await asyncio.wait_for(analyzer.analyze_token(mint), timeout=25.0)
                            except asyncio.TimeoutError:
                                print(f"⏱️ [JUP-TIMEOUT] {mint[:8]}: анализ превысил 25с, пропуск")
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
                                "delegation", "zombie", 
                                "deadcat", "dead-cat", "ghost-pool", "wash-spike", 
                                "wash-trap", "mcap-too-large", "price-split"
                            ))
                            if ok is False:
                                processed[mint] = _t.time() + (3600 if _is_term_j else 0)
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
