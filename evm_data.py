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
import time
_t = time
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
# GROWTH: минимум $20k везде (было $30k). Пыль $0-15k режется, раги от вершины,
# а не от глубины (все лузеры были в $30k+ пулах). Conviction-x2 от $30k отдельно.
CHAINS = {
    "robinhood": {"min_liq": float(getattr(config, "ROBINHOOD_MIN_LIQUIDITY", 3000.0)), "tag": "ROBINHOOD"},
    "base": {"min_liq": float(getattr(config, "BASE_MIN_LIQUIDITY", 3000.0)), "tag": "BASE"},
    "bsc": {"min_liq": float(getattr(config, "BSC_MIN_LIQUIDITY", 3000.0)), "tag": "BSC"},
    "ethereum": {"min_liq": float(getattr(config, "ETHEREUM_MIN_LIQUIDITY", 15000.0)), "tag": "ETHEREUM"},
}


def _gt_net(chain: str) -> str:
    """GeckoTerminal network slug: у Ethereum — 'eth', остальные 1:1."""
    if chain == "base":
        return "base"
    if chain == "robinhood":
        return "robinhood"
    if chain == "ethereum":
        return "eth"
    return chain


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


_EVM_PRICE_OBS = {}


def _enrich_evm_m1(address: str, best: dict):
    if not best or not address:
        return
    try:
        px = float(best.get("priceUsd", 0) or 0)
        if px <= 0:
            return
        now = _t.time()
        key = address.lower()
        hist = _EVM_PRICE_OBS.setdefault(key, [])
        hist.append((now, px))
        cutoff = now - 600.0
        if len(hist) > 40:
            hist[:] = [x for x in hist if x[0] >= cutoff]
        if len(_EVM_PRICE_OBS) > 3000:
            _EVM_PRICE_OBS.clear()

        # 5-минутный наблюдавшийся пик
        recent_5m = [p for ts, p in hist if now - ts <= 300.0 and p > 0]
        if recent_5m:
            peak_5m = max(recent_5m)
            if peak_5m > 0:
                best["_drop_from_peak_5m"] = (px - peak_5m) / peak_5m * 100.0

        # Реальное изменение за ~1 минуту (ищем точку 15..120 сек назад)
        cands = [( abs((now - ts) - 60.0), ts, p ) for ts, p in hist if 12.0 <= (now - ts) <= 150.0 and p > 0]
        if cands:
            cands.sort(key=lambda x: x[0])
            old_px = cands[0][2]
            real_m1 = (px - old_px) / old_px * 100.0
            if not isinstance(best.get("priceChange"), dict):
                best["priceChange"] = {}
            if not best["priceChange"].get("m1"):
                best["priceChange"]["m1"] = round(real_m1, 2)
    except Exception:
        pass


# Кэш батч-префетча пар EVM: (chain.lower(), addr.lower()) -> (ts, best_pair_dict)
_EVM_PREFETCH_CACHE: dict = {}


def _cache_evm_pair(chain: str, address: str, best: dict):
    if not address or not isinstance(best, dict):
        return
    best["_source"] = f"dexscreener-{chain}"
    best["_chain"] = chain
    best["_socials_unknown"] = False
    _enrich_evm_m1(address, best)
    now = time.time()
    _EVM_PREFETCH_CACHE[(chain.lower(), address.lower())] = (now, best)
    if len(_EVM_PREFETCH_CACHE) > 2000:
        cutoff = now - 60.0
        for k in [k for k, (ts, _) in list(_EVM_PREFETCH_CACHE.items()) if ts < cutoff]:
            _EVM_PREFETCH_CACHE.pop(k, None)


