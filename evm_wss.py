"""EVM factory-listener: новые пулы в реальном времени (до индексации DS/GT).

Подписка eth_subscribe logs на фабрики Uniswap V2/V3 (Base, BSC) через
публичные PublicNode WSS (без ключей). Событие PairCreated/PoolCreated ->
токен (не-quote сторона) -> очередь свежих -> скан-петля разбирает ПЕРВЫМ.
Задержка секунды вместо минут опроса. Robinhood: публичного WSS нет - только опрос.

Топики считаются keccak рантайм (pysha3), fallback - проверенные константы.
"""
import asyncio
import json
import time

# Проверенные keccak-значения (пересчитаны, совпали с каноном Uniswap и Aerodrome)
TOPIC_V3_POOL_CREATED = "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"
TOPIC_V2_PAIR_CREATED = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
TOPIC_SLIPSTREAM_POOL_CREATED = "0xab0d57f0df537bb25e80245ef7748fa62353808c54d6e528a9dd20887aed9ac2"

V3_FACTORY = "0x1F98431c8aD98523631D114a775f6886A8e1957b6"
V2_FACTORY = "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f"

# Реальные фабрики по сетям (Base: Uniswap V3/V2, Aerodrome Slipstream/V2, Pancake, Sushi, BaseSwap, SwapBased)
FACTORIES_PER_CHAIN = {
    "base": [
        ("0x33128a8fC17869897dcE68Ed026d694621f6FDfD", "v3"),         # Uniswap V3 Base (Clanker / native)
        ("0x5e7BB104d84c7CB9B682AaC2F3d509f5F406809A", "slipstream"), # Aerodrome Slipstream CLFactory Base
        ("0x420DD381b31aEf6683db6B902084cB0FFECe40Da", "v2"),         # Aerodrome V2 Base
        ("0x8909Dc15e40173Ff4699343b6eB8132c65e18eC6", "v2"),         # Uniswap V2 Base
        ("0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865", "v3"),         # PancakeSwap V3 Base
        ("0x02a84c1b3BBD7401a5f7fa98a384EBC70bB5749E", "v2"),         # PancakeSwap V2 Base
        ("0x71524B4f93c58fcbF659783284E38825f0622859", "v2"),         # SushiSwap V2 Base
        ("0xc35DADB65012eC5796536bD9864eD8773aBc74C4", "v3"),         # SushiSwap V3 Base
        ("0xFDa619b6d20975be80A10332cD39b9a4b0FAa8BB", "v2"),         # BaseSwap V2
        ("0x04C9f118d21e8B767d2e50C946f0cC9F6C367300", "v2"),         # SwapBased V2
    ],
    "bsc": [
        ("0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73", "v2"),         # PancakeSwap V2 Factory BSC
        ("0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865", "v3"),         # PancakeSwap V3 Factory BSC
        ("0x858E3312ed37354473981844faE290602524d799", "v2"),         # Biswap V2 Factory BSC
        ("0x86407bEa2078ea5f5EB5A52B2caA963bC1F889Da", "v2"),         # BabySwap V2 Factory BSC
        ("0xdB1d10011AD0Ff90774D0C6Bb92e5C5c8b4461F7", "v3"),         # Uniswap V3 Factory BSC
        ("0x0841BD0B734E4F5853f0dD8d7Ea041c241fb0Da6", "v2"),         # ApeSwap V2 Factory BSC
    ],
}

# chain -> (wss_url, quote-токены, для которых второй адрес = новый мем)
CHAINS_WSS = {
    "base": ("wss://base-rpc.publicnode.com", {
        "0x4200000000000000000000000000000000000006",  # WETH
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC
        "0x0b3e328455c4059eeb9e3f84b5543f74e24e7e1b",  # VIRTUAL (Virtuals Protocol agent memes)
        "0x940181a94a35a4569e4529a3cdfb74e38fd98631",  # AERO (Aerodrome native pools)
        "0x1bc0c42215582d5a085795f4badbac3ff36d1bcb",  # CLANKER (Clanker token pairs)
        "0x4ed4e862860bed51a9570b96d89af5e1b0efefed",  # DEGEN (Farcaster degen memes)
        "0x0578d8a44db98b23bf096a382e016e29a5ce0ffe",  # HIGHER (Farcaster higher meme)
        "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca",  # USDbC
        "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf",  # cbBTC
    }),
    "bsc": ("wss://bsc-rpc.publicnode.com", {
        "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",  # WBNB
        "0x55d398326f99059f775485246999027b3197955c",  # USDT
        "0xe9e7cea3dedca5984780bafc599bd69add087d56",  # BUSD
        "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",  # USDC
        "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82",  # CAKE
    }),
}

