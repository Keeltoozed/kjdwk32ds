"""Единый источник рыночных данных.

Приоритет: GeckoTerminal (стабильно работает с Render, без Cloudflare-блока,
проверено: token, pools, trending, search, ohlcv).
DexScreener остаётся fallback'ом внутри get_token_data (fail-open).

Нормализация: get_token_data возвращает словарь в форме DexScreener-пары,
чтобы остальной код (analyzer, sniper) не менять:
  baseToken{address,symbol,name}, priceUsd, liquidity{usd},
  volume{m5,h24}, txns{m5:{buys,sells},h24:{...}}, priceChange{h24,...},
  fdv, marketCap, pairCreatedAt (ms), dexId, info{socials,websites},
  _source, _socials_unknown (True, если соцсети проверить негде).
"""
import asyncio
import re
import time
from datetime import datetime, timezone

BASE = "https://api.geckoterminal.com/api/v2"
HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}

_locks: dict = {}

def _get_lock():
    try:
        loop = asyncio.get_running_loop()
        k = id(loop)
    except RuntimeError:
        k = 0
    if k not in _locks:
        _locks[k] = asyncio.Lock()
    return _locks[k]

_last_call = 0.0
MIN_INTERVAL = 2.5  # ~24/мин на весь процесс: квота GT ~30/мин, запас от 429
_jlast = 0.0
J_MIN_INTERVAL = 0.8  # пауза между Token API запросами к Jupiter

# Кэш данных токенов: сканер и FOMO запрашивают одни и те же минты по кругу.
# Без кэша свежие токены съедают весь лимит 30/мин и всё падает с 429.
_TD_CACHE = {}  # mint -> (timestamp_monotonic, data)
TD_TTL = 20.0  # секунд свежие данные считаются годными


_gt_consecutive_fails = 0
_gt_breaker_until = 0.0


async def _gt_get(path: str, retries: int = 0, timeout: int = 6):
    """GET к GeckoTerminal через ГЛОБАЛЬНЫЙ семафор http_client (Sem=1) и локальный пейсер.
    ВАЖНО:
    - Circuit Breaker: при 4 сбоях подряд (таймауты/5xx/429) пауза 25с (fail-fast, не забиваем очередь).
    - default retries=0 и timeout=6 для быстрого дискавери.
    - 429 НЕ ретраится (повторный долбёж усугубляет бан). Возвращает dict или {}."""
    global _gt_consecutive_fails, _gt_breaker_until, _last_call
    from http_client import fetch_json
    now_m = time.monotonic()
    if now_m < _gt_breaker_until:
        return {}  # Circuit breaker open: fail-fast

    async with _get_lock():
        wait_s = 3.0 - (time.monotonic() - _last_call)
        if wait_s > 0:
            await asyncio.sleep(wait_s)
        _last_call = time.monotonic()
        status, data = await fetch_json(BASE + path, headers=HEADERS, timeout=timeout, retries=0)
    if status == 200 and isinstance(data, dict):
        _gt_consecutive_fails = 0
        return data
    if status == 404:
        return {}  # токен/пул ещё не проиндексирован — ретраи бессмысленны
    if status == 429:
        _gt_consecutive_fails += 1
        if _gt_consecutive_fails >= 4:
            _gt_consecutive_fails = 0
            _gt_breaker_until = time.monotonic() + 15.0
            print("⚡ [GT-BREAKER] GeckoTerminal 429 -> пауза GT на 15с (fail-fast, не блочим систему).")
        return {}

    # 5xx / timeout:
    _gt_consecutive_fails += 1
    if _gt_consecutive_fails >= 4:
        _fails_cnt = _gt_consecutive_fails
        _gt_consecutive_fails = 0
        _gt_breaker_until = time.monotonic() + 15.0
        print(f"⚡ [GT-BREAKER] GeckoTerminal {_fails_cnt} сбоя подряд -> пауза GT на 15с.")

    last_err = f"HTTP {status}"
    for attempt in range(retries):
        await asyncio.sleep(2 + 2 * attempt)
        status, data = await fetch_json(BASE + path, headers=HEADERS, timeout=timeout, retries=0)
        if status == 200 and isinstance(data, dict):
            _gt_consecutive_fails = 0
            return data
        if status in (404, 429):
            break
        last_err = f"HTTP {status}"

    if status != 200 and status != 429:
        print(f"🔎 GT fail {path.split('?')[0][:60]}: {last_err}")
    return {}


def _parse_ms(ts) -> int:
    try:
        if not ts:
            return 0
        s = str(ts).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            s_clean = re.sub(r"\.\d+", "", s)
            dt = datetime.fromisoformat(s_clean)
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


def _dex_id_from_pool(item: dict) -> str:
    try:
        dex = item.get("relationships", {}).get("dex", {}).get("data", {}).get("id", "")
        dex = str(dex).lower().strip()
        if "pumpswap" in dex or "pump-swap" in dex or "pump_swap" in dex:
            return "pumpswap"
        if dex in ("pump", "pump-fun", "pumpfun", "pump.fun"):
            return "pump"
        if "meteora-dbc" in dex or "meteoradbc" in dex or "dynamic-bonding" in dex:
            return "meteoradbc"
        if "meteora" in dex:
            return "meteora"
        if "raydium" in dex:
            return "raydium"
        if "orca" in dex or "whirlpool" in dex:
            return "orca"
        return dex or "unknown"
    except Exception:
        return "unknown"


def _base_mint(item: dict) -> str:
    try:
        bid = item.get("relationships", {}).get("base_token", {}).get("data", {}).get("id", "")
        mint = bid.split("_", 1)[1] if "_" in bid else ""
        # Solana-адреса base58 (32-44 символа) и не начинаются с 0x
        if not mint or mint.startswith("0x") or len(mint) < 32 or len(mint) > 44:
            return ""
        return mint
    except Exception:
        return ""


def _normalize_pool(mint: str, name: str, symbol: str, item: dict, token_attrs: dict) -> dict:
    a = item.get("attributes", {})
    vol = a.get("volume_usd", {}) or {}
    tx = a.get("transactions", {}) or {}
    chg = a.get("price_change_percentage", {}) or {}
    m5 = tx.get("m5", {}) or {}
    h24 = tx.get("h24", {}) or {}
    try:
        liq = float(a.get("reserve_in_usd", 0) or 0)
    except Exception:
        liq = 0.0
    try:
        price = float(a.get("base_token_price_usd", 0) or 0)
    except Exception:
        price = 0.0
    try:
        fdv = float(token_attrs.get("fdv_usd", 0) or 0)
    except Exception:
        fdv = 0.0
    try:
        mcap = float(token_attrs.get("market_cap_usd", 0) or 0)
    except Exception:
        mcap = 0.0
    return {
        "chainId": "solana",
        "dexId": _dex_id_from_pool(item),
        "pairAddress": a.get("address", ""),
        "baseToken": {"address": mint, "name": name, "symbol": symbol},
        "priceUsd": str(price),
        "liquidity": {"usd": liq},
        "volume": {"m5": float(vol.get("m5", 0) or 0), "h24": float(vol.get("h24", 0) or 0)},
        "txns": {"m5": {"buys": int(m5.get("buys", 0) or 0), "sells": int(m5.get("sells", 0) or 0)},
                 "h24": {"buys": int(h24.get("buys", 0) or 0), "sells": int(h24.get("sells", 0) or 0)}},
        "priceChange": {"m5": float(chg.get("m5", 0) or 0), "h1": float(chg.get("h1", 0) or 0),
                        "h6": float(chg.get("h6", 0) or 0), "h24": float(chg.get("h24", 0) or 0)},
        "fdv": fdv,
        "marketCap": mcap,
        "pairCreatedAt": _parse_ms(a.get("pool_created_at")),
        "info": {"socials": [], "websites": []},
        "_source": "geckoterminal",
        "_socials_unknown": True,  # GT не отдаёт соцсети — фильтр соцсетей пропускаем
    }


_JUP_TD_CACHE: dict = {}