async def prefetch_token_data(addresses: list, chain: str = SLUG, ttl: float = 12.0) -> dict:
    """Батч-загрузка полных пар до 30 токенов за 1 запрос (/tokens/v1/{chain}/{addrs}).
    Ускоряет сканирование EVM-сетей в 25-30 раз (4 запроса вместо 120!)."""
    now = time.time()
    out = {}
    missing = []
    seen = set()
    for a in addresses or []:
        if not a:
            continue
        al = a.lower()
        if al in seen:
            continue
        seen.add(al)
        hit = _EVM_PREFETCH_CACHE.get((chain.lower(), al))
        if hit and (now - hit[0]) <= ttl:
            out[al] = hit[1]
        else:
            missing.append(a)

    for i in range(0, len(missing), 30):
        chunk = missing[i:i + 30]
        try:
            status, data = await fetch_json(
                f"{DS}/tokens/v1/{chain}/{','.join(chunk)}", timeout=10, retries=1)
            if status != 200 or not data:
                continue
            pairs = data if isinstance(data, list) else data.get("pairs") or []
            pools_by_addr = {}
            for p in pairs:
                if (p.get("chainId") or "").lower() != chain.lower():
                    continue
                addr = ((p.get("baseToken") or {}).get("address") or "")
                if not addr:
                    continue
                al = addr.lower()
                pools_by_addr.setdefault(al, []).append(p)

            for al, p_list in pools_by_addr.items():
                best = sorted(p_list, key=lambda x: (x.get("liquidity") or {}).get("usd", 0) or 0, reverse=True)[0]
                if len(p_list) >= 2:
                    top_l = float((best.get("liquidity") or {}).get("usd", 0) or 0)
                    _active = []
                    for p in p_list:
                        _l = float((p.get("liquidity") or {}).get("usd", 0) or 0)
                        _v5 = float(((p.get("volume") or {}).get("m5", 0) or 0))
                        _tx5 = int(((p.get("txns") or {}).get("m5", {}).get("buys", 0) or 0)) + int(((p.get("txns") or {}).get("m5", {}).get("sells", 0) or 0))
                        if _l >= max(3000.0, top_l * 0.20) or _v5 >= 100.0 or _tx5 >= 3:
                            _active.append(p)
                    if len(_active) >= 2:
                        _prices = [float(p.get("priceUsd", 0) or 0) for p in _active if float(p.get("priceUsd", 0) or 0) > 0]
                        if len(_prices) >= 2:
                            max_p, min_p = max(_prices), min(_prices)
                            if min_p > 0 and (max_p / min_p) >= 1.50:
                                best["_price_divergence"] = True
                                best["_price_divergence_ratio"] = max_p / min_p
                addr_orig = ((best.get("baseToken") or {}).get("address") or al)
                _cache_evm_pair(chain, addr_orig, best)
                out[al] = best
        except Exception:
            continue
    return out


async def get_token_data(address: str, chain: str = SLUG) -> dict:
    """Все пулы токена в сети chain, лучший по ликвидности.
    Возвращает пару в форме DexScreener (как Solana) + _chain."""
    if not address:
        return {}
    hit = _EVM_PREFETCH_CACHE.get((chain.lower(), address.lower()))
    if hit and (time.time() - hit[0]) <= 12.0:
        return hit[1]
    status, data = await fetch_json(
        f"{DS}/token-pairs/v1/{chain}/{address}", timeout=10, retries=2)
    if status != 200 or not data:
        return {}
    # ВАЖНО: этот эндпоинт возвращает голый массив, не {pairs:[...]}
    pairs = data if isinstance(data, list) else data.get("pairs") or []
    pools = [p for p in pairs if p.get("chainId") == chain
             and ((p.get("baseToken") or {}).get("address") or "").lower() == address.lower()]
    if not pools:
        return {}
    best = sorted(pools, key=lambda x: (x.get("liquidity") or {}).get("usd", 0) or 0,
                  reverse=True)[0]
    if len(pools) >= 2:
        top_l = float((best.get("liquidity") or {}).get("usd", 0) or 0)
        _active = []
        for p in pools:
            _l = float((p.get("liquidity") or {}).get("usd", 0) or 0)
            _v5 = float(((p.get("volume") or {}).get("m5", 0) or 0))
            _tx5 = int(((p.get("txns") or {}).get("m5", {}).get("buys", 0) or 0)) + int(((p.get("txns") or {}).get("m5", {}).get("sells", 0) or 0))
            if _l >= max(3000.0, top_l * 0.20) or _v5 >= 100.0 or _tx5 >= 3:
                _active.append(p)
        if len(_active) >= 2:
            _prices = [float(p.get("priceUsd", 0) or 0) for p in _active if float(p.get("priceUsd", 0) or 0) > 0]
            if len(_prices) >= 2:
                max_p, min_p = max(_prices), min(_prices)
                if min_p > 0 and (max_p / min_p) >= 1.50:
                    best["_price_divergence"] = True
                    best["_price_divergence_ratio"] = max_p / min_p
    _cache_evm_pair(chain, address, best)
    return best


