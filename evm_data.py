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


async def fetch_boosted_tokens() -> list:
    """Бусты latest+top, фильтр chainId=robinhood -> адреса 0x..."""
    out = []
    for path in ("/token-boosts/latest/v1", "/token-boosts/top/v1"):
        status, data = await fetch_json(DS + path, timeout=10, retries=2)
        if status != 200 or not isinstance(data, list):
            continue
        for t in data:
            if t.get("chainId") == SLUG:
                addr = t.get("tokenAddress")
                if addr and addr not in out:
                    out.append(addr)
    return out


async def fetch_profile_tokens() -> list:
    """Новые профили, фильтр chainId=robinhood -> адреса."""
    status, data = await fetch_json(DS + "/token-profiles/latest/v1",
                                    timeout=10, retries=2)
    if status != 200 or not isinstance(data, list):
        return []
    return [t.get("tokenAddress") for t in data
            if t.get("chainId") == SLUG and t.get("tokenAddress")]


async def get_token_data(address: str) -> dict:
    """Все пулы токена на Robinhood Chain, лучший по ликвидности.
    Возвращает пару в форме DexScreener (как Solana) + _chain='robinhood'."""
    status, data = await fetch_json(
        f"{DS}/token-pairs/v1/{SLUG}/{address}", timeout=10, retries=2)
    if status != 200 or not data:
        return {}
    # ВАЖНО: этот эндпоинт возвращает голый массив, не {pairs:[...]}
    pairs = data if isinstance(data, list) else data.get("pairs", [])
    pools = [p for p in pairs if p.get("chainId") == SLUG]
    if not pools:
        return {}
    best = sorted(pools, key=lambda x: (x.get("liquidity") or {}).get("usd", 0),
                  reverse=True)[0]
    best["_source"] = "dexscreener-robinhood"
    best["_chain"] = SLUG
    best["_socials_unknown"] = False
    return best


async def get_bulk_prices(addresses: list) -> dict:
    """Балк-цены до 30 адресов за запрос (голый массив в ответе)."""
    out = {}
    ms = [a for a in dict.fromkeys(addresses) if a]
    for i in range(0, len(ms), 30):
        chunk = ms[i:i + 30]
        status, data = await fetch_json(
            f"{DS}/tokens/v1/{SLUG}/{','.join(chunk)}", timeout=10, retries=2)
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


async def get_trending_pools_gt() -> list:
    """Запасной дискавери: трендовые пулы GeckoTerminal сети robinhood."""
    from http_client import fetch_json as _f
    status, data = await _f(
        "https://api.geckoterminal.com/api/v2/networks/robinhood/trending_pools",
        timeout=10, retries=1)
    if status != 200:
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
    return out