def normalize_jup_token(t: dict) -> dict:
    """Нормализует объект токена из Jupiter Tokens API v2 в формат DexScreener pair_data."""
    if not isinstance(t, dict):
        return {}
    mint = str(t.get("id") or "").strip()
    if not mint:
        return {}
    try:
        s5 = t.get("stats5m", {}) or {}
        s1 = t.get("stats1h", {}) or {}
        s6 = t.get("stats6h", {}) or {}
        s24 = t.get("stats24h", {}) or {}
        try:
            price = float(t.get("usdPrice", 0) or 0)
        except Exception:
            price = 0.0
        if price <= 0:
            return {}
        try:
            liq = float(t.get("liquidity", 0) or 0)
        except Exception:
            liq = 0.0
        try:
            fdv = float(t.get("fdv", 0) or 0)
        except Exception:
            fdv = 0.0
        try:
            mcap = float(t.get("mcap", 0) or fdv)
        except Exception:
            mcap = fdv
        graduated = bool(t.get("graduatedAt") or t.get("graduatedPool") or (not mint.endswith("pump") and liq >= 5000.0))
        socials, websites = [], []
        if t.get("twitter"):
            socials.append({"type": "twitter", "url": t["twitter"]})
        if t.get("telegram"):
            socials.append({"type": "telegram", "url": t["telegram"]})
        if t.get("website"):
            websites.append({"url": t["website"]})
        try:
            bv5 = float(s5.get("buyVolume", 0) or 0) + float(s5.get("sellVolume", 0) or 0)
        except Exception:
            bv5 = 0.0
        try:
            bv1 = float(s1.get("buyVolume", 0) or 0) + float(s1.get("sellVolume", 0) or 0)
        except Exception:
            bv1 = 0.0
        try:
            bv24 = float(s24.get("buyVolume", 0) or 0) + float(s24.get("sellVolume", 0) or 0)
        except Exception:
            bv24 = 0.0
        first_pool_obj = t.get("firstPool") if isinstance(t.get("firstPool"), dict) else {}
        grad_pool_raw = t.get("graduatedPool")
        pair_addr_jup = (
            grad_pool_raw.get("id", "") if isinstance(grad_pool_raw, dict)
            else (str(grad_pool_raw) if grad_pool_raw else str(first_pool_obj.get("id", "") or ""))
        )
        created_ts_raw = t.get("graduatedAt") or first_pool_obj.get("createdAt") or t.get("createdAt")
        _pool_dex_hint = str(first_pool_obj.get("dex") or t.get("launchpad") or "").lower()
        if not graduated:
            dex_out = "pump"
        elif mint.endswith("pump") or "pump" in _pool_dex_hint:
            dex_out = "pumpswap"
        elif "meteora" in _pool_dex_hint:
            dex_out = "meteora"
        else:
            dex_out = "raydium"
        out = {
            "chainId": "solana",
            "dexId": dex_out,
            "pairAddress": pair_addr_jup,
            "baseToken": {"address": mint, "name": t.get("name", ""),
                          "symbol": t.get("symbol", "")},
            "priceUsd": str(price),
            "liquidity": {"usd": liq},
            "volume": {"m5": bv5, "h24": bv24},
            "txns": {"m5": {"buys": int(s5.get("numBuys", 0) or 0),
                            "sells": int(s5.get("numSells", 0) or 0)},
                     "h24": {"buys": int(s24.get("numBuys", 0) or 0),
                             "sells": int(s24.get("numSells", 0) or 0)}},
            "priceChange": {"m5": float(s5.get("priceChange", 0) or 0),
                            "h1": float(s1.get("priceChange", 0) or 0),
                            "h6": float(s6.get("priceChange", 0) or 0),
                            "h24": float(s24.get("priceChange", 0) or 0)},
            "fdv": fdv,
            "marketCap": mcap,
            "pairCreatedAt": _parse_ms(created_ts_raw),
            "info": {"socials": socials, "websites": websites},
            "holderCount": int(t.get("holderCount", 0) or 0),
            "organicScore": t.get("organicScore"),
            "audit": t.get("audit", {}) or {},
            "_source": "jupiter",
            "_socials_unknown": False,
            "_vol1h": bv1,
        }
        import time as _t
        _JUP_TD_CACHE[mint] = (_t.time(), out)
        if len(_JUP_TD_CACHE) > 2500:
            _cut = _t.time() - 180.0
            for _k in list(_JUP_TD_CACHE.keys()):
                if _JUP_TD_CACHE[_k][0] < _cut:
                    _JUP_TD_CACHE.pop(_k, None)
        return out
    except Exception:
        return {}


async def _jup_token_data(mint: str) -> dict:
    """Токен-профиль через Jupiter Tokens API v2 (/tokens/v2/search?query=mint).
    Старый /tokens/v1/token/{mint} отключён (404) — используем v2 на
    api.jup.ag (с x-api-key) -> lite-api.jup.ag. Даёт соцсети, холдеры, аудит, возраст миграции и m5/h1 статистику.
    Нормализация — в форму DexScreener-пары. {} если токен не проиндексирован."""
    import time as _t
    _cached_j = _JUP_TD_CACHE.get(mint)
    if _cached_j and (_t.time() - _cached_j[0]) < 15.0:
        return dict(_cached_j[1])
    from http_client import fetch_json
    import config as _jc
    jup_key = getattr(_jc, "JUP_API_KEY", "") or ""
    jup_hdrs = {"x-api-key": jup_key} if jup_key else {}
    data = None
    for base, hdrs in (("https://api.jup.ag", jup_hdrs), ("https://lite-api.jup.ag", {})):
        status, body = await fetch_json(
            f"{base}/tokens/v2/search?query={mint}",
            headers=hdrs,
            timeout=8,
            retries=0,
        )
        if status == 200 and body:
            data = [body] if isinstance(body, dict) else body
            break
    if not data:
        return {}
    try:
        items = data if isinstance(data, list) else []
        exact = [x for x in items if isinstance(x, dict) and x.get("id") == mint]
        if not exact:
            return {}
        return normalize_jup_token(exact[0])
    except Exception:
        return {}