async def get_bulk_prices(addresses: list, chain: str = SLUG, pair_addresses: list = None) -> dict:
    """Балк-цены до 30 адресов за запрос (голый массив в ответе).
    Поддерживает как token-адреса (/latest/dex/tokens/), так и pair-адреса (/latest/dex/pairs/{chain}/).
    Ключи — в ОБОИХ регистрах (оригинал + lower + upper) для token и pair.
    ВАЖНО: берём цену пула с МАКС. ликвидностью, а не макс. цену."""
    out = {}
    _liq = {}

    def _process_pairs(pairs_list):
        for p in pairs_list:
            if (p.get("chainId") or "").lower() != (chain or "").lower():
                continue
            try:
                base = (p.get("baseToken") or {})
                addr = base.get("address", "")
                p_addr = p.get("pairAddress", "")
                price = float(p.get("priceUsd", 0) or 0)
                liq = float((p.get("liquidity") or {}).get("usd", 0) or 0)
                if not price:
                    continue
                keys_to_set = []
                if addr:
                    keys_to_set.extend([addr, addr.lower(), addr.upper()])
                if p_addr:
                    keys_to_set.extend([p_addr, p_addr.lower(), p_addr.upper()])
                for _k in keys_to_set:
                    if liq > _liq.get(_k, -1):
                        _liq[_k] = liq
                        out[_k] = price
                    elif _k not in out and liq == 0 and _liq.get(_k) is None:
                        out[_k] = price
            except Exception:
                continue

    # 1. Если известны адреса пар (pairAddress), батчим их первыми — они всегда актуальны
    if pair_addresses:
        ps = [p for p in dict.fromkeys(pair_addresses) if p]
        for i in range(0, len(ps), 30):
            p_chunk = ps[i:i + 30]
            try:
                status, data = await fetch_json(
                    f"{DS}/latest/dex/pairs/{chain}/{','.join(p_chunk)}", timeout=8, retries=1)
                if status == 200 and data:
                    pairs = data if isinstance(data, list) else data.get("pairs") or []
                    _process_pairs(pairs)
            except Exception:
                pass

    # 2. Добираем по адресам токенов (tokens/{addresses}) тех, чьих цен ещё нет
    ms = [a for a in dict.fromkeys(addresses) if a and a not in out and a.lower() not in out]
    if ms:
        for i in range(0, len(ms), 30):
            chunk = ms[i:i + 30]
            status, data = await fetch_json(
                f"{DS}/latest/dex/tokens/{','.join(chunk)}", timeout=8, retries=1)
            if status != 200 or not data:
                continue
            pairs = data if isinstance(data, list) else data.get("pairs") or []
            _process_pairs(pairs)
    return out


async def resolve_ds_pair(pair: str, chain: str) -> str:
    """DexScreener pair-URL → адрес ТОКЕНА (baseToken). Кнопки TG-каналов
    ссылаются на страницы ПАР, а не токенов. '' при любой беде (fail-skip)."""
    if not pair or not chain:
        return ""
    try:
        status, data = await fetch_json(
            f"{DS}/latest/dex/pairs/{chain}/{pair}", timeout=10, retries=1)
        if status != 200 or not isinstance(data, dict):
            return ""
        _pair = data.get("pair") or {}
        if not _pair and isinstance(data.get("pairs"), list) and data["pairs"]:
            _pair = data["pairs"][0]
        addr = ((_pair.get("baseToken") or {}).get("address")) or ""
        return addr if len(addr) > 10 else ""
    except Exception:
        return ""