# chain -> [(token, pool, ts), ...] свежие пулы (моложе 10 мин)
FRESH_POOLS: dict = {}

# Копи-покупки китов: chain -> {token_lower: ts}. Скан разбирает их первыми,
# в сделке метим source COPY (видно в дашборде, откуда вход).
COPY_BUYS: dict = {}

# ERC20 Transfer(address,address,uint256) — канонический топик, одинаков везде
TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3e6"


def _pad_addr(a: str) -> str:
    a = a.lower().replace("0x", "")
    return "0x" + "0" * (64 - len(a)) + a


def is_copybuy(chain: str, addr: str) -> bool:
    try:
        ts = (COPY_BUYS.get(chain) or {}).get((addr or "").lower(), 0)
        return (time.time() - ts) <= 600
    except Exception:
        return False


def _copy_wallets(chain: str) -> list:
    try:
        import config
        return list((getattr(config, "EVM_COPY_WALLETS", {}) or {}).get(chain, []) or [])
    except Exception:
        return []


async def _listen_wallets(chain: str, url: str):
    """Копитрейдинг EVM: входящие Transfer на кошельки китов = их покупки.
    Токен (log.address) уходит в очередь свежих с меткой COPY — скан разберёт первым."""
    import websockets
    wallets = [w.lower() for w in _copy_wallets(chain) if w]
    if not wallets:
        return
    padded = [_pad_addr(w) for w in wallets]
    while True:
        try:
            print(f"🐋 EVM-COPY {chain}: подключаюсь ({len(wallets)} китов)...")
            async with websockets.connect(url, max_size=10 ** 6, ping_interval=30, ping_timeout=60) as ws:
                await ws.send(json.dumps({
                    "jsonrpc": "2.0", "id": 21, "method": "eth_subscribe",
                    "params": ["logs", {"topics": [TOPIC_TRANSFER, None, padded]}]}))
                await ws.recv()
                print(f"✅ EVM-COPY {chain}: слушаю покупки китов")
                async for message in ws:
                    try:
                        d = json.loads(message)
                        if d.get("method") != "eth_subscription":
                            continue
                        res = d.get("params", {}).get("result", {}) or {}
                        token = str(res.get("address", "") or "")
                        if len(token) != 42 or not token.startswith("0x"):
                            continue
                        FRESH_POOLS.setdefault(chain, []).append((token, "", time.time()))
                        COPY_BUYS.setdefault(chain, {})[token.lower()] = time.time()
                        print(f"🐋 EVM-COPY {chain}: кит купил {token[:10]}")
                    except Exception:
                        continue
        except Exception as e:
            if "ConnectionClosed" in type(e).__name__ or "keepalive" in str(e).lower():
                print(f"🔄 EVM-COPY {chain}: переподключение к WSS...")
            else:
                print(f"⚠️ EVM-COPY {chain}: {type(e).__name__} {e}. Реконнект 5с...")
            await asyncio.sleep(5)


def _keccak_topics():
    try:
        import sha3

        def t(s):
            k = sha3.keccak_256()
            k.update(s.encode())
            return "0x" + k.hexdigest()
        return (t("PoolCreated(address,address,uint24,int24,address)"),
                t("PairCreated(address,address,address,uint256)"),
                t("PoolCreated(address,address,int24,address)"))
    except Exception:
        return TOPIC_V3_POOL_CREATED, TOPIC_V2_PAIR_CREATED, TOPIC_SLIPSTREAM_POOL_CREATED


def _addr(topic: str) -> str:
    topic = (topic or "").lower()
    if topic.startswith("0x") and len(topic) == 66:
        return "0x" + topic[-40:]
    return ""