async def get_token_data(mint: str, max_cache_age: float = TD_TTL) -> dict:
    """DexScreener -> Jupiter Tokens API v2 -> GeckoTerminal fallback."""
    import time as _t
    now = _t.monotonic()
    _is_grad_md = False
    try:
        from birth_tracker import birth_tracker as _bt_md
        _is_grad_md = _bt_md.is_graduated(mint)
    except Exception:
        pass
    hit = _TD_CACHE.get(mint)
    if hit and (now - hit[0]) < max_cache_age:
        cached_d = hit[1] or {}
        if not (_is_grad_md and str(cached_d.get("dexId", "")).lower() in ("pump", "pumpfun")):
            return cached_d
    # 1. DexScreener первым: 300 зап/мин, самый надёжный, даёт m5/h1/h24 + соцсети.
    ds = await _ds_token_data(mint)
    if ds and float(ds.get("priceUsd", 0) or 0) > 0:
        # Если токен ТОЛЬКО ЧТО мигрировал (subscribeMigration), а DS ещё отдаёт старый кёрв "pump"
        # или отдаёт liq=0 в первые 5 минут после миграции — проваливаемся в Jupiter v2 / GeckoTerminal!
        _ds_liq = float((ds.get("liquidity") or {}).get("usd", 0) or 0)
        _ds_created = float(ds.get("pairCreatedAt", 0) or 0)
        _ds_young_no_liq = (_ds_liq <= 0.0) and (
            _is_grad_md or (_ds_created > 0 and (now * 1000.0 - _ds_created) <= 300_000.0)
        )
        if not (_is_grad_md and str(ds.get("dexId", "")).lower() in ("pump", "pumpfun")) and not _ds_young_no_liq:
            _TD_CACHE[mint] = (now, ds)
            if len(_TD_CACHE) > 2000:
                _TD_CACHE.clear()
            return ds
    # 2. Jupiter v2: мгновенно видит graduatedPool, ликвидность, соцсети, холдеры и m5/h1 статистику
    jup = await _jup_token_data(mint)
    if jup and not (_is_grad_md and str(jup.get("dexId", "")).lower() in ("pump", "pumpfun")):
        # Добираем GT только если у Jupiter пустое окно m5
        if float((jup.get("volume") or {}).get("m5", 0) or 0) <= 0:
            try:
                g = await _gt_get(f"/networks/solana/tokens/{mint}?include=top_pools")
                pools = [x for x in g.get("included", []) if x.get("type") == "pool" and _base_mint(x) == mint]
                if pools:
                    best = max(pools, key=lambda x: float(
                        x.get("attributes", {}).get("reserve_in_usd", 0) or 0))
                    pa = best.get("attributes", {})
                    vol = pa.get("volume_usd", {}) or {}
                    tx = pa.get("transactions", {}) or {}
                    chg = pa.get("price_change_percentage", {}) or {}
                    m5 = tx.get("m5", {}) or {}
                    jup["volume"]["m5"] = float(vol.get("m5", 0) or 0)
                    jup["txns"]["m5"] = {"buys": int(m5.get("buys", 0) or 0),
                                         "sells": int(m5.get("sells", 0) or 0)}
                    jup["priceChange"]["m5"] = float(chg.get("m5", 0) or 0)
                    gt_dex = _dex_id_from_pool(best)
                    if gt_dex not in ("unknown", "pump"):
                        jup["dexId"] = gt_dex
            except Exception:
                pass
        _TD_CACHE[mint] = (now, jup)
        if len(_TD_CACHE) > 2000:
            _TD_CACHE.clear()
        return jup
    # 3. GeckoTerminal целиком (только если это не сырая неиндексированная кривая pump.fun)
    import config as _md_cfg
    if mint.endswith("pump") and not getattr(_md_cfg, "ALLOW_UNGRADUATED_PUMPFUN", False):
        if not _is_grad_md:
            return ds or {}
    d = await _gt_get(f"/networks/solana/tokens/{mint}?include=top_pools")
    try:
        data = d.get("data", {})
        ta = data.get("attributes", {})
        pools = [x for x in d.get("included", []) if x.get("type") == "pool" and _base_mint(x) == mint]
        if ta and pools:
            name = ta.get("name", "") or ""
            symbol = ta.get("symbol", "") or ""
            best = max(pools, key=lambda x: float(
                x.get("attributes", {}).get("reserve_in_usd", 0) or 0))
            norm = _normalize_pool(mint, name, symbol, best, ta)
            if float(norm["priceUsd"] or 0) > 0:
                _TD_CACHE[mint] = (now, norm)
                if len(_TD_CACHE) > 2000:  # чистка старья
                    _TD_CACHE.clear()
                return norm
    except Exception:
        pass
    # Fallback: DexScreener уже пробовали первым — возвращаем что есть
    return ds or {}


_ALLOWED_SOL_QUOTES = {
    "so11111111111111111111111111111111111111112",
    "epjfwdd5aufqssqem2qn1xzybapc8g4weggkzwytdt1v",
    "es9vmfrzacermjfrf4h2fyd4kconky11mcce8benwnyb",
    "usd1ttgy1n17szxhfd31eyqso32682556356108733",
}
_ALLOWED_SOL_SYMBOLS = {
    "SOL", "WSOL", "USDC", "USDT", "USD1", "USDS", "PYUSD",
}


def _is_allowed_sol_quote(p: dict, mint: str, min_ps_liq: float = 4000.0) -> bool:
    """Разрешает ТОЛЬКО пары к SOL/WSOL и стейблкоинам (USDC/USDT/USD1).
    Исключает неликвидные/нероутируемые xStocks (TTWO/QQQx/SPCX — кейсы JETSKI -42.7%, FOILED -34.0%)."""
    q_addr = str((p.get("quoteToken") or {}).get("address") or "").strip().lower()
    q_sym = str((p.get("quoteToken") or {}).get("symbol") or "").strip().upper()
    if not q_addr and not q_sym:
        return True
    if q_addr and q_addr in _ALLOWED_SOL_QUOTES:
        return True
    if not q_addr and q_sym in _ALLOWED_SOL_SYMBOLS:
        return True
    return False


async def _ds_token_data(mint: str) -> dict:
    import config
    from http_client import fetch_json
    url = f"{config.DEXSCREENER_SEARCH}{mint}"
    status, data = await fetch_json(url, timeout=8, retries=2)
    if status == 200 and data:
        try:
            pairs = data.get("pairs") or []
            sol = []
            for p in pairs:
                if p.get("chainId") != "solana":
                    continue
                if ((p.get("baseToken") or {}).get("address") or "") != mint:
                    continue
                if not _is_allowed_sol_quote(p, mint):
                    continue
                sol.append(p)
            if sol:
                amm_sol = [p for p in sol if str(p.get("dexId") or "").lower() not in ("pump", "pumpfun", "meteoradbc")]
                real_amm = [p for p in amm_sol if float(((p.get("liquidity") or {}).get("usd", 0) or 0)) >= 500.0]
                if real_amm:
                    best = sorted(real_amm, key=lambda x: float(((x.get("liquidity") or {}).get("usd", 0) or 0)), reverse=True)[0]
                elif amm_sol:
                    best = sorted(amm_sol, key=lambda x: float(((x.get("liquidity") or {}).get("usd", 0) or 0)), reverse=True)[0]
                else:
                    best = sorted(sol, key=lambda x: float(((x.get("liquidity") or {}).get("usd", 0) or 0)) or (float(x.get("fdv", 0) or x.get("marketCap", 0) or 0) * 0.40), reverse=True)[0]
                best["_source"] = "dexscreener"
                best["_socials_unknown"] = False
                return best
        except Exception:
            pass
    return {}


async def _cex_prices(mints: list) -> dict:
    """Цены majors через CEX public API: бесплатно, без ключей, глобально.
    Coinbase Spot + Kraken Ticker. Покрывает только SOL (для мемов есть Jupiter/Llama/GT)."""
    from http_client import fetch_json
    out = {}
    SOL = "So11111111111111111111111111111111111111112"
    if SOL not in mints:
        return out
    status, data = await fetch_json("https://api.coinbase.com/v2/prices/SOL-USD/spot",
                                    timeout=8, retries=1)
    try:
        px = float((data.get("data", {}) or {}).get("amount", 0))
        if px > 0:
            out[SOL] = px
            return out
    except Exception:
        pass
    status, data = await fetch_json("https://api.kraken.com/0/public/Ticker?pair=SOLUSD",
                                    timeout=8, retries=1)
    try:
        last = (((data.get("result", {}) or {}).get("SOLUSD", {}) or {}).get("c", []) or [0])[0]
        if float(last) > 0:
            out[SOL] = float(last)
    except Exception:
        pass
    return out


async def _llama_prices(mints: list) -> dict:
    """Цены через DeFiLlama Coins API: бесплатно, без ключа.
    https://coins.llama.fi/prices/current/solana:addr1,solana:addr2"""
    from http_client import fetch_json
    out = {}
    ms = [m for m in dict.fromkeys(mints) if m]
    for i in range(0, len(ms), 30):
        chunk = ms[i:i + 30]
        ids = ",".join(f"solana:{m}" for m in chunk)
        status, data = await fetch_json(f"https://coins.llama.fi/prices/current/{ids}",
                                        timeout=10, retries=1)
        if status != 200:
            continue
        try:
            coins = data.get("coins", {})
            for m in chunk:
                px = (coins.get(f"solana:{m}", {}) or {}).get("price", 0)
                if px and float(px) > 0:
                    out[m] = float(px)
        except Exception:
            pass
    return out


async def get_price(mint: str) -> float:
    prices = await get_bulk_prices([mint])
    return prices.get(mint, 0.0)


async def _jup_v3_bulk_prices(mints: list) -> dict:
    """Сверхбыстрые ончейн-цены через Jupiter Price API v3 (задержка ~150мс, уровень блока Solana).
    Один запрос на пачку до 50 токенов, своя квота (до 600 зап/мин)."""
    from http_client import fetch_json
    import config
    out = {}
    if not mints:
        return out
    key = (getattr(config, "JUP_API_KEY", "") or "").strip()
    headers = {"Accept": "application/json"}
    if key:
        headers["x-api-key"] = key
    _jup_hosts = (("https://api.jup.ag", True), ("https://lite-api.jup.ag", False)) if key else (("https://lite-api.jup.ag", False),)
    for i in range(0, len(mints), 50):
        chunk = [m for m in mints[i:i + 50] if m and not m.startswith("0x")]
        if not chunk:
            continue
        ids_q = ",".join(chunk)
        for j_host, use_key in _jup_hosts:
            url = f"{j_host}/price/v3?ids={ids_q}"
            req_hdrs = headers if use_key else {"Accept": "application/json"}
            try:
                status, data = await fetch_json(url, headers=req_hdrs, timeout=1.1, retries=0)
                if status == 200 and isinstance(data, dict):
                    for m in chunk:
                        entry = data.get(m)
                        if isinstance(entry, dict):
                            px = float(entry.get("usdPrice") or entry.get("price") or 0.0)
                            if px > 0.0:
                                out[m] = px
                    break
                if status not in (429, 401, 403):
                    break
            except Exception:
                continue
    return out


