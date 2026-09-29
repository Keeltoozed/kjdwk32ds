"""Прямые цены EVM с нод блокчейна (мимо лимитов DexScreener/GeckoTerminal).

Для ОТКРЫТЫХ позиций (критичный путь стопов/трейлингов) цену берём батчем
eth_call getReserves с RPC ноды — 1 HTTP-батч на сеть за цикл вместо
DS-балка + до 12 поштучных + GT-запросов. DS/GT остаются только для
дискавери (там кэши и квоты сходятся).

Покрытие: UniswapV2-пулы (40-hex адрес: pancake/uniswap-v2/flapsh).
UniswapV4 poolId (66-hex, весь ROB-uniswap) — eth_call невозможен без
PoolManager (на ROB канона нет); такие отдаём DS-фолбэку, считаем метрику.
"""
import time as _t

from http_client import fetch_json

# chain -> HTTP RPC (публичные, без ключей; ROB — официальный)
RPCS = {
    "robinhood": "https://rpc.mainnet.chain.robinhood.com",
    "base": "https://base-rpc.publicnode.com",
    "bsc": "https://bsc-rpc.publicnode.com",
    "ethereum": "https://ethereum-rpc.publicnode.com",
}

SEL_RESERVES = "0x0902f1ac"   # getReserves()
SEL_TOKEN0 = "0x0dfe1681"     # token0()
SEL_DECIMALS = "0x313ce567"   # decimals()

STABLES = {"USDC", "USDT", "DAI", "USD₮"}
QUOTE_COIN = {"WETH": "coingecko:ethereum", "ETH": "coingecko:ethereum",
              "WBNB": "coingecko:binancecoin", "BNB": "coingecko:binancecoin"}

# (chain, mint_lower) -> pool-info
_POOL_CACHE: dict = {}
# quote coin -> (price, ts)
_QUOTE_CACHE: dict = {}

HITS = {"direct": 0, "fallback": 0, "v4skip": 0}


def _norm(a: str) -> str:
    return (a or "").lower()


async def _rpc_batch(url: str, calls: list):
    """JSON-RPC батч одним POST. Возвращает {id: result_hex}."""
    out = {}
    if not calls:
        return out
    payload = [{"jsonrpc": "2.0", "id": i + 1, "method": "eth_call",
                "params": [{"to": to, "data": data}, "latest"]}
               for i, (to, data) in enumerate(calls)]
    try:
        status, data = await fetch_json(url, timeout=10, retries=1,
                                        method="POST", json_payload=payload)
    except Exception:
        return out
    if status != 200 or not data:
        return out
    items = data if isinstance(data, list) else [data]
    for it in items:
        try:
            if isinstance(it, dict) and "result" in it and isinstance(it["result"], str):
                out[int(it.get("id", -1))] = it["result"]
        except Exception:
            continue
    return out


async def _quote_usd(coins: set) -> dict:
    """USD стейблов и WETH/WBNB через Llama (1 запрос, кэш 60с). DS/GT не трогаем."""
    prices = {}
    need = []
    for c in coins:
        if c in STABLES:
            prices[c] = 1.0
        elif c in QUOTE_COIN:
            hit = _QUOTE_CACHE.get(c)
            if hit and _t.time() - hit[1] < 60:
                prices[c] = hit[0]
            else:
                need.append(QUOTE_COIN[c])
    if need:
        try:
            ids = ",".join(sorted(set(need)))
            status, data = await fetch_json(
                f"https://coins.llama.fi/prices/current/{ids}",
                timeout=10, retries=1)
            if status == 200 and data:
                inv = {v: k for k, v in QUOTE_COIN.items()}
                for cid, obj in ((data.get("coins") or {}).items()):
                    sym = next((s for s, cc in QUOTE_COIN.items() if cc == cid), None)
                    px = float((obj or {}).get("price", 0) or 0)
                    if sym and px > 0:
                        prices[sym] = px
                        _QUOTE_CACHE[sym] = (px, _t.time())
        except Exception:
            pass
    return prices


