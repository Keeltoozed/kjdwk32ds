"""EVM-данные для Robinhood Chain (chainId 4663, DexScreener slug "robinhood").

Источники (все бесплатные, без ключа):
- DexScreener boosts/profiles (60 зап/мин) — дискавери мемов
- DexScreener /token-pairs/v1/robinhood/{addr} — данные токена (та же форма пары, что Solana)
- DexScreener /tokens/v1/robinhood/{addrs} — балк-цены до 30 за запрос (возвращает ГОЛЫЙ массив!)
- GeckoTerminal network "robinhood" — запасной источник

Важно: slug "robinhood", НЕ "4663" (тот молча вернёт пусто).
"""
import config
from http_client import fetch_json

SLUG = getattr(config, "ROBINHOOD_DS_SLUG", "robinhood")
DS = "https://api.dexscreener.com"

# TTL-кэш дискавери: свежие пулы обновляем каждый цикл (ранний детект),
# тяжёлые списки (тренды/топ/поиск) держим 2-3 мин, чтобы не превысить квоту GT 25/мин.
import time as _t
_DISC_CACHE = {}
def _cached(key: str, ttl: int):
    hit = _DISC_CACHE.get(key)
    if hit and _t.time() - hit[0] < ttl:
        return hit[1]
    return None
def _store(key: str, val: list):
    _DISC_CACHE[key] = (_t.time(), val)
    return val

# Поддерживаемые EVM-сети: slug DexScreener -> (мин. ликва, метка)
CHAINS = {
    "robinhood": {"min_liq": float(getattr(config, "ROBINHOOD_MIN_LIQUIDITY", 8000)), "tag": "ROBINHOOD"},
    "base": {"min_liq": 15000.0, "tag": "BASE"},
    "bsc": {"min_liq": 15000.0, "tag": "BSC"},
}


async def fetch_boosted_tokens(chain: str = SLUG) -> list:
    """Бусты latest+top, фильтр chainId=chain -> адреса 0x..."""
    out = []
    for path in ("/token-boosts/latest/v1", "/token-boosts/top/v1"):
        status, data = await fetch_json(DS + path, timeout=10, retries=2)
        if status != 200 or not isinstance(data, list):
            continue
        for t in data:
            if t.get("chainId") == chain:
                addr = t.get("tokenAddress")
                if addr and addr not in out:
                    out.append(addr)
    return out


async def fetch_profile_tokens(chain: str = SLUG) -> list:
    """Новые профили, фильтр chainId=chain -> адреса."""
    status, data = await fetch_json(DS + "/token-profiles/latest/v1",
                                    timeout=10, retries=2)
    if status != 200 or not isinstance(data, list):
        return []
    return [t.get("tokenAddress") for t in data
            if t.get("chainId") == chain and t.get("tokenAddress")]


async def get_token_data(address: str, chain: str = SLUG) -> dict:
    """Все пулы токена в сети chain, лучший по ликвидности.
    Возвращает пару в форме DexScreener (как Solana) + _chain."""
    status, data = await fetch_json(
        f"{DS}/token-pairs/v1/{chain}/{address}", timeout=10, retries=2)
    if status != 200 or not data:
        return {}
    # ВАЖНО: этот эндпоинт возвращает голый массив, не {pairs:[...]}
    pairs = data if isinstance(data, list) else data.get("pairs", [])
    pools = [p for p in pairs if p.get("chainId") == chain]
    if not pools:
        return {}
    best = sorted(pools, key=lambda x: (x.get("liquidity") or {}).get("usd", 0),
                  reverse=True)[0]
    best["_source"] = f"dexscreener-{chain}"
    best["_chain"] = chain
    best["_socials_unknown"] = False
    return best


async def get_bulk_prices(addresses: list, chain: str = SLUG) -> dict:
    """Балк-цены до 30 адресов за запрос (голый массив в ответе)."""
    out = {}
    ms = [a for a in dict.fromkeys(addresses) if a]
    for i in range(0, len(ms), 30):
        chunk = ms[i:i + 30]
        status, data = await fetch_json(
            f"{DS}/tokens/v1/{chain}/{','.join(chunk)}", timeout=10, retries=2)
        if status != 200 or not data:
            continue
        pairs = data if isinstance(data, list) else data.get("pairs", [])
        for p in pairs:
            try:
                base = (p.get("baseToken") or {})
                addr = base.get("address", "")
                price = float(p.get("priceUsd", 0) or 0)
                if addr and price > out.get(addr, 0):
                    out[addr] = price
            except Exception:
                continue
    return out