async def get_bulk_prices(mints: list, pair_hints: dict = None) -> dict:
    """Сверхбыстрые балк-цены для мемкоинов (задержка < 0.2с).
    Приоритет: Jupiter Price API v3 (живые ончейн котировки уровня блока Solana 400мс без CDN-кэша).
    Если токен не найден на Jupiter — мгновенный фоллбэк на DexScreener / GeckoTerminal."""
    out = {}
    if not mints:
        return out
    ms = [m for m in dict.fromkeys(mints) if m]

    # 1. Приоритет №1: Сверхбыстрые ончейн-цены Jupiter Price v3 (живой Solana блок, без 15-секундного CDN-кэша)
    try:
        jup_prices = await asyncio.wait_for(_jup_v3_bulk_prices(ms), timeout=1.2)
        if isinstance(jup_prices, dict) and jup_prices:
            out.update({k: v for k, v in jup_prices.items() if v and v > 0})
    except Exception:
        pass

    missing = [m for m in ms if m not in out or out[m] <= 0]
    if missing:
        # 2. Фоллбэк на DexScreener bulk latest/dex/tokens (с таймаутом 2.5с)
        try:
            ds_out = await asyncio.wait_for(_ds_bulk_prices(missing, pair_hints=pair_hints), timeout=2.5)
            if isinstance(ds_out, dict):
                out.update({k: v for k, v in ds_out.items() if v and v > 0})
        except Exception:
            pass

    missing = [m for m in ms if m not in out or out[m] <= 0]
    if missing:
        # 3. Добираем через GeckoTerminal
        try:
            from http_client import fetch_json
            url = f"https://api.geckoterminal.com/api/v2/simple/networks/solana/token_price/{','.join(missing[:30])}"
            status, data = await fetch_json(url, timeout=0.9, retries=0)
            if status == 200 and isinstance(data, dict):
                prices = data.get("data", {}).get("attributes", {}).get("token_prices", {})
                out.update({k: float(v) for k, v in prices.items() if float(v or 0) > 0})
        except Exception:
            pass

    # 4. Fallback: CEX для SOL
    missing = [m for m in ms if m not in out or out[m] <= 0]
    if missing:
        try:
            out.update(await _cex_prices(missing))
        except Exception:
            pass

    return out


async def _ds_bulk_prices(mints: list, pair_hints: dict = None) -> dict:
    """Балк-цены через DexScreener (работает и для Solana).
    Если передан pair_hints (mint -> pair_address), приоритет отдаётся ТОЧНОМУ пулу входа,
    чтобы переключение между побочными пулами не давало ложных -20% стопов.
    Исключает мертвые bonding curve пулы (pumpfun с liq < 500), предотвращая фантомные дампы."""
    from http_client import fetch_json
    out = {}
    _liq = {}
    _locked_exact = set()
    _hints = pair_hints or {}
    _mset = set(mints)
    for i in range(0, len(mints), 30):
        chunk = [m for m in mints[i:i + 30] if m]
        if not chunk:
            continue
        pairs = []
        try:
            status, data = await fetch_json(f"https://api.dexscreener.com/latest/dex/tokens/{','.join(chunk)}", timeout=2.0, retries=0)
            if status == 200 and isinstance(data, dict):
                pairs = data.get("pairs") or []
        except Exception:
            pairs = []
        for p in (pairs or []):
            if p.get("chainId") != "solana":
                continue
            addr = (p.get("baseToken") or {}).get("address", "")
            if not addr or addr not in _mset:
                continue
            if not _is_allowed_sol_quote(p, addr):
                continue
            px = float(p.get("priceUsd", 0) or 0)
            if px <= 0:
                continue
            liq = float((p.get("liquidity") or {}).get("usd", 0) or 0)
            dex_low = str(p.get("dexId", "") or "").lower()
            p_addr = str(p.get("pairAddress") or "").strip()
            target_pair = str(_hints.get(addr) or "").strip()

            # Игнорируем мертвые bonding curves (pumpfun с нулевой/отсутствующей ликвидностью),
            # из-за которых происходили фантомные дампы в -63% на токенах в профите!
            if dex_low in ("pump", "pumpfun") and liq < 500.0:
                continue

            # Приоритет №1: Точный пул входа позиции
            if target_pair and p_addr == target_pair:
                _locked_exact.add(addr)
                _liq[addr] = 1e12
                out[addr] = px
                try:
                    _TD_CACHE[addr] = (time.monotonic(), p)
                except Exception:
                    pass
                continue

            if addr in _locked_exact:
                continue

            # Приоритет №2: Пул с наибольшей реальной ликвидностью
            eff_liq = liq if liq > 0 else (float(p.get("fdv", 0) or p.get("marketCap", 0) or 0) * 0.40 if dex_low not in ("pump", "pumpfun") else 0.0)
            if eff_liq > _liq.get(addr, -1):
                _liq[addr] = eff_liq
                out[addr] = px
                try:
                    _TD_CACHE[addr] = (time.monotonic(), p)
                except Exception:
                    pass
    return out


_trending_cache = []
_trending_cache_time = 0
_growth_cache = []
_growth_cache_time = 0.0