def _handle_log(chain: str, quotes: set, log: dict):
    """Из лога фабрики достаёт (новый_токен, пул) или None."""
    try:
        topics = [str(t or "").lower() for t in log.get("topics", [])]
        if not topics:
            return None
        data = str(log.get("data", "") or "0x")
        if (topics[0] in (TOPIC_V3_POOL_CREATED.lower(), TOPIC_SLIPSTREAM_POOL_CREATED.lower())) and len(topics) >= 4:
            t0, t1 = _addr(topics[1]), _addr(topics[2])
            pool = "0x" + data[-40:] if len(data) >= 42 else ""
        elif topics[0] == TOPIC_V2_PAIR_CREATED.lower() and len(topics) >= 3:
            t0, t1 = _addr(topics[1]), _addr(topics[2])
            # PairCreated: pair НЕ indexed - первое слово data, адрес = последние 40 hex
            pool = "0x" + data[26:66] if len(data) >= 66 else ""
        else:
            return None
        q = {x.lower() for x in quotes}
        new = ""
        if t0 in q and t1 not in q:
            new = t1
        elif t1 in q and t0 not in q:
            new = t0
        else:
            return None
        if not new or not pool:
            return None
        return new.lower(), pool.lower()
    except Exception:
        return None


def drain_fresh(chain: str, max_age_sec: int = 600) -> list:
    """Забрать свежие пулы сети (и почистить старые). Возвращает [token, ...]."""
    now = time.time()
    box = FRESH_POOLS.setdefault(chain, [])
    valid = [(tok, pool, ts) for tok, pool, ts in box if now - ts <= max_age_sec][-30:]
    FRESH_POOLS[chain] = valid
    seen = set()
    out = []
    for tok, _pool, _ts in valid:
        tok_clean = tok.lower() if str(tok).startswith("0x") else str(tok)
        if tok_clean not in seen:
            seen.add(tok_clean)
            out.append(tok_clean)
    return out


async def _listen_chain(chain: str, url: str, quotes: set):
    import websockets
    v3, v2, v_slip = _keccak_topics()
    global TOPIC_V3_POOL_CREATED, TOPIC_V2_PAIR_CREATED, TOPIC_SLIPSTREAM_POOL_CREATED
    TOPIC_V3_POOL_CREATED, TOPIC_V2_PAIR_CREATED, TOPIC_SLIPSTREAM_POOL_CREATED = v3, v2, v_slip
    while True:
        try:
            print(f"🔌 EVM-WSS {chain}: подключаюсь...")
            factories = FACTORIES_PER_CHAIN.get(chain, [(V3_FACTORY, "v3"), (V2_FACTORY, "v2")])
            async with websockets.connect(url, max_size=10 ** 6, ping_interval=30, ping_timeout=60) as ws:
                for i, (factory, v_type) in enumerate(factories):
                    if v_type == "v3":
                        topic = v3
                    elif v_type == "slipstream":
                        topic = v_slip
                    else:
                        topic = v2
                    await ws.send(json.dumps({
                        "jsonrpc": "2.0", "id": 10 + i, "method": "eth_subscribe",
                        "params": ["logs", {"address": factory, "topics": [topic]}]}))
                    await ws.recv()
                print(f"✅ EVM-WSS {chain}: слушаю фабрики ({len(factories)} шт)")
                async for message in ws:
                    try:
                        d = json.loads(message)
                        if d.get("method") != "eth_subscription":
                            continue
                        hit = _handle_log(chain, quotes, d.get("params", {}).get("result", {}))
                        if hit:
                            token, pool = hit
                            FRESH_POOLS.setdefault(chain, []).append((token, pool, time.time()))
                            print(f"🆕 EVM-WSS {chain}: новый пул {token[:10]} (пул {pool[:10]})")
                    except Exception:
                        continue
        except Exception as e:
            if "ConnectionClosed" in type(e).__name__ or "keepalive" in str(e).lower():
                print(f"🔄 EVM-WSS {chain}: сокет закрыт узлом. Переподключение через 5с...")
            else:
                print(f"⚠️ EVM-WSS {chain}: {type(e).__name__} {e}. Реконнект 5с...")
            await asyncio.sleep(5)


async def evm_wss_loop(*_args, **_kwargs):
    import config
    if not getattr(config, "EVM_WSS_ENABLED", True):
        return
    tasks = [_listen_chain(c, u, q) for c, (u, q) in CHAINS_WSS.items()]
    # Копи-покупки китов (только где заданы кошельки в EVM_COPY_WALLETS)
    for c, (u, _q) in CHAINS_WSS.items():
        try:
            import config as _c
            if ((getattr(_c, "EVM_COPY_WALLETS", {}) or {}).get(c)):
                tasks.append(_listen_wallets(c, u))
        except Exception:
            pass
    await asyncio.gather(*tasks)