def _filter_and_rank_gt_items(items: list, min_liq: float = 2000.0) -> list:
    """Отсеивает мёртвые пулы ($0 ликвидности / 0 сделок) сразу на этапе GeckoTerminal
    и сортирует живые ракеты по m5-импульсу и объёму."""
    scored = []
    seen = set()
    for item in (items or []):
        try:
            bid = item.get("relationships", {}).get("base_token", {}).get("data", {}).get("id", "")
            addr = bid.split("_", 1)[1] if "_" in bid else ""
            if not addr or addr.lower() in seen:
                continue
            attrs = item.get("attributes") or {}
            name = str(attrs.get("name") or "").upper()
            _stock_tickers = ("SGOV", "SPY", "QQQ", "META", "INTC", "MU", "NVDA", "AAPL", "GOOGL", "MSFT", "GLD", "HXMAX", "STANDARD")
            if any(name.startswith(st + " ") or name.startswith(st + "/") or name == st or f"/{st}" in name for st in _stock_tickers):
                continue
            liq = float(attrs.get("reserve_in_usd") or 0)
            if liq > 0 and liq < min_liq:
                continue
            if liq > 500000.0:
                continue
            tx = attrs.get("transactions") or {}
            m5_tx = tx.get("m5") or {}
            h1_tx = tx.get("h1") or {}
            b5 = int(m5_tx.get("buys") or 0)
            s5 = int(m5_tx.get("sells") or 0)
            b1 = int(h1_tx.get("buys") or 0)
            s1 = int(h1_tx.get("sells") or 0)
            if (b5 + s5) == 0 and (b1 + s1) < 3 and liq < 5000:
                continue
            pc = attrs.get("price_change_percentage") or {}
            m5_chg = float(pc.get("m5") or 0)
            h1_chg = float(pc.get("h1") or 0)
            # Приоритет живым зелёным пулам с покупками
            score = m5_chg * 2.0 + min(h1_chg, 100.0) * 0.2 + (b5 - s5) * 1.5
            seen.add(addr.lower())
            scored.append((score, addr))
        except Exception:
            continue
    scored.sort(key=lambda x: x[0], reverse=True)
    return [addr for _, addr in scored]


async def get_trending_pools_gt(chain: str = SLUG) -> list:
    """Запасной дискавери: трендовые пулы GeckoTerminal сети chain.
    Ловит лидеров по объему (ARCHIBROWN/Agrippa-типа) раньше, чем бусты.
    Идёт через общий лимитер market_data._gt_get (единая квота GT на процесс)."""
    hit = _cached(f"trend:{chain}", 90)
    if hit is not None:
        return hit
    from market_data import _gt_get as _g
    gt_net = _gt_net(chain)
    data = await _g(f"/networks/{gt_net}/trending_pools", retries=1)
    if not data:
        return []
    out = _filter_and_rank_gt_items(data.get("data") or [], min_liq=2000.0)
    return _store(f"trend:{chain}", out)


async def get_new_pools_gt(chain: str = SLUG, pages: int = 0) -> list:
    """РАННИЙ детект: свежесозданные пулы сети (GeckoTerminal new_pools).
    Именно здесь ракеты видны ДО роста - бусты/тренды показывают уже летящие."""
    from market_data import _gt_get as _g
    if not pages:
        pages = int(getattr(config, "EVM_NEW_POOL_PAGES", 2))
    hit = _cached(f"new:{chain}:{pages}", 45)
    if hit is not None:
        return hit
    gt_net = _gt_net(chain)
    raw_items = []
    for page in range(1, pages + 1):
        data = await _g(f"/networks/{gt_net}/new_pools?page={page}", retries=1)
        if not data:
            continue
        raw_items.extend(data.get("data") or [])
    out = _filter_and_rank_gt_items(raw_items, min_liq=2000.0)
    return _store(f"new:{chain}:{pages}", out)


