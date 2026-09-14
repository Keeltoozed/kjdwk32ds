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
import time
from datetime import datetime, timezone

BASE = "https://api.geckoterminal.com/api/v2"
HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}

_lock = asyncio.Lock()
_last_call = 0.0
MIN_INTERVAL = 2.0  # не чаще ~30/мин на весь процесс

# Кэш данных токенов: сканер и FOMO запрашивают одни и те же минты по кругу.
# Без кэша свежие токены съедают весь лимит 30/мин и всё падает с 429.
_TD_CACHE = {}  # mint -> (timestamp_monotonic, data)
TD_TTL = 90.0  # секунд свежие данные считаются годными


async def _gt_get(path: str):
    """GET к GeckoTerminal с общим rate-limiter'ом. Возвращает dict или {}."""
    global _last_call
    from http_client import get_session
    session = await get_session()
    async with _lock:
        wait = MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with session.get(BASE + path, headers=HEADERS, timeout=10) as r:
                _last_call = time.monotonic()
                if r.status == 200:
                    return await r.json()
                return {}
        except Exception:
            _last_call = time.monotonic()
            return {}


def _parse_ms(ts) -> int:
    try:
        if not ts:
            return 0
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


def _dex_id_from_pool(item: dict) -> str:
    try:
        dex = item.get("relationships", {}).get("dex", {}).get("data", {}).get("id", "")
        dex = str(dex).lower()
        if "pump" in dex:
            return "pump"
        if "raydium" in dex:
            return "raydium"
        return dex or "unknown"
    except Exception:
        return "unknown"


def _base_mint(item: dict) -> str:
    try:
        bid = item.get("relationships", {}).get("base_token", {}).get("data", {}).get("id", "")
        return bid.split("_", 1)[1] if "_" in bid else ""
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


async def get_token_data(mint: str) -> dict:
    """Главная замена DexScreener fetch_token_data. GT -> DS fallback."""
    import time as _t
    now = _t.monotonic()
    hit = _TD_CACHE.get(mint)
    if hit and (now - hit[0]) < TD_TTL:
        return hit[1]
    d = await _gt_get(f"/networks/solana/tokens/{mint}?include=top_pools")
    try:
        data = d.get("data", {})
        ta = data.get("attributes", {})
        pools = [x for x in d.get("included", []) if x.get("type") == "pool"]
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
    # Fallback: DexScreener (старая логика из analyzer)
    ds = await _ds_token_data(mint)
    if ds:
        _TD_CACHE[mint] = (now, ds)
    return ds


async def _ds_token_data(mint: str) -> dict:
    import config
    from http_client import get_session
    session = await get_session()
    url = f"{config.DEXSCREENER_SEARCH}{mint}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    try:
        async with session.get(url, headers=headers, timeout=10) as r:
            if r.status == 200:
                data = await r.json()
                pairs = data.get("pairs", [])
                sol = [p for p in pairs if p.get("chainId") == "solana"]
                if sol:
                    best = sorted(sol, key=lambda x: x.get("liquidity", {}).get("usd", 0),
                                  reverse=True)[0]
                    best["_source"] = "dexscreener"
                    best["_socials_unknown"] = False
                    return best
    except Exception as e:
        print(f"Dexscreener token data error: {type(e).__name__} {e}")
    return {}


async def get_price(mint: str) -> float:
    prices = await get_bulk_prices([mint])
    return prices.get(mint, 0.0)


async def get_bulk_prices(mints: list) -> dict:
    """Балк-цены: сначала Jupiter Lite (свой лимит, не трогает квоту GT),
    недостающее добираем через GeckoTerminal simple endpoint (до 30 за запрос)."""
    out = {}
    if not mints:
        return out
    ms = [m for m in dict.fromkeys(mints) if m]
    try:
        out = await _jup_lite_prices(ms)
    except Exception as e:
        print(f"Jupiter Lite bulk error: {type(e).__name__} {e}")
    missing = [m for m in ms if m not in out]
    for i in range(0, len(missing), 30):
        chunk = missing[i:i + 30]
        d = await _gt_get("/simple/networks/solana/token_price/" + ",".join(chunk))
        try:
            px = d.get("data", {}).get("attributes", {}).get("token_prices", {})
            for m, p in px.items():
                out[m] = float(p)
        except Exception:
            pass
    return out


async def _jup_lite_prices(mints: list) -> dict:
    """Цены через Jupiter Lite Price API v3 (до 50 адресов за запрос)."""
    import json as _json
    import urllib.request as _url
    out = {}

    def one_call(chunk):
        url = ("https://lite-api.jup.ag/price/v3?ids=" + ",".join(chunk))
        req = _url.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                         "Accept": "application/json"})
        with _url.urlopen(req, timeout=15) as r:
            return _json.load(r)

    for i in range(0, len(mints), 50):
        chunk = mints[i:i + 50]
        try:
            data = await asyncio.to_thread(one_call, chunk)
        except Exception:
            continue
        for m in chunk:
            try:
                px = data.get(m, {}).get("usdPrice", 0)
                if px and float(px) > 0:
                    out[m] = float(px)
            except Exception:
                continue
    return out


async def get_trending() -> list:
    """Замена boosts/profiles: тренды GT + свежие пулы pump.fun.
    Формат как у DexScreener boosts: [{'tokenAddress','chainId'}]."""
    out, seen = [], set()

    def add(mint):
        if mint and mint not in seen and len(mint) > 30:
            seen.add(mint)
            out.append({"tokenAddress": mint, "chainId": "solana"})

    d = await _gt_get("/networks/solana/trending_pools?page=1")
    for item in (d.get("data") or []):
        add(_base_mint(item))
    d2 = await _gt_get("/networks/solana/dexes/pump-fun/pools?page=1")
    for item in (d2.get("data") or []):
        add(_base_mint(item))
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