async def get_trending_pools_gt(chain: str = SLUG) -> list:
    """Запасной дискавери: трендовые пулы GeckoTerminal сети chain.
    Ловит лидеров по объему (ARCHIBROWN/Agrippa-типа) раньше, чем бусты.
    Идёт через общий лимитер market_data._gt_get (единая квота GT на процесс)."""
    hit = _cached(f"trend:{chain}", 180)
    if hit is not None:
        return hit
    from market_data import _gt_get as _g
    gt_net = "base" if chain == "base" else ("robinhood" if chain == "robinhood" else chain)
    data = await _g(f"/networks/{gt_net}/trending_pools", retries=1)
    if not data:
        return []
    out = []
    for item in (data.get("data") or []):
        try:
            bid = item.get("relationships", {}).get("base_token", {}).get("data", {}).get("id", "")
            addr = bid.split("_", 1)[1] if "_" in bid else ""
            if addr and addr not in out:
                out.append(addr)
        except Exception:
            continue
    return _store(f"trend:{chain}", out)


async def get_new_pools_gt(chain: str = SLUG, pages: int = 0) -> list:
    """РАННИЙ детект: свежесозданные пулы сети (GeckoTerminal new_pools).
    Именно здесь ракеты видны ДО роста - бусты/тренды показывают уже летящие."""
    from market_data import _gt_get as _g
    if not pages:
        pages = int(getattr(config, "EVM_NEW_POOL_PAGES", 8))
    hit = _cached(f"new:{chain}:{pages}", 60)
    if hit is not None:
        return hit
    gt_net = "base" if chain == "base" else ("robinhood" if chain == "robinhood" else chain)
    out = []
    for page in range(1, pages + 1):
        data = await _g(f"/networks/{gt_net}/new_pools?page={page}", retries=1)
        if not data:
            continue
        for item in (data.get("data") or []):
            try:
                bid = item.get("relationships", {}).get("base_token", {}).get("data", {}).get("id", "")
                addr = bid.split("_", 1)[1] if "_" in bid else ""
                if addr and addr not in out:
                    out.append(addr)
            except Exception:
                continue
    return _store(f"new:{chain}:{pages}", out)


async def get_top_volume_pools_gt(chain: str = SLUG) -> list:
    """Топ пулов по объёму (GeckoTerminal, стр 1-3). Ракеты у которых УЖЕ идёт объём,
    но они ещё не попали в бусты/тренды DexScreener."""
    hit = _cached(f"topvol:{chain}", 180)
    if hit is not None:
        return hit
    from market_data import _gt_get as _g
    gt_net = "base" if chain == "base" else ("robinhood" if chain == "robinhood" else chain)
    out = []
    for page in (1, 2, 3):
        data = await _g(f"/networks/{gt_net}/pools?page={page}", retries=1)
        if not data:
            continue
        for item in (data.get("data") or []):
            try:
                bid = item.get("relationships", {}).get("base_token", {}).get("data", {}).get("id", "")
                addr = bid.split("_", 1)[1] if "_" in bid else ""
                if addr and addr not in out:
                    out.append(addr)
            except Exception:
                continue
    return _store(f"topvol:{chain}", out)


async def fetch_dex_search_tokens(chain: str = SLUG) -> list:
    """DexScreener поиск активных пар сети (3 сортировки). Находит органические ракеты
    которые не проплачены (нет буста/профиля), но уже летят."""
    hit = _cached(f"search:{chain}", 120)
    if hit is not None:
        return hit
    out = []
    # Внимание: /search требует q длиной 2+ (без него/1 символ = 400).
    # Несколько частых биграмм покрывают разные имена, дедуп по адресу ниже.
    for q in ("an", "er", "in", "on", "ar", "es"):
        status, data = await fetch_json(
            f"{DS}/latest/dex/search?q={q}&chainIds={chain}&order=desc",
            timeout=10, retries=1)
        if status != 200 or not data:
            continue
        for p in (data.get("pairs") or []):
            if p.get("chainId") != chain:
                continue
            addr = (p.get("baseToken") or {}).get("address", "")
            if addr and addr not in out:
                out.append(addr)
    return _store(f"search:{chain}", out)