async def get_growth_universe(min_reserve_usd: float = 500000.0, limit: int = 20) -> list:
    """Вселенная GROWTH: ядро вотчлиста + топ пулов Raydium по резерву.
    Адреса только живые из API (не хардкод) + фильтр стейблов. Кэш 5 мин."""
    global _growth_cache, _growth_cache_time
    if time.time() - _growth_cache_time < 300 and _growth_cache:
        return _growth_cache
    out, seen = [], set()

    def add(mint):
        if mint and mint not in seen and len(mint) > 30:
            seen.add(mint)
            out.append(mint)

    try:
        import config as _c
        for m in getattr(_c, "GROWTH_WATCHLIST", []):
            add(m)
    except Exception:
        pass
    # CoinGecko trending (без ключей): ротации рынка, которых нет ни в вотчлисте,
    # ни в топе Raydium. Кэш 30 мин, единицы запросов — квоту не трогает.
    try:
        import coingecko as _cg
        for m in await _cg.trending_solana_mints():
            add(m)
    except Exception:
        pass
    stables = {"EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
               "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}
    try:
        for page in (1, 2, 3, 4, 5):
            d = await _gt_get(f"/networks/solana/dexes/raydium/pools?page={page}")
            for item in (d.get("data") or []):
                try:
                    a = item.get("attributes", {})
                    if float(a.get("reserve_in_usd", 0) or 0) < min_reserve_usd:
                        continue
                    m = _base_mint(item)
                    if m in stables:
                        continue
                    add(m)
                    if len(out) >= limit:
                        _growth_cache = out
                        _growth_cache_time = time.time()
                        return out
                except Exception:
                    continue
    except Exception as e:
        print(f"growth universe err: {type(e).__name__} {e}")
    _growth_cache = out
    _growth_cache_time = time.time()
    return out


async def get_trending() -> list:
    """Замена boosts/profiles: компактная выборка живых пулов GT (6 страниц ~14с раз в 60с, без 429!).
    Параллельно прогревает _TD_CACHE нормализованными данными пулов, экономя одиночные запросы GT.
    Формат как у DexScreener boosts: [{'tokenAddress','chainId'}]."""
    global _trending_cache, _trending_cache_time

    # Кэшируем результаты на 50 секунд (снижает нагрузку на GeckoTerminal API и исключает 429)
    if time.time() - _trending_cache_time < 15 and _trending_cache:
        return _trending_cache

    out, seen = [], set()
    now_mono = time.monotonic()

    import config as _tr_cfg
    _allow_raw_curve = bool(getattr(_tr_cfg, "ALLOW_UNGRADUATED_PUMPFUN", False))

    def add(item):
        if not item or not isinstance(item, dict):
            return
        mint = _base_mint(item)
        if not mint or mint in seen or mint.startswith("0x") or len(mint) < 32 or len(mint) > 44:
            return
        _qt_id = str((((item.get("relationships") or {}).get("quote_token") or {}).get("data") or {}).get("id") or "").replace("solana_", "").lower()
        if _qt_id and _qt_id not in _ALLOWED_SOL_QUOTES and _qt_id != "usd1ttgy1n17szxhfd31eyqso32682556356108733":
            return
        dex_id = _dex_id_from_pool(item)
        if not _allow_raw_curve and dex_id in ("pump", "pumpfun", "meteoradbc"):
            return
        attrs = item.get("attributes") or {}
        try:
            liq_usd = float(attrs.get("reserve_in_usd", 0) or 0)
        except Exception:
            liq_usd = 0.0
        if dex_id not in ("pump", "pumpfun") and liq_usd < 2500.0:
            return
        vol = attrs.get("volume_usd") or {}
        try:
            vol5_usd = float(vol.get("m5", 0) or 0)
        except Exception:
            vol5_usd = 0.0
        # Защита от старых (>240м), сверх-тяжёлых (> $850k FDV) и стоячих пулов прямо на входе дискавери:
        _fdv_pool = float(attrs.get("fdv_usd") or attrs.get("market_cap_usd") or 0)
        _max_fdv_gt = float(getattr(_tr_cfg, "JEV_MAX_MCAP_USD", 850000.0))
        if _fdv_pool > _max_fdv_gt:
            return
        created_ms = _parse_ms(attrs.get("pool_created_at"))
        if created_ms:
            age_h = (time.time() * 1000 - created_ms) / 3600000.0
            tx = attrs.get("transactions") or {}
            m5_tx = int((tx.get("m5") or {}).get("buys", 0) or 0) + int((tx.get("m5") or {}).get("sells", 0) or 0)
            _max_age_gt_h = float(getattr(_tr_cfg, "SOLANA_MAX_AGE_MIN", 240.0)) / 60.0
            if age_h > _max_age_gt_h:
                return
            if age_h > 0.75 and vol5_usd < 2000.0:
                return
        seen.add(mint)
        out.append({"tokenAddress": mint, "chainId": "solana"})
        # Прогрев _TD_CACHE прямо из ответа списка пулов (0 лишних сетевых вызовов!)
        hit = _TD_CACHE.get(mint)
        if not hit or (now_mono - hit[0]) >= TD_TTL:
            try:
                raw_name = str(attrs.get("name") or "")
                base_sym = raw_name.split("/")[0].strip() if "/" in raw_name else (raw_name.strip() or mint[:6])
                norm = _normalize_pool(mint, base_sym, base_sym, item, {"fdv_usd": attrs.get("fdv_usd"), "market_cap_usd": attrs.get("market_cap_usd")})
                if float(norm.get("priceUsd", 0) or 0) > 0:
                    _TD_CACHE[mint] = (now_mono, norm)
            except Exception:
                pass

    # 4 страницы живых 5м трендов GT (~9с с пейсером 3.0с — гарантированно без HTTP 429!):
    # Охватывает ТОП-80 самых горячих 5м пулов Solana (PumpSwap / Meteora / Raydium).
    gt_pages = (
        "/networks/solana/new_pools?page=1",
        "/networks/solana/new_pools?page=2",
        "/networks/solana/trending_pools?duration=5m&page=1",
    )
    for path in gt_pages:
        d = await _gt_get(path, retries=0, timeout=6)
        for item in (d.get("data") or []):
            add(item)

    # Не кэшируем пустой get_trending на 60с (иначе бот слепнет при сбое GT)
    if out:
        _trending_cache = out
        _trending_cache_time = time.time()
    else:
        _trending_cache = []
        _trending_cache_time = time.time() - 55  # даём повторить через 5с
    return out


async def search_symbol(symbol: str) -> list:
    """Поиск пулов по тикеру для clone-check. Возвращает список
    {mint, symbol, reserve_usd, created_ms}."""
    if not symbol or len(symbol) <= 2:
        return []
    d = await _gt_get(f"/search/pools?query={symbol}&network=solana")
    res = []
    for item in (d.get("data") or []):
        try:
            a = item.get("attributes", {})
            name = str(a.get("name", ""))
            base_sym = name.split("/")[0].strip().upper()
            if base_sym != symbol.upper():
                continue
            res.append({
                "mint": _base_mint(item),
                "symbol": base_sym,
                "reserve_usd": float(a.get("reserve_in_usd", 0) or 0),
                "created_ms": _parse_ms(a.get("pool_created_at")),
            })
        except Exception:
            continue
    return res


_gainers_cache: dict = {}


async def fetch_dexscreener_gainers(timeframe: str = "5m", network: str = "all") -> list:
    """Возвращает список топ-гейнеров (New Pairs / Gainers 5M или 1H).
    Использует защищённый лимитер _gt_get с кэшем на 25 секунд.
    """
    global _gainers_cache
    tf = "5m" if str(timeframe).lower().startswith("5") else "1h"
    net = (network or "all").lower()
    cache_key = f"{tf}:{net}"
    now = time.time()
    hit = _gainers_cache.get(cache_key)
    if hit and (now - hit[0]) < 60.0:
        return hit[1]

    pools = []
    gt_net = {"solana": "solana", "base": "base", "robinhood": "robinhood", "bsc": "bsc", "ethereum": "eth"}.get(net, net)
    for page in (1, 2):
        path = f"/networks/trending_pools?duration={tf}&page={page}" if net == "all" else f"/networks/{gt_net}/trending_pools?duration={tf}&page={page}"
        data = await _gt_get(path, retries=0)
        if data and isinstance(data, dict):
            pools.extend(data.get("data") or [])
    if not pools:
        return hit[1] if hit else []
    out = []
    rank = 1
    for p in pools:
        try:
            attr = p.get("attributes") or {}
            net_id = p.get("relationships", {}).get("network", {}).get("data", {}).get("id", "") or (net if net != "all" else "solana")
            base_id = p.get("relationships", {}).get("base_token", {}).get("data", {}).get("id", "")
            mint = base_id.split("_", 1)[1] if "_" in base_id else base_id
            pair_addr = p.get("attributes", {}).get("address") or ""
            name = str(attr.get("name") or "Unknown")
            chg = attr.get("price_change_percentage") or {}
            m5 = float(chg.get("m5", 0) or 0)
            h1 = float(chg.get("h1", 0) or 0)
            liq = float(attr.get("reserve_in_usd", 0) or 0)
            vol = float(attr.get("volume_usd", {}).get("h24", 0) or 0)
            price = float(attr.get("base_token_price_usd", 0) or 0)
            tx = attr.get("transactions", {}).get("m5", {}) or {}
            buys = int(tx.get("buys", 0) or 0)
            sells = int(tx.get("sells", 0) or 0)

            created_str = attr.get("pool_created_at") or ""
            created_ms = _parse_ms(created_str)
            age_m = (now * 1000 - created_ms) / 60000.0 if created_ms else 0
            if age_m < 60:
                age_str = f"{int(age_m)}m"
            else:
                age_str = f"{int(age_m // 60)}h {int(age_m % 60)}m"

            out.append({
                "rank": rank,
                "network": net_id,
                "name": name,
                "mint": mint,
                "pair_address": pair_addr,
                "price": price,
                "age_str": age_str,
                "age_m": age_m,
                "m5": m5,
                "h1": h1,
                "buys": buys,
                "sells": sells,
                "volume": vol,
                "liquidity": liq,
            })
            rank += 1
        except Exception:
            continue

    _gainers_cache[cache_key] = (now, out)
    return out


PUMP_PROGRAM_PUBKEY_STR = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPSWAP_PROGRAM_STR = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
_WSOL_MINT = "So11111111111111111111111111111111111111112"
_STABLE_QUOTES = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "USD1ttGY1N17NEEHLmELoaybftRBUSErhqYiQzvEmuB",   # USD1
    "USDSwr9ApdHk5bvJKMjzff41FfuX8bSxdKcR81vTwcA",   # USDS
    "2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo", # PYUSD
}