async def get_top_volume_pools_gt(chain: str = SLUG) -> list:
    """Топ пулов по объёму (GeckoTerminal, стр 1). Ракеты у которых УЖЕ идёт объём,
    но они ещё не попали в бусты/тренды DexScreener."""
    hit = _cached(f"topvol:{chain}", 120)
    if hit is not None:
        return hit
    from market_data import _gt_get as _g
    gt_net = _gt_net(chain)
    raw_items = []
    for page in (1,):
        data = await _g(f"/networks/{gt_net}/pools?page={page}", retries=1)
        if not data:
            continue
        raw_items.extend(data.get("data") or [])
    out = _filter_and_rank_gt_items(raw_items, min_liq=2500.0)
    return _store(f"topvol:{chain}", out)


async def fetch_dex_search_tokens(chain: str = SLUG) -> list:
    """DexScreener поиск активных пар сети. Находит органические ракеты,
    которые не проплачены (нет буста/профиля), но уже летят, и сразу кэширует их пары."""
    hit = _cached(f"search:{chain}", 75)
    if hit is not None:
        return hit
    scored = []
    seen = set()
    # Внимание: /search требует q длиной 2+ (без него/1 символ = 400).
    if chain == "base":
        queries = ("base", "clanker", "virtual", "aero", "virtuals", "base meme", "ai base", "farcaster", "degen", "aerodrome", "bswap", "higher", "base ai")
    elif chain == "bsc":
        queries = ("bsc", "pancakeswap", "bnb", "bsc meme", "cake", "four.meme", "four", "biswap", "baby", "bsc gem")
    elif chain == "robinhood":
        queries = ("robinhood", "rh", "orbit")
    else:
        queries = ("an", "er", "in", "on", "ar", "es", "ai", "el")

    for q in queries:
        status, data = await fetch_json(
            f"{DS}/latest/dex/search?q={q}",
            timeout=10, retries=1)
        if status != 200 or not data:
            continue
        for p in (data.get("pairs") or []):
            if p.get("chainId") != chain:
                continue
            addr = (p.get("baseToken") or {}).get("address", "")
            if not addr or addr.lower() in seen:
                continue
            sym = str((p.get("baseToken") or {}).get("symbol") or "").upper()
            _stock_symbols = {"SPY", "QQQ", "SGOV", "GLD", "AAPL", "MSFT", "NVDA", "INTC", "META", "GOOGL", "GOOG", "PLTR", "MU", "TSLA", "AMZN", "CBBTC", "WETH", "USDT", "USDC", "DAI"}
            if sym in _stock_symbols or any(s in sym for s in ("XMAX", "1L", "2L", "3L")):
                continue
            _px = float(p.get("priceUsd", 0) or 0)
            if _px > 50.0:  # Мемы не стоят $50+ (это синтетические акции или обертки)
                continue
            _created = p.get("pairCreatedAt") or 0
            _age_h = (time.time() * 1000 - _created) / 3600000.0 if _created else 999.0
            _v1 = float((p.get("volume") or {}).get("h1", 0) or 0)
            if _age_h > 48.0 and _v1 < 3000.0:
                continue
            liq = float((p.get("liquidity") or {}).get("usd", 0) or 0)
            if liq < 2500:
                continue
            tx = p.get("txns") or {}
            m5_tx = tx.get("m5") or {}
            h1_tx = tx.get("h1") or {}
            b5 = int(m5_tx.get("buys") or 0)
            s5 = int(m5_tx.get("sells") or 0)
            b1 = int(h1_tx.get("buys") or 0)
            s1 = int(h1_tx.get("sells") or 0)
            if (b5 + s5) == 0 and (b1 + s1) < 5:
                continue
            pc = p.get("priceChange") or {}
            m5_chg = float(pc.get("m5") or 0)
            h1_chg = float(pc.get("h1") or 0)
            if m5_chg < -8.0 and h1_chg < 0:
                continue
            _cache_evm_pair(chain, addr, p)
            seen.add(addr.lower())
            score = m5_chg * 2.5 + min(h1_chg, 120.0) * 0.25 + (b5 - s5) * 1.5
            scored.append((score, addr))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = [addr for _, addr in scored]
    return _store(f"search:{chain}", out)