async def _pool_info(mint: str, chain: str):
    """Разрешение пула один раз на позицию: лучший по ликвидности V2-пул (40-hex).
    V4 poolId (66-hex) сразу помечаем skip — их ведёт DS-фолбэк."""
    key = (chain, _norm(mint))
    if key in _POOL_CACHE:
        return _POOL_CACHE[key]
    try:
        import evm_data
        td = await evm_data.get_token_data(mint, chain)
    except Exception:
        td = {}
    if not td:
        return None
    pool = td.get("pairAddress", "") or ""
    if len(pool) != 42:
        HITS["v4skip"] += 1
        _POOL_CACHE[key] = {"v4": True}
        return _POOL_CACHE[key]
    base = ((td.get("baseToken") or {}).get("address")) or mint
    quote = ((td.get("quoteToken") or {}).get("symbol", "")) or ""
    info = {"pool": pool, "base": base, "quote": quote.upper(),
            "token0": None, "decB": None, "decQ": None}
    _POOL_CACHE[key] = info
    return info


async def get_direct_prices(mints: list, chain: str) -> dict:
    """Цены напрямую с ноды для списка минтов. Возвращает {mint: price} (как дали)."""
    out = {}
    rpc = RPCS.get(chain)
    if not rpc:
        return out
    ms = [m for m in dict.fromkeys(mints) if m]
    infos = {}
    for m in ms:
        try:
            infos[m] = await _pool_info(m, chain)
        except Exception:
            continue
    # добираем token0/token1/decimals одним батчем для новых пулов:
    # на пул 4 вызова: token0, token1, decimals(base), decimals(quote-позже)
    meta_calls, meta_order = [], []
    for m, inf in infos.items():
        if not inf or inf.get("v4") or inf.get("token0"):
            continue
        meta_calls += [(inf["pool"], SEL_TOKEN0),
                       (inf["pool"], "0xd21220a7"),  # token1()
                       (inf["base"], SEL_DECIMALS)]
        meta_order.append(m)
    if meta_calls:
        res = await _rpc_batch(rpc, meta_calls)
        qcalls, qorder = [], []
        for i, m in enumerate(meta_order):
            inf = infos[m]
            try:
                t0 = ("0x" + res[3 * i + 1][-40:]).lower()
                t1 = ("0x" + res[3 * i + 2][-40:]).lower()
                decB = int(res[3 * i + 3], 16)
                base_l = _norm(inf["base"])
                inf["token0"] = t0
                inf["decB"] = decB
                inf["quote_addr"] = t1 if t0 == base_l else t0
                qcalls.append((inf["quote_addr"], SEL_DECIMALS))
                qorder.append(m)
            except Exception:
                continue
        if qcalls:
            res2 = await _rpc_batch(rpc, qcalls)
            for i, m in enumerate(qorder):
                try:
                    infos[m]["decQ"] = int(res2[i + 1], 16)
                except Exception:
                    infos[m]["decQ"] = 18
    # USD котировок
    quotes = {inf["quote"] for inf in infos.values() if inf and not inf.get("v4") and inf.get("quote")}
    qpx = await _quote_usd(quotes)
    # резервы одним батчем
    rcalls, rorder = [], []
    for m, inf in infos.items():
        if not inf or inf.get("v4") or not inf.get("token0") or inf.get("decB") is None:
            continue
        rcalls.append((inf["pool"], SEL_RESERVES))
        rorder.append(m)
    if rcalls:
        res = await _rpc_batch(rpc, rcalls)
        for i, m in enumerate(rorder):
            inf = infos[m]
            try:
                raw = res[i + 1]
                r0 = int(raw[2:66], 16)
                r1 = int(raw[66:130], 16)
                decB = inf["decB"]
                decQ = inf.get("decQ") or 18
                base_is_t0 = (_norm(inf["base"]) == inf["token0"])
                rB = r0 if base_is_t0 else r1
                rQ = r1 if base_is_t0 else r0
                if rB <= 0:
                    continue
                qsym = inf.get("quote", "")
                qusd = qpx.get(qsym, 0)
                if not qusd:
                    continue
                price = (rQ / (10 ** decQ) * qusd) / (rB / (10 ** decB))
                if price > 0:
                    out[m] = price
                    out[m.lower()] = price
                    out[m.upper()] = price
                    HITS["direct"] += 1
            except Exception:
                continue
    return out