_PUMPSWAP_LAYOUT_CACHE = {}   # pair_address -> (base_side_idx, base_vault, quote_vault, quote_mint)
_ORDER_FLOW_CACHE = {}        # pair_address -> (monotonic_ts, result_dict)


def _solana_rpc_endpoints() -> list:
    import config
    raw = []
    helius = (getattr(config, "HELIUS_RPC_URL", "") or "").strip()
    if helius and ("api-key" not in helius or getattr(config, "HELIUS_API_KEY", "")):
        raw.append(helius)
    raw.extend([
        "https://api.mainnet-beta.solana.com",
        "https://solana-rpc.publicnode.com",
    ])
    return list(dict.fromkeys(u for u in raw if u))


async def _solana_rpc_json(method: str, params: list, timeout: float = 2.0):
    from http_client import fetch_json
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    for rpc in _solana_rpc_endpoints():
        try:
            status, resp = await fetch_json(rpc, method="POST", json_data=payload, timeout=timeout, retries=0)
            if status == 200 and isinstance(resp, dict) and "result" in resp and not resp.get("error"):
                return resp.get("result")
        except Exception:
            continue
    return None


async def get_live_pump_curve_state(mint: str) -> dict:
    """Ончейн проверка резервов bonding curve Pump.fun с задержкой < 250мс.
    Возвращает dict с live ценой, реальным SOL и статусом complete.
    Спасает от покупки пустых/слитых в 0 кёрвов ($PICKLES, GTAMICHI, AabL4, bob)."""
    if not mint or not str(mint).endswith("pump"):
        return None
    try:
        from solders.pubkey import Pubkey
        import struct, base64
        from sol_price import get_sol_price_sync

        pump_prog = Pubkey.from_string(PUMP_PROGRAM_PUBKEY_STR)
        mint_pk = Pubkey.from_string(str(mint).strip())
        bonding_curve_pk, _ = Pubkey.find_program_address([b"bonding-curve", bytes(mint_pk)], pump_prog)
        curve_addr = str(bonding_curve_pk)

        res = await _solana_rpc_json("getAccountInfo", [curve_addr, {"encoding": "base64"}], timeout=2.0)
        val = res.get("value") if isinstance(res, dict) else None
        if val is None:
            return None
        data_list = val.get("data", [])
        if not data_list:
            return None
        raw = base64.b64decode(data_list[0])
        if len(raw) < 49:
            return None
        v_token, v_sol, r_token, r_sol, supply, complete = struct.unpack('<QQQQQ?', raw[8:49])
        sol_p = float(get_sol_price_sync() or 75.0)
        if sol_p <= 0:
            sol_p = 75.0

        real_sol = r_sol / 1e9
        v_sol_flt = v_sol / 1e9
        v_tok_flt = v_token / 1e6
        price_sol = (v_sol_flt / v_tok_flt) if v_tok_flt > 0 else 0.0
        price_usd = price_sol * sol_p
        fdv_usd = price_usd * 1_000_000_000.0
        prog_pct = min(100.0, (real_sol / 85.0) * 100.0)

        return {
            "mint": mint,
            "curve_address": curve_addr,
            "complete": bool(complete),
            "real_sol": real_sol,
            "v_sol": v_sol_flt,
            "v_token": v_tok_flt,
            "price_sol": price_sol,
            "price_usd": price_usd,
            "fdv_usd": fdv_usd,
            "progress_pct": prog_pct,
        }
    except Exception:
        pass
    return None


async def get_live_pumpswap_pool_state(mint: str, pair_address: str = "") -> dict:
    """Читает реальные ончейн-резервы AMM-пула PumpSwap (baseVault / quoteVault) за 1-2 RPC (~150мс).
    Возвращает живую цену уровня блока Solana и реальный остаток SOL/USD в пуле (защита от 0-ликвы и VICECITY -95%)."""
    pair_address = str(pair_address or "").strip()
    if not mint or not pair_address or len(pair_address) < 32:
        return None
    try:
        from solders.pubkey import Pubkey
        import base64
        from sol_price import get_sol_price_sync

        layout = _PUMPSWAP_LAYOUT_CACHE.get(pair_address)
        if layout is None:
            res = await _solana_rpc_json("getAccountInfo", [pair_address, {"encoding": "base64"}], timeout=2.0)
            val = res.get("value") if isinstance(res, dict) else None
            if not isinstance(val, dict) or str(val.get("owner") or "") != PUMPSWAP_PROGRAM_STR:
                return None
            raw_field = val.get("data")
            raw_b64 = raw_field[0] if isinstance(raw_field, list) else raw_field
            raw = base64.b64decode(raw_b64)
            if len(raw) < 203:
                return None
            t0 = str(Pubkey.from_bytes(raw[43:75]))
            t1 = str(Pubkey.from_bytes(raw[75:107]))
            v0 = str(Pubkey.from_bytes(raw[139:171]))
            v1 = str(Pubkey.from_bytes(raw[171:203]))
            if t0 == mint and (t1 == _WSOL_MINT or t1 in _STABLE_QUOTES):
                layout = (v0, v1, t1)
            elif t1 == mint and (t0 == _WSOL_MINT or t0 in _STABLE_QUOTES):
                layout = (v1, v0, t0)
            else:
                return None
            _PUMPSWAP_LAYOUT_CACHE[pair_address] = layout
            if len(_PUMPSWAP_LAYOUT_CACHE) > 1000:
                _PUMPSWAP_LAYOUT_CACHE.clear()

        base_vault, quote_vault, quote_mint = layout
        mres = await _solana_rpc_json(
            "getMultipleAccounts",
            [[base_vault, quote_vault], {"encoding": "jsonParsed"}],
            timeout=2.0,
        )
        vals = (mres or {}).get("value") if isinstance(mres, dict) else None
        if not isinstance(vals, list) or len(vals) < 2 or not vals[0] or not vals[1]:
            return None

        base_ui = float((((vals[0].get("data") or {}).get("parsed") or {}).get("info") or {}).get("tokenAmount", {}).get("uiAmount") or 0.0)
        quote_ui = float((((vals[1].get("data") or {}).get("parsed") or {}).get("info") or {}).get("tokenAmount", {}).get("uiAmount") or 0.0)
        if base_ui <= 0 or quote_ui <= 0:
            return {"pair_address": pair_address, "liq_usd": 0.0, "quote_amount": quote_ui, "price_usd": 0.0}

        sol_p = float(get_sol_price_sync() or 75.0)
        if sol_p <= 0:
            sol_p = 75.0
        is_wsol = (quote_mint == _WSOL_MINT)
        quote_usd = (quote_ui * sol_p) if is_wsol else quote_ui
        price_usd = quote_usd / base_ui
        price_sol = (quote_ui / base_ui) if is_wsol else (price_usd / sol_p)
        return {
            "mint": mint,
            "pair_address": pair_address,
            "quote_mint": quote_mint,
            "is_wsol": is_wsol,
            "quote_amount": quote_ui,
            "base_amount": base_ui,
            "quote_usd": quote_usd,
            "liq_usd": quote_usd * 2.0,
            "price_sol": price_sol,
            "price_usd": price_usd,
        }
    except Exception:
        return None


async def check_live_order_flow(
    mint: str,
    pair_address: str = "",
    smart_wallets: set = None,
    ref_price_usd: float = 0.0,
) -> dict:
    """Анализ реальной ленты последних сделок пула (Order Flow, GeckoTerminal /trades, задержка <1с).
    Выявляет:
      1) Активный слив / дамп прямо в момент входа (красная лента, падение >=22% от 3м-пика)
      2) MEV/sandwich и wash-ботов (один кошелёк покупает и тут же продаёт как FISH FDi3BzLs)
      3) Участие проверенных Smart Money кошельков (smart_wallets_db)."""
    res = {
        "available": False,
        "block": False,
        "reason": "",
        "smart_buyers": [],
        "live_tape_px": 0.0,
        "wash_ratio": 0.0,
    }
    clean_pair = str(pair_address or "").replace("solana_", "").strip()
    if not clean_pair or len(clean_pair) < 32 or clean_pair.startswith("0x"):
        return res

    now_m = time.monotonic()
    hit = _ORDER_FLOW_CACHE.get(clean_pair)
    trades = None
    if hit and (now_m - hit[0]) < 4.0:
        trades = hit[1]
    else:
        try:
            gt_resp = await _gt_get(f"/networks/solana/pools/{clean_pair}/trades", retries=0, timeout=4)
            trades = (gt_resp or {}).get("data") if isinstance(gt_resp, dict) else None
            if isinstance(trades, list):
                _ORDER_FLOW_CACHE[clean_pair] = (now_m, trades)
                if len(_ORDER_FLOW_CACHE) > 500:
                    _ORDER_FLOW_CACHE.clear()
        except Exception:
            trades = None

    if not isinstance(trades, list) or len(trades) < 4:
        return res

    try:
        parsed = []
        for tr in trades[:120]:
            a = tr.get("attributes") or {}
            kind = str(a.get("kind") or "").lower()
            if kind not in ("buy", "sell"):
                continue
            ts_ms = _parse_ms(a.get("block_timestamp"))
            ts = ts_ms / 1000.0 if ts_ms > 0 else 0.0
            vol_usd = float(a.get("volume_in_usd") or 0.0)
            px = float(a.get("price_to_in_usd") or 0.0) if kind == "buy" else float(a.get("price_from_in_usd") or 0.0)
            wallet = str(a.get("tx_from_address") or "").strip()
            parsed.append({"ts": ts, "kind": kind, "usd": vol_usd, "px": px, "wallet": wallet})

        if len(parsed) < 4:
            return res

        latest_ts = max((t["ts"] for t in parsed if t["ts"] > 0), default=0.0) or time.time()
        last_trade_age_s = max(0.0, time.time() - latest_ts)

        # Свежие окна 30с и 60с от последнего трейда в ленте
        w60 = [t for t in parsed if t["ts"] <= 0 or (latest_ts - t["ts"]) <= 60.0]
        w30 = [t for t in parsed if t["ts"] <= 0 or (latest_ts - t["ts"]) <= 30.0]
        w180 = [t for t in parsed if t["ts"] <= 0 or (latest_ts - t["ts"]) <= 180.0]

        buy_60 = sum(t["usd"] for t in w60 if t["kind"] == "buy")
        sell_60 = sum(t["usd"] for t in w60 if t["kind"] == "sell")
        sells_60_n = sum(1 for t in w60 if t["kind"] == "sell")
        buy_30 = sum(t["usd"] for t in w30 if t["kind"] == "buy")
        sell_30 = sum(t["usd"] for t in w30 if t["kind"] == "sell")

        last5 = parsed[:5]
        last5_sells = sum(1 for t in last5 if t["kind"] == "sell")
        last3_all_sell_usd = sum(t["usd"] for t in parsed[:3]) if all(t["kind"] == "sell" for t in parsed[:3]) else 0.0

        # Живая цена по медиане последних 3 валидных сделок
        recent_pxs = [t["px"] for t in parsed[:5] if t["px"] > 0]
        live_px = 0.0
        if recent_pxs:
            s_pxs = sorted(recent_pxs[:3])
            live_px = s_pxs[len(s_pxs) // 2]
            if ref_price_usd > 0 and (live_px > ref_price_usd * 50.0 or live_px < ref_price_usd / 50.0):
                live_px = 0.0  # несовпадающая сторона пары на GT -> не подменяем цену

        hi_3m = 0.0
        pxs_3m = [t["px"] for t in w180 if t["px"] > 0 and (live_px <= 0 or live_px / 8.0 <= t["px"] <= live_px * 8.0)]
        if pxs_3m:
            hi_3m = max(pxs_3m)

        # Детект сэндвич / wash-ботов и метрики MELT (уникальность покупателей + доля перевёртышей)
        by_wallet = {}
        buy_wallets = set()
        sell_wallets = set()
        total_buys_n = 0
        buy_usd_total = 0.0
        sell_usd_total = 0.0
        total_usd_sample = sum(t["usd"] for t in parsed)
        for t in parsed:
            w = t["wallet"]
            if t["kind"] == "buy":
                total_buys_n += 1
                buy_usd_total += t["usd"]
                if w:
                    buy_wallets.add(w)
            elif t["kind"] == "sell":
                sell_usd_total += t["usd"]
                if w:
                    sell_wallets.add(w)
            if not w:
                continue
            by_wallet.setdefault(w, []).append(t)

        wash_usd = 0.0
        max_single_buyer_usd = 0.0
        for w, txs in by_wallet.items():
            buys_w = [x for x in txs if x["kind"] == "buy"]
            sells_w = [x for x in txs if x["kind"] == "sell"]
            b_w_usd = sum(x["usd"] for x in buys_w)
            if b_w_usd > max_single_buyer_usd:
                max_single_buyer_usd = b_w_usd
            if buys_w and sells_w:
                # Проверяем парный разворот <= 30с
                has_fast_flip = any(abs(b["ts"] - s["ts"]) <= 30.0 for b in buys_w for s in sells_w)
                if has_fast_flip:
                    wash_usd += sum(x["usd"] for x in txs)
        wash_ratio = (wash_usd / total_usd_sample) if total_usd_sample > 0 else 0.0
        unique_buyers_n = len(buy_wallets)
        unique_buyers_ratio = (unique_buyers_n / total_buys_n) if total_buys_n > 0 else 1.0
        active_traders = buy_wallets | sell_wallets
        both_sides_overlap = (len(buy_wallets & sell_wallets) / len(active_traders)) if active_traders else 0.0
        top_buyer_usd_share = (max_single_buyer_usd / buy_usd_total) if buy_usd_total > 0 else 0.0

        # Смарт-мани в последних сделках (нетто-покупатели из smart_wallets)
        smart_buyers = []
        if smart_wallets:
            for w in smart_wallets:
                txs = by_wallet.get(w)
                if not txs:
                    continue
                b_sum = sum(x["usd"] for x in txs if x["kind"] == "buy")
                s_sum = sum(x["usd"] for x in txs if x["kind"] == "sell")
                if b_sum >= 40.0 and b_sum > s_sum * 1.1:
                    smart_buyers.append(w)

        block = False
        reason = ""
        if last_trade_age_s > 240.0 and len(w60) == 0:
            block = True
            reason = f"tape-dead-pool (last trade {last_trade_age_s:.0f}s ago)"
        elif last3_all_sell_usd >= 180.0 and sell_30 > buy_30 * 1.35 and last5_sells >= 4:
            block = True
            reason = f"tape-active-dump (4+/5 sells, 30s sell ${sell_30:.0f} > buy ${buy_30:.0f})"
        elif sells_60_n >= 4 and sell_60 >= 300.0 and sell_60 > buy_60 * 1.65:
            block = True
            reason = f"tape-sell-pressure (60s sell ${sell_60:.0f} vs buy ${buy_60:.0f})"
        elif live_px > 0 and hi_3m > 0 and live_px < hi_3m * 0.78 and sell_60 >= buy_60 * 0.90:
            drop_p = (1.0 - live_px / hi_3m) * 100.0
            block = True
            reason = f"tape-waterfall-drop (-{drop_p:.1f}% from 3m tape high, sell ${sell_60:.0f}/buy ${buy_60:.0f})"
        elif len(parsed) >= 12 and (wash_ratio >= 0.60 or (wash_ratio >= 0.50 and buy_60 <= sell_60 * 1.15)):
            block = True
            reason = f"tape-wash-mev ({wash_ratio*100:.0f}% same-block roundtrip MEV/wash volume)"
        elif len(parsed) >= 16 and total_buys_n >= 8 and unique_buyers_n <= 4 and unique_buyers_ratio < 0.35:
            block = True
            reason = f"tape-fake-buyers-wash ({unique_buyers_n} уникальных покупателей на {total_buys_n} покупок)"
        elif len(active_traders) >= 8 and both_sides_overlap >= 0.55 and wash_ratio >= 0.35 and buy_60 <= sell_60 * 1.15:
            block = True
            reason = f"tape-overlap-wash-bots ({both_sides_overlap*100:.0f}% трейдеров в обе стороны, wash {wash_ratio*100:.0f}%)"
        elif buy_usd_total >= 400.0 and total_buys_n >= 8 and top_buyer_usd_share >= 0.65 and not smart_buyers and (wash_ratio >= 0.30 or both_sides_overlap >= 0.35 or top_buyer_usd_share >= 0.88):
            block = True
            reason = f"tape-single-buyer-spoof ({top_buyer_usd_share*100:.0f}% спроса от 1 кошелька)"

        res.update({
            "available": True,
            "block": block,
            "reason": reason,
            "smart_buyers": smart_buyers,
            "live_tape_px": live_px,
            "recent_high_3m": hi_3m,
            "buy_usd_60s": buy_60,
            "sell_usd_60s": sell_60,
            "buy_usd_total": round(buy_usd_total, 2),
            "sell_usd_total": round(sell_usd_total, 2),
            "unique_buyers_n": unique_buyers_n,
            "unique_buyers_ratio": round(unique_buyers_ratio, 3),
            "both_sides_overlap": round(both_sides_overlap, 3),
            "top_buyer_usd_share": round(top_buyer_usd_share, 3),
            "wash_ratio": round(wash_ratio, 3),
            "last_trade_age_s": round(last_trade_age_s, 1),
        })
        return res
    except Exception:
        return res


async def verify_entry_onchain_and_orderflow(
    mint: str,
    symbol: str,
    pair_data: dict,
    scan_price: float,
) -> tuple:
    """Единый про-бот Pre-Flight (уровень блока Solana + живая лента сделок):
      1. Репутация создателя (bad_creators / серийный запуск)
      2. Резервы bonding curve Pump.fun (real_sol >= 1.8 SOL) или ончейн-резервы PumpSwap (quote >= 5 SOL)
      3. Живой Order Flow по последним трейдам пула (<1с задержка): отсев активного слива и MEV/wash-ботов
      4. Детект Smart Money покупок (Tier-1/2/VIP)
    Возвращает (ok: bool, live_price: float, reason: str, meta: dict)."""
    meta = {"smart_buyers": [], "wash_ratio": 0.0, "onchain_source": ""}
    live_price = float(scan_price or 0.0)
    pair_data = pair_data if isinstance(pair_data, dict) else {}
    dex_id = str(pair_data.get("dexId") or "").lower()
    pair_addr = str(pair_data.get("pairAddress") or "").strip()

    try:
        from birth_tracker import birth_tracker as _bt_pf
        _cr_ok, _cr_why = _bt_pf.check_creator_risk(mint)
        if not _cr_ok:
            return False, live_price, f"creator-blacklist ({_cr_why})", meta
    except Exception:
        pass

    smart_set = set()
    try:
        from smart_money_hunter import smart_money_hunter as _smh_pf
        smart_set = _smh_pf.get_trusted_wallets(chain="solana", max_tier=2)
    except Exception:
        pass

    is_raw_curve = dex_id in ("pump", "pumpfun") or (
        mint.lower().endswith("pump") and dex_id not in ("pumpswap", "raydium", "meteora", "orca")
    )

    tasks = {}
    if is_raw_curve or mint.lower().endswith("pump"):
        tasks["curve"] = asyncio.create_task(get_live_pump_curve_state(mint))
    if dex_id == "pumpswap" and pair_addr:
        tasks["pumpswap"] = asyncio.create_task(get_live_pumpswap_pool_state(mint, pair_addr))
    if dex_id not in ("pump", "pumpfun"):
        from jupiter import JupiterAPI
        tasks["jup_px"] = asyncio.create_task(JupiterAPI.get_price(mint))
        if pair_addr:
            tasks["flow"] = asyncio.create_task(
                check_live_order_flow(mint, pair_addr, smart_wallets=smart_set, ref_price_usd=live_price)
            )

    results = {}
    if tasks:
        vals = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for k, v in zip(tasks.keys(), vals):
            results[k] = None if isinstance(v, Exception) else v

    # 1. Ончейн проверка сырой кривой Pump.fun
    c_st = results.get("curve")
    if isinstance(c_st, dict):
        if c_st.get("complete"):
            if dex_id in ("pump", "pumpfun"):
                return False, live_price, "curve-migrated-stale-pair (complete=True)", meta
        else:
            r_sol = float(c_st.get("real_sol", 0.0) or 0.0)
            c_px = float(c_st.get("price_usd", 0.0) or 0.0)
            if r_sol < 2.2:
                return False, live_price, f"curve-empty-rug (real_sol={r_sol:.3f} < 2.2)", meta
            if live_price > 0 and c_px > 0:
                if c_px < live_price * 0.90:
                    return False, c_px, f"curve-live-dump (-{(1.0 - c_px/live_price)*100:.1f}%)", meta
                if c_px > live_price * 1.35:
                    return False, c_px, f"curve-spike (+{(c_px/live_price - 1.0)*100:.1f}%)", meta
                live_price = c_px
                meta["onchain_source"] = "pump_curve"

    # 2. Ончейн проверка резервов PumpSwap AMM
    ps_st = results.get("pumpswap")
    if isinstance(ps_st, dict):
        ps_liq = float(ps_st.get("liq_usd", 0.0) or 0.0)
        ps_q = float(ps_st.get("quote_amount", 0.0) or 0.0)
        ps_px = float(ps_st.get("price_usd", 0.0) or 0.0)
        if ps_st.get("is_wsol") and ps_q < 5.0:
            return False, live_price, f"pumpswap-drained-onchain ({ps_q:.2f} SOL < 5 SOL)", meta
        if ps_liq < 1200.0:
            return False, live_price, f"pumpswap-low-liq-onchain (${ps_liq:.0f} < $1200)", meta
        if live_price > 0 and ps_px > 0:
            if ps_px < live_price * 0.91:
                return False, ps_px, f"pumpswap-live-dump (-{(1.0 - ps_px/live_price)*100:.1f}%)", meta
            if ps_px > live_price * 1.30:
                return False, ps_px, f"pumpswap-spike (+{(ps_px/live_price - 1.0)*100:.1f}%)", meta
            live_price = ps_px
            meta["onchain_source"] = "pumpswap_vault"

    # 3. Jupiter fallback (для Raydium/Orca/Meteora или если PumpSwap RPC не ответил)
    flow = results.get("flow")
    _flow_clean_buy = bool(
        isinstance(flow, dict)
        and flow.get("available")
        and not flow.get("block")
        and float(flow.get("buy_usd_60s", 0.0) or 0.0) > float(flow.get("sell_usd_60s", 0.0) or 0.0)
    )
    if meta["onchain_source"] == "":
        jup_px = float(results.get("jup_px") or 0.0)
        if jup_px > 0 and live_price > 0:
            if jup_px < live_price * 0.925:
                return False, jup_px, f"jup-live-dump (-{(1.0 - jup_px/live_price)*100:.1f}%)", meta
            if jup_px > live_price * 1.45 and not _flow_clean_buy:
                return False, jup_px, f"jup-spike (+{(jup_px/live_price - 1.0)*100:.1f}%)", meta
            if jup_px <= live_price * 1.45:
                live_price = jup_px
                meta["onchain_source"] = "jupiter_v3"

    # 4. Живая лента сделок (Order Flow / Sandwich-Wash / Dump / MELT Metrics)
    if isinstance(flow, dict) and flow.get("available"):
        meta["smart_buyers"] = list(flow.get("smart_buyers") or [])
        meta["wash_ratio"] = float(flow.get("wash_ratio", 0.0) or 0.0)
        meta["unique_buyers_ratio"] = float(flow.get("unique_buyers_ratio", 0.0) or 0.0)
        meta["both_sides_overlap"] = float(flow.get("both_sides_overlap", 0.0) or 0.0)
        if flow.get("block"):
            return False, live_price, str(flow.get("reason") or "order-flow-block"), meta
        if meta["smart_buyers"]:
            print(f"🐋 [SMART-MONEY INFLOW] {symbol} ({mint[:8]}): {len(meta['smart_buyers'])} активных топ-трейдеров в ленте!")

    return True, live_price, "OK", meta



