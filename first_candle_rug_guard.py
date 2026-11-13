"""
first_candle_rug_guard.py — предвходовой детектор «мгновенного рага первой свечой»
                            (First Candle Rug) с глубоким анализом кошельков.

ЗАЧЕМ
-----
Бот регулярно ловит монеты, которые рагают на ПЕРВОЙ свече: дев выводит ликвидность
или продаёт свой стек в первые 30-180 секунд после входа (см. exit_managers.py ->
"🚨 First Candle Rug Cut"). Потерять на этом -15..-35% — самая частая смерть позиции.

Судя по разметке trades_raydium_rows (TIMSEN, CSI, CASH, TITTY, FOOD CAT, GTAINU, DCA,
CHRISTMAX, GIGAPIG, THRONG, FOOD, CHARITY, AUTISM) — РАЗДЕЛИТЬ «ракету» и «мгновенный раг»
по рыночным метрикам (m5, ликвидность, концентрация) НЕВОЗМОЖНО: у ракет и рагов
почти одинаковые liq/m5/top10. Разница видна только ОНЧЕЙН: кто держит токен и
ОТКУДА эти кошельки получили SOL. Именно это и делает модуль.

ЧТО ДЕЛАЕТ (всё ДО входа)
-------------------------
1. КОГОРТА. Собирает кошельки, связанные с токеном:
   - топ-холдеры (RugCheck topHolders → owner + % эмиссии),
   - ранние покупатели пула (GeckoTerminal /trades → tx_from_address).
2. ФИНАНСИРОВАНИЕ. Для каждого кошелька достаёт ПЕРВУЮ транзакцию (genesis) и по
   изменению балансов (preBalances/postBalances) определяет, кто перевёл SOL —
   ИСТОЧНИК ФИНАНСИРОВАНИЯ. Это ключ к сибил-детекту: дев раздаёт SOL суб-кошелькам.
3. КЛАСТЕРЫ «ОДНОГО ИСТОЧНИКА». Группирует кошельки по источнику финансирования.
   Для каждого кластера считает:
     - сколько кошельков в кластере,
     - сколько % эмиссии они держат (сумма) и какой % у каждого,
     - сколько $ это стоит,
     - в какое временное окно профинансированы (бурст раздачи),
     - одинаковые ли суммы перевода (клоны),
     - было ли ПЕРВОЕ действие кошелька — покупка ЭТОГО токена (launch-prep ферма).
4. МИКРОСТРУКТУРА. Вторичные сигналы рага первой свечи: памп с мёртвым m1 (дев сливает
   стек в толпу), sell-domination, тонкий пул, огромный v/l, слишком молодая монета,
   концентрация одного холдера.
5. ВЕРДИКТ. score 0..1 + причины + человекочитаемый отчёт для консоли/дашборда.

ИНТЕГРАЦИЯ
----------
- main.py: SCANNER pre-flight (рядом с PRE-FLIGHT RUG WALLET AUDIT).
- grok_jev.py: full_anti_scam_audit (аудит перед JEV-оценкой).

ПОЛИТИКА ОТКАЗА
---------------
Fail-open: при сетевой ошибке вход НЕ блокируется (score=0, degraded=True).
Никогда не блокирует вход из-за отсутствия данных — только из-за найденных паттернов.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

import config

try:
    from http_client import get_session
except Exception:  # pragma: no cover - автономный режим
    get_session = None


# =============================================================================
# КОНСТАНТЫ / СПРАВОЧНИКИ
# =============================================================================

SYSTEM_PROGRAM = "11111111111111111111111111111111"

# Системные/программные аккаунты: они не могут быть «источником финансирования»
_PROGRAM_ACCOUNTS = {
    SYSTEM_PROGRAM,
    "SysvarRent111111111111111111111111111111111",
    "SysvarC1ock11111111111111111111111111111111",
    "SysvarS1otHashes111111111111111111111111111111",
    "SysvarRecentB1ockHashes11111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s",
    "ComputeBudget111111111111111111111111111111",
    "Vote111111111111111111111111111111111111111",
    "Stake11111111111111111111111111111111111111",
    "AddressLookupTab1e1111111111111111111111111",
    "AddressLookupTab1e1111111111111111111111111",
    "Sysvar1nstructions1111111111111111111111111",
    "ComputeBudget111111111111111111111111111111",
}

# Известные агрегаторы/DEX — если SOL пришёл от них, это не «раздача дева»
_KNOWN_LIQUIDITY_SOURCES = {
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "SoLFiHG9TfgtdUXUjWAxi3LtvYuFyDLVhBWxdMZxyCe",
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",
    "D4FPEruKEHrG5NTsqazoqfv8zsFWApqTjuzFLUQDkTz5",
    "GpMZpSM2GgvTKHJirzeGfMFoaZ8UR2X7F4v8vHTvxFbL",
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
    "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB",
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
    "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1",
}

# Адреса, которые не могут быть владельцами токен-аккаунтов (pool vaults и т.п.)
_NON_OWNER_HINTS = (
    "raydium", "meteora", "orca", "pump", "pumpswap", "amm", "pool",
    "vault", "locker", "raydiumclmm", "whirlpool",
)

# Известные биржи/кастодианы: если SOL пришёл от них, это может быть честный ретейл.
# Такие кластеры оцениваем с пониженным весом (иначе ловим ложные блокировки).
_EXCHANGE_LABELS = {
    "binance", "coinbase", "kraken", "bybit", "okx", "gate", "mexc", "htx",
    "crypto-com", "robinhood", "strike", "moonpay", "transact", "kaiko",
}


def _cfg(name: str, default: Any) -> Any:
    """Читает настройку из config.py, но не падает, если её там нет."""
    try:
        v = getattr(config, name, None)
        return default if v is None else v
    except Exception:
        return default


# =============================================================================
# КЕШИ
# =============================================================================

# Кеш вердикта по минту: mint -> (ts, verdict)
_VERDICT_CACHE: Dict[str, Tuple[float, dict]] = {}
# Кеш источника финансирования: wallet -> (ts, info). Источник не меняется — TTL большой.
_FUNDER_CACHE: Dict[str, Tuple[float, dict]] = {}
# Кеш когорты холдеров: mint -> (ts, cohort)
_COHORT_CACHE: Dict[str, Tuple[float, list]] = {}


def _cache_get(store: dict, key: str, ttl: float):
    hit = store.get(key)
    if hit and (time.time() - hit[0]) < ttl:
        return hit[1]
    return None


def _cache_set(store: dict, key: str, value) -> None:
    if len(store) > 4000:
        store.clear()
    store[key] = (time.time(), value)


def _clear_cache() -> None:
    _VERDICT_CACHE.clear()
    _FUNDER_CACHE.clear()
    _COHORT_CACHE.clear()


# =============================================================================
# RPC-СЛОЙ (batch JSON-RPC с фоллбэками)
# =============================================================================

_RPC_SEM: Optional[asyncio.Semaphore] = None
_RPC_LAST_CALL: Dict[str, float] = {}


def _rpc_endpoints() -> List[str]:
    """
    Приоритет RPC-эндпоинтов: Helius (если есть ключ) → publicnode → mainnet-beta.
    ankr без ключа не работает (отдаёт 403), mainnet-beta агрессивно рейтлимитит
    тяжёлые батчи, поэтому publicnode идёт первым.
    """
    urls: List[str] = []
    try:
        helius = getattr(config, "HELIUS_RPC_URL", "") or ""
        if helius and "api.mainnet-beta" not in helius:
            urls.append(helius)
    except Exception:
        pass
    urls.extend([
        "https://solana-rpc.publicnode.com",
        "https://api.mainnet-beta.solana.com",
    ])
    seen, out = set(), []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _rpc_sem() -> asyncio.Semaphore:
    global _RPC_SEM
    if _RPC_SEM is None:
        _RPC_SEM = asyncio.Semaphore(3)
    return _RPC_SEM


async def _rpc_batch(calls: List[Tuple[str, list]], timeout: float = 9.0) -> List[Any]:
    """
    Батч JSON-RPC: [(method, params), ...] -> [result | None, ...].
    Один HTTP- запрос на весь батч — критично для скорости входа (иначе 2*N запросов).
    """
    if not calls:
        return []
    if get_session is None:
        return [None] * len(calls)

    payload = [
        {"jsonrpc": "2.0", "id": i, "method": m, "params": p}
        for i, (m, p) in enumerate(calls)
    ]
    session = await get_session()
    last_err = "?"
    for url in _rpc_endpoints():
        try:
            async with _rpc_sem():
                # Минимальный интервал между стартами запросов к одному хосту
                last = _RPC_LAST_CALL.get(url, 0.0)
                gap = 0.25 - (time.monotonic() - last)
                if gap > 0:
                    await asyncio.sleep(gap)
                _RPC_LAST_CALL[url] = time.monotonic()
                async with session.post(
                    url, json=payload, timeout=aiohttp.ClientTimeout(total=timeout)
                ) as resp:
                    if resp.status != 200:
                        last_err = f"HTTP {resp.status}"
                        continue
                    body = await resp.json(content_type=None)
            if isinstance(body, dict):
                body = [body]
            if not isinstance(body, list):
                last_err = "bad-body"
                continue
            by_id: Dict[int, Any] = {}
            for item in body:
                if isinstance(item, dict) and "id" in item:
                    try:
                        by_id[int(item["id"])] = item.get("result")
                    except Exception:
                        continue
            return [by_id.get(i) for i in range(len(calls))]
        except asyncio.CancelledError:
            raise
        except Exception as e:
            last_err = type(e).__name__
            continue
    print(f"⚠️ [FCR] RPC batch не удался: {last_err} ({len(calls)} вызовов)")
    return [None] * len(calls)


# =============================================================================
# 0. СНИМОК RUGCHECK (supply, известные аккаунты, инсайдерские сети)
# =============================================================================

async def _rugcheck_snapshot(mint: str) -> dict:
    """Кэшированный отчёт RugCheck: supply, knownAccounts (AMM/создатель/сейфы)."""
    try:
        import goplus as _gp
        st, data = await _gp.fetch_rugcheck_report_cached(mint, ttl=120.0, timeout=6)
    except Exception:
        return {}
    if st != 200 or not isinstance(data, dict):
        return {}
    return data


def _known_account_labels(report: dict) -> dict:
    """{адрес: тип} из RugCheck knownAccounts: AMM / LOCKER / CREATOR / EXCHANGE."""
    ka = (report or {}).get("knownAccounts") or {}
    out: Dict[str, str] = {}
    if isinstance(ka, dict):
        for addr, info in ka.items():
            if isinstance(info, dict):
                out[str(addr)] = str(info.get("type") or info.get("name") or "").upper()
    return out


# =============================================================================
# 1. КОГОРТА КОШЕЛЬКОВ (топ-холдеры + ранние покупатели)
# =============================================================================

def _is_pool_like(addr: str) -> bool:
    low = (addr or "").lower()
    return any(h in low for h in _NON_OWNER_HINTS)


async def _holders_from_rugcheck(mint: str) -> List[dict]:
    """Топ-холдеры из RugCheck-отчёта (owner + % эмиссии)."""
    try:
        import goplus as _gp
        st, data = await _gp.fetch_rugcheck_report_cached(mint, ttl=120.0, timeout=6)
    except Exception:
        return []
    if st != 200 or not isinstance(data, dict):
        return []
    out: List[dict] = []
    # RugCheck знает, какие адреса — пулы/сейфы (knownAccounts). Именно они держат
    # 40-99% "эмиссии" в топ-холдерах, и без их отсечения любой токен выглядит
    # как "один кошелёк держит 40%". Без knownAccounts это ложные срабатывания.
    known = _known_account_labels(data)
    pool_addrs = set(_KNOWN_LIQUIDITY_SOURCES)
    for addr, lbl in known.items():
        if any(k in lbl for k in ("AMM", "POOL", "LOCKER", "BURN", "VAULT", "DEX")):
            pool_addrs.add(addr)

    for h in (data.get("topHolders") or []):
        if not isinstance(h, dict):
            continue
        owner = str(h.get("owner") or "").strip()
        addr = str(h.get("address") or "").strip()
        if not owner and not addr:
            continue
        if _is_pool_like(owner) or _is_pool_like(addr):
            continue
        if owner in pool_addrs or addr in pool_addrs:
            continue
        try:
            pct = float(h.get("pct", 0) or 0.0)
        except Exception:
            pct = 0.0
        if pct <= 0 or pct > 70:
            continue
        out.append({
            "wallet": owner or addr,
            "token_account": addr,
            "pct": pct,
            "ui_amount": float(h.get("uiAmount", 0) or 0.0),
            "source": "top_holder",
            "insider": bool(h.get("insider", False)),
        })
    out.sort(key=lambda x: x["pct"], reverse=True)
    # Помечаем создателя (дева) среди топ-холдеров — по нему отдельный сигнал риска
    creator = str(data.get("creator") or "").strip()
    if creator:
        for h in out:
            if h["wallet"] == creator:
                h["source"] = "creator"
                h["insider"] = True
                break
    return out


async def _holders_from_rpc(mint: str) -> List[dict]:
    """
    Фоллбэк: getTokenLargestAccounts + getMultipleAccounts(jsonParsed) → owner'ы.
    accounts[0].amount = total supply (raw), остальные — доли.
    """
    try:
        from solders.pubkey import Pubkey
        mint_pk = Pubkey.from_string(mint)
    except Exception:
        return []

    res = await _rpc_batch([("getTokenLargestAccounts", [str(mint_pk), {"limit": 20}])], timeout=8.0)
    accounts = res[0] if res else None
    if not isinstance(accounts, list) or len(accounts) < 2:
        return []

    total_raw = 0
    try:
        total_raw = int(accounts[0]["amount"])
    except Exception:
        total_raw = 0
    if total_raw <= 0:
        return []

    tok_accounts = [str(a.get("address")) for a in accounts[:20] if a.get("address")]
    if not tok_accounts:
        return []

    res2 = await _rpc_batch([
        ("getMultipleAccounts", [tok_accounts, {"encoding": "jsonParsed"}])
    ], timeout=8.0)
    vals = res2[0] if res2 else None
    if not isinstance(vals, list):
        return []

    out: List[dict] = []
    for i, acc in enumerate(vals):
        if not isinstance(acc, dict) or not acc.get("account"):
            continue
        info = (((acc.get("account") or {}).get("data") or {}).get("parsed") or {}).get("info") or {}
        owner = str(info.get("owner") or "").strip()
        if not owner:
            continue
        try:
            amount_raw = int(info.get("tokenAmount", {}).get("amount", 0) or 0)
            ui_amount = float(info.get("tokenAmount", {}).get("uiAmount", 0) or 0.0)
        except Exception:
            amount_raw, ui_amount = 0, 0.0
        pct = (amount_raw / total_raw * 100.0) if total_raw else 0.0
        if pct <= 0 or pct > 70:
            continue
        if _is_pool_like(owner) or owner in _KNOWN_LIQUIDITY_SOURCES:
            continue
        out.append({
            "wallet": owner,
            "token_account": tok_accounts[i] if i < len(tok_accounts) else "",
            "pct": pct,
            "ui_amount": ui_amount,
            "source": "top_holder_rpc",
            "insider": False,
        })
    out.sort(key=lambda x: x["pct"], reverse=True)
    return out


async def _early_buyers_from_trades(pair_address: str) -> List[dict]:
    """Ранние покупатели пула из ленты сделок GeckoTerminal."""
    if not pair_address:
        return []
    clean = str(pair_address).replace("solana_", "").strip()
    if not clean or len(clean) < 32 or clean.startswith("0x"):
        return []
    try:
        import market_data
        resp = await market_data._gt_get(f"/networks/solana/pools/{clean}/trades", retries=0, timeout=5)
    except Exception:
        return []
    trades = (resp or {}).get("data") if isinstance(resp, dict) else None
    if not isinstance(trades, list) or not trades:
        return []

    by_wallet: Dict[str, dict] = {}
    for item in trades:
        attrs = (item.get("attributes") if isinstance(item, dict) and "attributes" in item else item) or {}
        if not isinstance(attrs, dict):
            continue
        if str(attrs.get("kind") or "").lower() != "buy":
            continue
        w = str(attrs.get("tx_from_address") or "").strip()
        if not w or len(w) < 32 or w.startswith("0x") or _is_pool_like(w):
            continue
        usd = float(attrs.get("volume_in_usd") or 0.0)
        rec = by_wallet.setdefault(w, {
            "wallet": w,
            "token_account": "",
            "pct": 0.0,
            "ui_amount": 0.0,
            "source": "early_buyer",
            "insider": False,
            "buy_usd": 0.0,
            "buy_count": 0,
            "first_buy_ts": str(attrs.get("block_timestamp") or ""),
        })
        rec["buy_usd"] += usd
        rec["buy_count"] += 1
    return list(by_wallet.values())


async def build_cohort(mint: str, pair_data: Optional[dict] = None) -> List[dict]:
    """
    Когорта кошельков: топ-холдеры + ранние покупатели.
    Возвращает список dict: wallet, pct, ui_amount, source, ...
    """
    ttl = float(_cfg("FCR_COHORT_CACHE_TTL", 45.0))
    cached = _cache_get(_COHORT_CACHE, mint, ttl)
    if cached is not None:
        return cached

    pair_data = pair_data or {}
    n_holders = int(_cfg("FCR_HOLDER_COUNT", 6))
    n_buyers = int(_cfg("FCR_BUYER_COUNT", 4))

    # RugCheck (топ-холдеры) и лента сделок пула (ранние покупатели) идут ПАРАЛЛЕЛЬНО
    holders_res, buyers = await asyncio.gather(
        _holders_from_rugcheck(mint),
        _early_buyers_from_trades(str(pair_data.get("pairAddress") or "")),
        return_exceptions=True,
    )
    if isinstance(holders_res, Exception):
        holders_res = []
    if isinstance(buyers, Exception):
        buyers = []
    holders = holders_res
    if not holders:
        holders = await _holders_from_rpc(mint)

    cohort: List[dict] = []
    seen = set()
    max_cohort = int(_cfg("FCR_COHORT_SIZE", 8))
    n_top = max(1, n_holders)

    # Дев (создатель) в когорте ВСЕГДА — даже если он не в топе по доле
    ordered = [h for h in holders if str(h.get("source") or "") == "creator"]
    ordered += [h for h in holders if str(h.get("source") or "") != "creator"]

    for h in ordered[:n_top]:
        w = h["wallet"]
        if not w or w in seen:
            continue
        seen.add(w)
        cohort.append(h)

    for b in buyers:
        if len(cohort) >= max_cohort:
            break
        w = b["wallet"]
        if w in seen:
            continue
        seen.add(w)
        cohort.append(b)

    # Не превышаем лимит анализируемых кошельков (каждый = 1-3 RPC-вызова)
    max_cohort = int(_cfg("FCR_COHORT_SIZE", 8))
    if len(cohort) > max_cohort:
        cohort = cohort[:max_cohort]

    _cache_set(_COHORT_CACHE, mint, cohort)
    return cohort


# =============================================================================
# 2. ТРАССИРОВКА ИСТОЧНИКА ФИНАНСИРОВАНИЯ
# =============================================================================

def _tx_full_keys(tx: dict) -> List[dict]:
    """Полный список ключей транзакции (static + loaded), как в balances[]."""
    msg = (tx.get("transaction") or {}).get("message") or {}
    keys: List[dict] = []
    for k in (msg.get("accountKeys") or []):
        if isinstance(k, dict):
            keys.append(k)
        elif isinstance(k, str):
            keys.append({"pubkey": k, "signer": False, "writable": True})
    meta = tx.get("meta") or {}
    loaded = meta.get("loadedAddresses") or {}
    for k in (loaded.get("writable") or []):
        keys.append({"pubkey": str(k), "signer": False, "writable": True})
    for k in (loaded.get("readonly") or []):
        keys.append({"pubkey": str(k), "signer": False, "writable": False})
    return keys


def _find_funder(tx: dict, wallet: str, mint: str) -> Tuple[Optional[str], float]:
    """
    Определяет, кто перевёл SOL на кошелёк, по изменению балансов транзакции.
    Возвращает (funder_address | None, amount_lamports).

    Логика: ищем счёт, который ПОТЕРЯЛ lamport'ы (дебет) и не является программой.
    Приоритет — дебет среди подписантов (fee payer обычно и есть отправитель).
    """
    meta = tx.get("meta") or {}
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    if not pre or not post or len(pre) != len(post):
        return None, 0.0
    keys = _tx_full_keys(tx)
    if len(keys) < len(pre):
        return None, 0.0

    def _pk(i: int) -> str:
        return str(keys[i].get("pubkey") or "")

    def _is_signer(i: int) -> bool:
        return bool(keys[i].get("signer"))

    # Куда именно пришёл SOL (для суммы перевода)
    received = 0.0
    for i in range(len(pre)):
        if _pk(i) == wallet:
            d = int(post[i]) - int(pre[i])
            if d > received:
                received = d

    debtors: List[Tuple[int, float]] = []
    for i in range(len(pre)):
        pk = _pk(i)
        if not pk or pk == wallet:
            continue
        if pk in _PROGRAM_ACCOUNTS or pk == SYSTEM_PROGRAM:
            continue
        if pk == mint:
            continue
        d = int(pre[i]) - int(post[i])
        if d <= 0:
            continue
        debtors.append((i, float(d)))

    if not debtors:
        return None, received

    # 1) дебет среди подписантов
    signers = [(i, d) for (i, d) in debtors if _is_signer(i)]
    pool = signers or debtors
    pool.sort(key=lambda x: x[1], reverse=True)
    funder = _pk(pool[0][0])
    amount = pool[0][1]
    if funder in _PROGRAM_ACCOUNTS:
        return None, received
    # Сумма финансирования = сколько ОТПРАВИЛ плательщик (дебет), а не кредит
    # получателя: у получателя кредит бывает 0 (токены вместо SOL, комиссии).
    return funder, (amount if amount > 0 else received)


def _tx_touches_mint(tx: dict, mint: str) -> bool:
    """Минт упоминается в ключах транзакции (значит кошелёк уже interacting с токеном)."""
    for k in _tx_full_keys(tx):
        if str(k.get("pubkey") or "") == mint:
            return True
    return False


async def _trace_all_funders(wallet_list: List[str], mint: str,
                            priority: Optional[Dict[str, float]] = None) -> Dict[str, dict]:
    """
    Источники финансирования для ВСЕХ кошельков когорты.

    КРИТИЧНО ДЛЯ СКОРОСТИ: всё батчится.
      батч 1 — getSignaturesForAddress по всем кошелькам сразу,
      батч 2 — пагинация genesis (только для кошельков с заметной долей),
      батч 3 — getTransaction по genesis-подписям всех кошельков сразу.
    Итого 2-3 HTTP- round trip вместо 2*N. На публичном RPC это разница
    между 40 секундами и 3-5 секундами (вход в позицию не должен ждать).

    priority: {wallet: pct} — нужен, чтобы пагинировать genesis только у
    существенных холдеров (у остальных точная первая транзакция не нужна).
    """
    infos: Dict[str, dict] = {}
    ttl = float(_cfg("FCR_FUNDER_CACHE_TTL", 21600))
    to_trace: List[str] = []
    for w in wallet_list:
        cached = _cache_get(_FUNDER_CACHE, w, ttl)
        if cached is not None:
            infos[w] = cached
        else:
            to_trace.append(w)
    if not to_trace:
        return infos

    def _blank(wallet: str) -> dict:
        return {
            "wallet": wallet, "funder": None, "amount_sol": 0.0, "funding_ts": 0,
            "tx_count": 0, "genesis_sig": None, "oldest_txs": [], "touched_mint": False,
            "error": None,
        }

    # Лимит страницы подписей: 200 хватает, чтобы найти genesis у «свежих»
    # кошельков (сибил-суб-кошельки), и не кладёт тяжёлый payload на публичный RPC.
    sig_limit = int(_cfg("FCR_SIG_PAGE_LIMIT", 200))
    genesis_min_pct = float(_cfg("FCR_GENESIS_MIN_PCT", 1.0))
    priority = priority or {}

    # --- Батч 1: подписи всех кошельков ---
    res = await _rpc_batch([
        ("getSignaturesForAddress", [w, {"limit": sig_limit}]) for w in to_trace
    ], timeout=6.0)

    page2_needed: List[Tuple[str, str]] = []
    for w, sigs in zip(to_trace, res or []):
        info = _blank(w)
        if not isinstance(sigs, list) or not sigs:
            info["error"] = "no-signatures"
            infos[w] = info
            continue
        info["tx_count"] = len(sigs)
        # Списки RPC отдаются от новых к старым → genesis = последний элемент
        oldest = list(reversed(sigs))
        info["oldest_txs"] = [str(s.get("signature")) for s in oldest[:3] if s.get("signature")]
        genesis_sig = str(oldest[0].get("signature"))
        info["genesis_sig"] = genesis_sig
        # Страница полная → возможно есть более старые транзакции. Догружаем genesis
        # только для существенных холдеров (остальным точная первая tx не нужна).
        if len(sigs) >= sig_limit and float(priority.get(w, 0.0)) >= genesis_min_pct:
            page2_needed.append((w, genesis_sig))
        infos[w] = info

    # --- Батч 2: пагинация для очень активных кошельков (иначе genesis не найден) ---
    if page2_needed:
        res2 = await _rpc_batch([
            ("getSignaturesForAddress", [w, {"limit": 1000, "before": g}]) for w, g in page2_needed
        ], timeout=6.0)
        for (w, _g), page2 in zip(page2_needed, res2 or []):
            if isinstance(page2, list) and page2:
                oldest2 = list(reversed(page2))
                infos[w]["oldest_txs"] = [str(s.get("signature")) for s in oldest2[:3] if s.get("signature")]
                infos[w]["genesis_sig"] = str(oldest2[0].get("signature"))

    # --- Батч 3: genesis-транзакции всех кошельков ---
    tx_wallets = [w for w in to_trace if infos.get(w, {}).get("genesis_sig")]
    if tx_wallets:
        res3 = await _rpc_batch([
            ("getTransaction", [infos[w]["genesis_sig"],
                                {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
            for w in tx_wallets
        ], timeout=6.0)
        for w, tx in zip(tx_wallets, res3 or []):
            info = infos[w]
            if not isinstance(tx, dict) or not tx:
                info["error"] = "no-genesis-tx"
                continue
            meta = tx.get("meta") or {}
            info["funding_ts"] = int(meta.get("blockTime") or 0)
            funder, amount = _find_funder(tx, w, mint)
            info["funder"] = funder
            info["amount_sol"] = round(amount / 1e9, 6)
            info["touched_mint"] = _tx_touches_mint(tx, mint)

    for w in to_trace:
        _cache_set(_FUNDER_CACHE, w, infos.get(w, _blank(w)))
    return infos


async def _funding_of(wallet: str, mint: str) -> dict:
    """Одиночная обёртка над батчевым трейсом (для внешних вызовов)."""
    res = await _trace_all_funders([wallet], mint)
    return res.get(wallet, {"wallet": wallet, "funder": None, "error": "not-traced"})


# =============================================================================
# 3. КЛАСТЕРЫ «ОДНОГО ИСТОЧНИКА»
# =============================================================================

# Кеш инсайдерских сетей RugCheck: mint -> (ts, [cluster...])
_GRAPH_CACHE: Dict[str, Tuple[float, list]] = {}

# Типы узлов RugCheck, которые НЕ являются кошельком-участником
_GRAPH_NODE_SKIP = ("amm", "pool", "vault", "lp", "locker", "burn", "dead")


async def _insider_graph_clusters(mint: str, report: dict = None) -> List[dict]:
    """
    Кластеры «одного источника» по ГРАФУ ИНСАЙДЕРОВ RugCheck.

    RugCheck сам строит сеть связанных кошельков (переводы между собой) и отдаёт:
      net_id, network_type, nodes[{id, participant, holdings}]
    holdings — в raw-единицах токена, поэтому переводим в % через supply из отчёта.

    Это ДЕШЁВЫЙ и точный источник: один HTTP-запрос (кэшируется в goplus),
    в отличие от нашего собственного трейса genesis-транзакций.
    """
    if not mint:
        return []
    now = time.time()
    cached = _GRAPH_CACHE.get(mint)
    if cached and (now - cached[0]) < 120.0:
        return cached[1]

    if report is None:
        report = await _rugcheck_snapshot(mint)

    token = (report or {}).get("token") or {}
    try:
        supply = float(token.get("supply") or 0.0)
        decimals = int(token.get("decimals") or 0)
    except Exception:
        supply, decimals = 0.0, 0
    if supply <= 0:
        _GRAPH_CACHE[mint] = (now, [])
        return []

    labels = _known_account_labels(report)
    creator = str((report or {}).get("creator") or "").strip()

    # Пул/сейфы исключаем по меткам RugCheck — иначе в сеть попадает vault с 90% пула
    pool_addrs = set(_KNOWN_LIQUIDITY_SOURCES)
    for addr, lbl in labels.items():
        if any(k in lbl for k in ("AMM", "POOL", "LOCKER", "BURN", "VAULT", "DEX")):
            pool_addrs.add(addr)

    # Порог «пыли»: узел с долей < FCR_GRAPH_MIN_PCT — не участник фермы
    min_node_pct = float(_cfg("FCR_GRAPH_MIN_PCT", 0.05))

    try:
        from http_client import fetch_json
        st, data = await fetch_json(
            f"https://api.rugcheck.xyz/v1/tokens/{mint}/insiders/graph",
            timeout=6, retries=1)
    except Exception:
        st, data = 0, None

    clusters: List[dict] = []
    if st == 200 and isinstance(data, list):
        for net in data:
            if not isinstance(net, dict):
                continue
            nodes = net.get("nodes") or []
            if len(nodes) < 2:
                continue
            wallets: List[str] = []
            seen_w = set()
            holdings_raw = 0.0
            for nd in nodes:
                if not isinstance(nd, dict):
                    continue
                if nd.get("participant") is False:
                    continue
                nid = str(nd.get("id") or "")
                if not nid or nid in seen_w:
                    continue
                ntype = str(nd.get("type") or "").lower()
                if any(k in ntype for k in _GRAPH_NODE_SKIP):
                    continue
                if nid in pool_addrs or _is_pool_like(nid):
                    continue
                try:
                    hold = float(nd.get("holdings") or 0.0)
                except Exception:
                    hold = 0.0
                if hold <= 0:
                    continue
                node_pct = (hold / supply) * 100.0
                if node_pct < min_node_pct:
                    continue
                seen_w.add(nid)
                holdings_raw += hold
                wallets.append(nid)

            if len(wallets) < 2:
                continue
            total_pct = (holdings_raw / supply) * 100.0
            # >100% = двойной счёт (wallet + его токен-аккаунт) — данным не верим
            if total_pct > 100.0:
                continue
            clusters.append({
                "source": "rugcheck_graph",
                "funder": str(net.get("net_id") or net.get("network_type") or "graph"),
                "funder_label": f"graph:{net.get('network_type') or 'net'}",
                "wallets": wallets,
                "wallet_count": len(wallets),
                "total_pct": round(total_pct, 3),
                "max_pct": 0.0,
                "total_amount_sol": 0.0,
                "funding_spread_sec": 0,
                "funding_burst": False,
                "equal_funding_ratio": 0.0,
                "launch_prep_count": 0,
                "launch_prep_ratio": 0.0,
                "fresh_wallet_count": 0,
                "fresh_ratio": 0.0,
                "min_funding_sol": 0.0,
                "has_creator": any(w == creator for w in wallets),
                "decimals": decimals,
            })

    clusters.sort(key=lambda c: (c["wallet_count"], c["total_pct"]), reverse=True)
    _GRAPH_CACHE[mint] = (now, clusters)
    return clusters


def _known_source_label(funder: str) -> str:
    if funder in _KNOWN_LIQUIDITY_SOURCES:
        return "known-dex"
    low = (funder or "").lower()
    for kw, lbl in (
        ("binance", "binance"), ("coinbase", "coinbase"), ("kraken", "kraken"),
        ("bybit", "bybit"), ("okx", "okx"), ("gate", "gate"), ("mexc", "mexc"),
        ("htx", "htx"), ("crypto", "crypto-com"), ("robinhood", "robinhood"),
        ("strike", "strike"), ("moonpay", "moonpay"), ("transact", "transact"),
        ("kaiko", "kaiko"), ("jupiter", "jupiter"), ("orca", "orca"),
    ):
        if kw in low:
            return lbl
    return "unknown"


async def _deep_first_action_batch(members: List[dict], mint: str) -> None:
    """
    Проверяет, что ПЕРВЫМ действием кошелька была покупка именно этого токена
    (launch-prep ферма: дев профинансировал суб-кошельки под конкретный запуск).
    Все транзакции забираются ОДНИМ батчем. Результат пишется в m["deep"].
    """
    todo = [m for m in members if m.get("deep") is None]
    if not todo:
        return

    calls: List[Tuple[str, list]] = []
    owner_of: List[dict] = []
    max_batch = int(_cfg("FCR_MAX_TX_BATCH", 30))
    for m in todo:
        funding = m.get("funding") or {}
        for s in (funding.get("oldest_txs") or [])[1:3]:
            if not s:
                continue
            if len(calls) >= max_batch:
                break
            calls.append(("getTransaction", [s, {"encoding": "jsonParsed",
                                                  "maxSupportedTransactionVersion": 0}]))
            owner_of.append(m)

    for m in todo:
        m["deep"] = {"first_action_mint": False, "checked": 0}

    if not calls:
        return

    res = await _rpc_batch(calls, timeout=6.0)
    for m, tx in zip(owner_of, res or []):
        if not isinstance(tx, dict) or not tx:
            continue
        m["deep"]["checked"] += 1
        if _tx_touches_mint(tx, mint):
            m["deep"]["first_action_mint"] = True


async def analyze_funding_clusters(mint: str, cohort: List[dict]) -> dict:
    """
    Главный блок: трассирует финансирование когорты и строит кластеры «одного источника».

    Два независимых источника кластеров:
      1) наш трейс genesis-транзакций (общий SOL-источник),
      2) граф инсайдеров RugCheck (связанные переводами кошельки).
    """
    result: Dict[str, Any] = {
        "clusters": [],
        "largest_cluster": None,
        "traced": 0,
        "errors": 0,
        "funder_map": {},
        "wallets": [],
        "graph_clusters": [],
    }
    if not cohort:
        return result

    wallets = list(cohort)[:int(_cfg("FCR_COHORT_SIZE", 8))]

    # 0) Кластеры по графу инсайдеров RugCheck (дёшево, точная доля эмиссии)
    graph_clusters: List[dict] = []
    try:
        graph_clusters = await _insider_graph_clusters(mint)
    except Exception:
        graph_clusters = []
    result["graph_clusters"] = graph_clusters

    # 1) Трассируем финансирование ВСЕХ кошельков когорты одним батчем.
    #    Приоритет пагинации genesis получают холдеры с заметной долей.
    priority = {w["wallet"]: float(w.get("pct", 0.0) or 0.0) for w in wallets}
    funding_map = await _trace_all_funders([w["wallet"] for w in wallets], mint, priority=priority)
    for w in wallets:
        info = funding_map.get(w["wallet"]) or {}
        if info.get("error"):
            result["errors"] += 1
        else:
            result["traced"] += 1
        w["funding"] = info
        result["wallets"].append(w)

    # 2) Группируем по источнику
    # 2) Группируем по источнику
    groups: Dict[str, List[dict]] = defaultdict(list)
    for w in wallets:
        f = (w.get("funding") or {}).get("funder")
        if f:
            groups[f].append(w)
            result["funder_map"][w["wallet"]] = f

    if not groups and not graph_clusters:
        return result

    # 3) Глубокая проверка для кластеров с 2+ кошельками (первое действие = покупка токена)
    deep_enabled = bool(_cfg("FCR_DEEP_FIRST_ACTION", True))
    if deep_enabled:
        deep_members = [m for members in groups.values() if len(members) >= 2 for m in members]
        if deep_members:
            await _deep_first_action_batch(deep_members, mint)

    # 4) Считаем метрики кластеров
    burst_window = float(_cfg("FCR_CLUSTER_BURST_SEC", 3600))

    for f, members in groups.items():
        pcts = [float(m.get("pct", 0.0) or 0.0) for m in members]
        total_pct = sum(pcts)
        ts_list = [int((m.get("funding") or {}).get("funding_ts") or 0) for m in members]
        ts_list = [t for t in ts_list if t > 0]
        spread = (max(ts_list) - min(ts_list)) if len(ts_list) >= 2 else 0
        amounts = [float((m.get("funding") or {}).get("amount_sol", 0.0) or 0.0) for m in members]
        amounts = [a for a in amounts if a > 0]
        # Клонированные переводы: одинаковая сумма (±2%) у большинства
        equal_ratio = 0.0
        if len(amounts) >= 2:
            from collections import Counter
            buckets = Counter(round(a, 2) for a in amounts)
            top_n, _ = buckets.most_common(1)[0]
            equal_ratio = top_n / float(len(amounts))
        launch_prep = sum(
            1 for m in members
            if bool((m.get("funding") or {}).get("touched_mint"))
            or bool((m.get("deep") or {}).get("first_action_mint"))
        )
        fresh = sum(
            1 for m in members
            if int((m.get("funding") or {}).get("tx_count", 0) or 0) <= int(_cfg("FCR_FRESH_TX_MAX", 8))
        )
        cluster = {
            "source": "funding",
            "funder": f,
            "funder_label": _known_source_label(f),
            "wallets": [m["wallet"] for m in members],
            "wallet_count": len(members),
            "total_pct": round(total_pct, 3),
            "max_pct": round(max(pcts) if pcts else 0.0, 3),
            "total_amount_sol": round(sum(amounts), 4),
            "funding_spread_sec": spread,
            "funding_burst": bool(spread and spread <= burst_window),
            "equal_funding_ratio": round(equal_ratio, 3),
            "launch_prep_count": launch_prep,
            "launch_prep_ratio": round(launch_prep / float(len(members)), 3),
            "fresh_wallet_count": fresh,
            "fresh_ratio": round(fresh / float(len(members)), 3),
            "min_funding_sol": round(min(amounts) if amounts else 0.0, 4),
            "has_creator": False,
        }
        result["clusters"].append(cluster)

    # Кластеры графа инсайдеров добавляем к общему списку (источник помечен)
    result["clusters"].extend(graph_clusters)
    result["clusters"].sort(
        key=lambda c: (int(c.get("wallet_count", 0)), float(c.get("total_pct", 0.0))), reverse=True)
    if result["clusters"]:
        result["largest_cluster"] = result["clusters"][0]
    return result


# =============================================================================
# 4. МИКРОСТРУКТУРНЫЕ СИГНАЛЫ РАГА
# =============================================================================

def _pair_age_min(pair_data: dict) -> float:
    """Возраст пула в минутах (pairCreatedAt в мс)."""
    created = pair_data.get("pairCreatedAt") or 0
    try:
        created = float(created or 0)
    except Exception:
        return 0.0
    if created <= 0:
        return 0.0
    if created > 1e12:  # миллисекунды
        created /= 1000.0
    age = (time.time() - created) / 60.0
    return max(0.0, age)


def analyze_microstructure(mint: str, pair_data: dict, cohort: List[dict]) -> dict:
    """Сигналы, что дев уже начал разгружать стек / пул тонкий / монета только что запущена."""
    pair_data = pair_data or {}
    pc = pair_data.get("priceChange") or {}
    m1 = float(pc.get("m1", 0) or 0)
    m5 = float(pc.get("m5", 0) or 0)
    tx5 = (pair_data.get("txns") or {}).get("m5") or {}
    buys = int(tx5.get("buys", 0) or 0)
    sells = int(tx5.get("sells", 0) or 0)
    vol5 = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
    liq = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
    fdv = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
    age_min = _pair_age_min(pair_data)

    top1 = max([float(c.get("pct", 0) or 0) for c in cohort] or [0.0])
    top10 = sum([float(c.get("pct", 0) or 0) for c in cohort[:10]])

    sigs: List[dict] = []
    score = 0.0        # «мягкие» сигналы (рынок) — разбавляются при суммировании
    hard_score = 0.0   # «жёсткие» (концентрация) — идут в total напрямую

    # a) Памп за 5м + мёртвый m1 = дев разгружает стек в толпу
    if (m5 >= float(_cfg("FCR_SPIKE_M5", 90.0)) and m1 <= float(_cfg("FCR_STALL_M1", 3.0))
            and sells >= int(_cfg("FCR_MIN_SELL_COUNT", 120))):
        score += 0.22
        sigs.append({"key": "distribution_stall", "weight": 0.22,
                     "text": f"дистрибуция: m5 +{m1 and m5:.0f}% при мёртвом m1 ({m1:+.1f}%), {sells} продаж/5м"})

    # b) Продавцы давят
    if buys > 0 and sells >= int(_cfg("FCR_MIN_SELL_COUNT", 120)):
        ratio = sells / float(buys)
        if ratio >= float(_cfg("FCR_SELL_DOMINANCE", 0.85)):
            w = min(0.20, 0.12 + (ratio - 0.85) * 0.2)
            score += w
            sigs.append({"key": "sell_dominance", "weight": round(w, 3),
                         "text": f"продажи доминируют: {buys}b/{sells}s (ratio {ratio:.2f})"})

    # c) Тонкий пул (раг стоит дёшево)
    min_liq = float(_cfg("FCR_MIN_LIQ_USD", 10000.0))
    if 0 < liq < min_liq:
        w = 0.16 if liq < min_liq * 0.6 else 0.10
        score += w
        sigs.append({"key": "thin_liquidity", "weight": w,
                     "text": f"тонкий пул: ликвидность ${liq:,.0f} (< ${min_liq:,.0f})"})

    # d) Огромный v/l — буря сделок (флипперы/боты), тонкая реальная глубина
    if liq > 0:
        vol_to_liq = vol5 / liq
        if vol_to_liq >= float(_cfg("FCR_MAX_VOL_TO_LIQ", 8.0)):
            score += 0.08
            sigs.append({"key": "volume_churn", "weight": 0.08,
                         "text": f"v/l = {vol_to_liq:.1f}x — шторм сделок при малой глубине"})

    # e) Монета ещё в окне запуска (дев может дёрнуть ликвидность в любую секунду)
    min_age = float(_cfg("FCR_MIN_AGE_MIN", 1.5))
    if 0 < age_min < min_age:
        score += 0.10
        sigs.append({"key": "too_young", "weight": 0.10,
                     "text": f"монета молодая: {age_min:.1f} мин (< {min_age:.1f}) — дев ещё может дёрнуть"})

    # f) FDV >> ликвидности
    if fdv > 0 and liq > 0:
        ratio = fdv / liq
        if ratio >= 8.0:
            score += 0.08
            sigs.append({"key": "fdv_to_liq", "weight": 0.08,
                         "text": f"FDV/лик ${ratio:.1f}x — запас хода вниз огромный"})

    # g) Концентрация одного холдера (чем больше — тем опаснее)
    if top1 >= 18.0:
        if top1 >= 35.0:
            w = 0.55
        elif top1 >= 25.0:
            w = 0.40
        else:
            w = 0.20
        hard_score = max(hard_score, w)
        sigs.append({"key": "single_holder", "weight": w,
                     "text": f"один кошелёк держит {top1:.1f}% эмиссии"})

    # h) ДЕВ ещё держит стек — самый прямой предвестник рага первой свечи
    dev_pct = 0.0
    for c in cohort:
        if str(c.get("source") or "") == "creator":
            dev_pct = max(dev_pct, float(c.get("pct", 0.0) or 0.0))
    if dev_pct >= 8.0:
        if dev_pct >= 25.0:
            w = 0.60
        elif dev_pct >= 15.0:
            w = 0.45
        else:
            w = 0.25
        hard_score = max(hard_score, w)
        sigs.append({"key": "dev_loaded", "weight": w,
                     "text": f"дев (создатель) держит {dev_pct:.1f}% эмиссии — может продать на первой свече"})

    return {
        "signals": sigs,
        "score": round(min(score, 1.0), 3),
        "hard_score": round(min(hard_score, 1.0), 3),
        "metrics": {
            "m1": m1, "m5": m5, "buys_m5": buys, "sells_m5": sells,
            "volume_m5": vol5, "liquidity": liq, "fdv": fdv,
            "age_min": round(age_min, 2), "top1_pct": round(top1, 2),
            "top10_pct": round(top10, 2),
        },
    }


# =============================================================================
# 5. ВЕРДИКТ
# =============================================================================

def _score_clusters(clusters: List[dict]) -> Tuple[float, List[dict]]:
    """
    Считает риск-балл кластеров «одного источника» и собирает причины.

    Кластеры приходят из двух независимых источников:
      - source="funding"  — наш трейс: кошельки профинансированы С ОДНОГО SOL-адреса;
      - source="rugcheck_graph" — граф инсайдеров RugCheck: кошельки связаны переводами.
    Оба описывают пересекающиеся множества кошельков, поэтому берём МАКСИМУМ
    (иначе один и тот же риск посчитается дважды), а при пересечении добавляем бонус —
    это самое сильное подтверждение сибил-фермы.
    """
    min_wallets = int(_cfg("FCR_MIN_CLUSTER_WALLETS", 3))
    min_pct = float(_cfg("FCR_MIN_CLUSTER_PCT", 8.0))
    warn_pct = float(_cfg("FCR_MIN_CLUSTER_PCT_WARN", 4.0))

    reasons: List[dict] = []
    fund_score = 0.0
    graph_score = 0.0
    fund_clusters: List[dict] = []
    graph_clusters: List[dict] = []

    for c in clusters:
        n = int(c.get("wallet_count", 0))
        pct = float(c.get("total_pct", 0.0))
        label = str(c.get("funder_label") or "unknown")
        is_graph = str(c.get("source") or "") == "rugcheck_graph"

        if not is_graph:
            # Источник — DEX/агрегатор: «общий SOL-источник» ничего не значит
            if label == "known-dex":
                continue
            if c.get("funder") in set(_cfg("FCR_TRUSTED_FUNDERS", []) or []):
                continue

        spread = int(c.get("funding_spread_sec", 0) or 0)
        fresh_ratio = float(c.get("fresh_ratio", 0.0))
        burst = bool(c.get("funding_burst"))
        equal_ratio = float(c.get("equal_funding_ratio", 0.0))

        # Скидка за «честных людей с общим кошельком» (биржа/кастодиан):
        #  - известная биржа по метке,
        #  - адрес из FCR_TRUSTED_FUNDERS,
        #  - либо кошельки НЕ одноразовые и профинансированы НЕ бурстом
        #    (биржа выводит SOL людям за недели, а не за 5 минут).
        discount = 1.0
        if is_graph:
            discount = 1.0
            # Огромная сеть (сотни кошельков) — это не ферма, а обычный граф переводов
            # толпы. Ферма — это МАЛО кошельков с БОЛЬШИМ стеком.
            max_graph_wallets = int(_cfg("FCR_GRAPH_MAX_WALLETS", 15))
            if n > max_graph_wallets:
                discount = 0.3
            if n > 60:
                continue
        elif label in _EXCHANGE_LABELS:
            discount = 0.45
        elif not burst and fresh_ratio < 0.34:
            # Не бурст + не одноразовые: скорее всего честные люди с общим кошельком
            # (вывод с биржи). Сигнал слабее, но не обнуляем.
            discount = 0.75

        if n >= min_wallets and pct >= min_pct:
            w = 0.45
            w += min(0.18, (n - min_wallets) * 0.06)       # каждый кошелёк сверх порога
            w += min(0.15, max(0.0, pct - min_pct) * 0.02)  # каждый % сверх порога
            w *= discount
            if is_graph:
                reasons.append({
                    "key": "insider_network",
                    "weight": round(w, 3),
                    "text": (f"инсайдерская сеть RugCheck: {n} связанных кошельков "
                             f"держат {pct:.1f}% эмиссии"),
                })
            else:
                reasons.append({
                    "key": "sybil_cluster",
                    "weight": round(w, 3),
                    "text": (f"сибил-кластер: {n} кошельков с ОДНОГО источника финансирования "
                             f"({c['funder'][:8]}…) держат {pct:.1f}% эмиссии"),
                })
        elif n >= 2 and pct >= warn_pct:
            w = 0.34 * discount
            if is_graph:
                reasons.append({
                    "key": "insider_network_warn",
                    "weight": round(w, 3),
                    "text": f"инсайдерская сеть: {n} кошельков на {pct:.1f}% эмиссии",
                })
            else:
                reasons.append({
                    "key": "sybil_cluster_warn",
                    "weight": round(w, 3),
                    "text": (f"кластер «одного источника»: {n} кошельков от {c['funder'][:8]}… "
                             f"({label}) на {pct:.1f}% эмиссии"),
                })
        else:
            continue

        # Усилители сигнала внутри подозрительного кластера.
        # Бурст раздачи + одинаковые суммы = признак раздачи дева, а не биржи.
        if burst:
            w += 0.08 * discount
            reasons.append({"key": "funding_burst", "weight": round(0.08 * discount, 3),
                            "text": f"раздача SOL за {spread}с (бурст) от {c['funder'][:8]}…"})
        if equal_ratio >= 0.6 and n >= 3:
            w += 0.08 * discount
            reasons.append({"key": "cloned_funding", "weight": round(0.08 * discount, 3),
                            "text": f"клонированные переводы: {int(equal_ratio*n)}/{n} кошельков получили одинаковую сумму"})
        if float(c.get("launch_prep_ratio", 0.0)) >= 0.5 and n >= 2:
            w += 0.12 * discount
            reasons.append({"key": "launch_prep", "weight": round(0.12 * discount, 3),
                            "text": f"{c['launch_prep_count']}/{n} кошельков первым делом купили ЭТОТ токен (launch-prep ферма)"})
        if fresh_ratio >= 0.6 and n >= 3:
            w += 0.05 * discount
            reasons.append({"key": "fresh_wallets", "weight": round(0.05 * discount, 3),
                            "text": f"{c['fresh_wallet_count']}/{n} кошельков в кластере — одноразовые (<= {int(_cfg('FCR_FRESH_TX_MAX', 8))} tx)"})
        if is_graph and c.get("has_creator"):
            w += 0.15
            reasons.append({"key": "creator_in_network", "weight": 0.15,
                            "text": "в инсайдерскую сеть входит сам создатель (дев) токена"})

        if is_graph:
            graph_score += w
            graph_clusters.append(c)
        else:
            fund_score += w
            fund_clusters.append(c)

    # Пересечение двух источников = самое сильное подтверждение фермы
    overlap_bonus = 0.0
    for gc in graph_clusters:
        gset = set(gc.get("wallets") or [])
        for fc in fund_clusters:
            shared = gset & set(fc.get("wallets") or [])
            if len(shared) >= 2:
                overlap_bonus = max(overlap_bonus, 0.15)
                reasons.append({
                    "key": "cluster_cross_confirmed",
                    "weight": 0.15,
                    "text": (f"{len(shared)} кошельков подтверждены сразу двумя способами: "
                             f"общий SOL-источник И связь переводами (сеть инсайдеров)"),
                })

    score = max(fund_score, graph_score) + overlap_bonus
    return min(score, 1.0), reasons


def _format_cluster_line(c: dict) -> str:
    if str(c.get("source") or "") == "rugcheck_graph":
        return (f"{c['wallet_count']} кошельков · {c['total_pct']:.1f}% эмиссии · "
                f"сеть инсайдеров RugCheck ({c.get('funder_label', '')})")
    return (f"{c['wallet_count']} кошельков · {c['total_pct']:.1f}% эмиссии · "
            f"{c['total_amount_sol']:.2f} SOL · источник {c['funder'][:8]}… ({c['funder_label']})")


async def pre_entry_rug_check(
    mint: str,
    pair_data: Optional[dict] = None,
    symbol: str = "",
    use_cache: bool = True,
) -> dict:
    """
    ПОЛНЫЙ предвходовой анализ на мгновенный раг первой свечи.

    Возвращает:
      {
        "mint", "symbol",
        "safe": bool,          # True = вход разрешён
        "verdict": "OK"|"WARN"|"BLOCK",
        "score": float,        # 0..1
        "reasons": [str],
        "signals": [dict],
        "clusters": [dict],
        "wallet_analysis": {...},
        "metrics": {...},
        "degraded": bool,      # True = данных не хватило (fail-open)
        "report": str,         # человекочитаемый отчёт
      }
    """
    if not mint:
        return _empty_verdict(mint, symbol, "no-mint")

    if use_cache:
        cached = _cache_get(_VERDICT_CACHE, mint, float(_cfg("FCR_AUDIT_CACHE_TTL", 45.0)))
        if cached is not None:
            return cached

    verdict = {
        "mint": mint,
        "symbol": symbol or mint[:8],
        "safe": True,
        "verdict": "OK",
        "score": 0.0,
        "reasons": [],
        "signals": [],
        "clusters": [],
        "wallet_analysis": {},
        "metrics": {},
        "degraded": False,
        "report": "",
    }

    try:
        # Жёсткий дедлайн: анализ не имеет права задерживать вход в позицию.
        # Не успели — fail-open (вход разрешён, degraded=True).
        budget = float(_cfg("FCR_MAX_DURATION_SEC", 14.0))
        verdict = await asyncio.wait_for(_analyze(mint, pair_data, verdict), timeout=budget)
    except asyncio.TimeoutError:
        verdict["degraded"] = True
        verdict["safe"] = True
        verdict["verdict"] = "OK"
        verdict["score"] = 0.0
        verdict["reasons"] = [f"анализ не уложился в {float(_cfg('FCR_MAX_DURATION_SEC', 14.0)):.0f}с — вход разрешён (fail-open)"]
        verdict["report"] = format_report(verdict)
    except Exception as e:  # fail-open: сеть/баг не должны блокировать вход
        verdict["degraded"] = True
        verdict["safe"] = True
        verdict["verdict"] = "OK"
        verdict["score"] = 0.0
        verdict["reasons"] = [f"анализ недоступен ({type(e).__name__}) — вход разрешён"]
        verdict["report"] = format_report(verdict)

    _cache_set(_VERDICT_CACHE, mint, verdict)
    return verdict


async def _analyze(mint: str, pair_data: Optional[dict], verdict: dict) -> dict:
    """Основная часть анализа (обёрнута в дедлайн в pre_entry_rug_check)."""
    pair_data = pair_data or {}
    # 1. Когорта
    cohort = await build_cohort(mint, pair_data)
    verdict["wallet_analysis"]["cohort_size"] = len(cohort)

    # 2. Финансирование + кластеры
    cluster_info = await analyze_funding_clusters(mint, cohort)
    verdict["clusters"] = cluster_info.get("clusters", [])
    verdict["wallet_analysis"].update({
        "traced": cluster_info.get("traced", 0),
        "errors": cluster_info.get("errors", 0),
        "cluster_count": len(verdict["clusters"]),
        "graph_cluster_count": len(cluster_info.get("graph_clusters") or []),
        "largest_cluster": cluster_info.get("largest_cluster"),
        "funder_map": cluster_info.get("funder_map", {}),
    })

    # 3. Микроструктура
    micro = analyze_microstructure(mint, pair_data, cohort)
    verdict["metrics"] = micro.get("metrics", {})
    verdict["signals"] = micro.get("signals", [])

    # 4. Скоринг: кластеры кошельков — главный сигнал, микроструктура — усилитель,
    #    концентрация (дев/кит) — жёсткий сигнал, идёт напрямую.
    cluster_score, cluster_reasons = _score_clusters(verdict["clusters"])
    micro_score = float(micro.get("score", 0.0))
    hard_score = float(micro.get("hard_score", 0.0))
    total = min(1.0, cluster_score + micro_score * 0.55 + hard_score)

    reasons = [r["text"] for r in cluster_reasons]
    reasons += [s["text"] for s in verdict["signals"]]

    verdict["score"] = round(total, 3)
    verdict["reasons"] = reasons
    verdict["wallet_analysis"]["cluster_score"] = round(cluster_score, 3)
    verdict["wallet_analysis"]["micro_score"] = round(micro_score, 3)
    verdict["wallet_analysis"]["hard_score"] = round(hard_score, 3)

    # 5. Вердикт
    block_at = float(_cfg("FCR_BLOCK_SCORE", 0.55))
    warn_at = float(_cfg("FCR_WARN_SCORE", 0.32))
    if total >= block_at:
        verdict["verdict"] = "BLOCK"
        verdict["safe"] = False
    elif total >= warn_at:
        verdict["verdict"] = "WARN"
        verdict["safe"] = True
    else:
        verdict["verdict"] = "OK"
        verdict["safe"] = True

    # Данных не хватило? Помечаем degraded, но НЕ блокируем.
    if not cohort or (cluster_info.get("traced", 0) == 0 and not verdict["clusters"]):
        verdict["degraded"] = True

    verdict["report"] = format_report(verdict)
    return verdict


def _empty_verdict(mint: str, symbol: str, reason: str) -> dict:
    v = {
        "mint": mint, "symbol": symbol or mint[:8], "safe": True, "verdict": "OK",
        "score": 0.0, "reasons": [reason], "signals": [], "clusters": [],
        "wallet_analysis": {}, "metrics": {}, "degraded": True, "report": "",
    }
    v["report"] = format_report(v)
    return v


async def quick_first_candle_check(
    mint: str,
    pair_data: Optional[dict] = None,
    symbol: str = "",
) -> Tuple[bool, str]:
    """
    Быстрый fail-open вариант для точки входа.
    Возвращает (safe: bool, reason: str).
    safe=False только при явном BLOCK-паттерне.
    """
    if not bool(_cfg("FCR_ENABLED", True)):
        return True, "fcr-disabled"

    verdict = await pre_entry_rug_check(mint, pair_data, symbol)
    if verdict.get("verdict") == "BLOCK":
        return False, "FCR-BLOCK: " + "; ".join(verdict.get("reasons", [])[:4])
    if verdict.get("verdict") == "WARN":
        return True, "FCR-WARN: " + "; ".join(verdict.get("reasons", [])[:3])
    return True, "FCR-OK"


def compact_summary(verdict: dict) -> dict:
    """
    Компактная сводка вердикта для сохранения в позиции (ml_features / лог сделок).
    Позволяет потом разобрать, ПОЧЕМУ вход был разрешён или заблокирован.
    """
    if not verdict:
        return {}
    clusters = verdict.get("clusters") or []
    top = clusters[0] if clusters else {}
    return {
        "fcr_verdict": str(verdict.get("verdict") or "OK"),
        "fcr_score": round(float(verdict.get("score", 0.0) or 0.0), 3),
        "fcr_cluster_wallets": int(top.get("wallet_count", 0) or 0),
        "fcr_cluster_pct": round(float(top.get("total_pct", 0.0) or 0.0), 2),
        "fcr_cluster_source": str(top.get("funder_label") or "")[:40],
        "fcr_cohort": int((verdict.get("wallet_analysis") or {}).get("cohort_size", 0) or 0),
        "fcr_reasons": "; ".join((verdict.get("reasons") or [])[:3])[:300],
    }


def format_report(verdict: dict) -> str:
    """Человекочитаемый отчёт для консоли/дашборда."""
    sym = verdict.get("symbol") or verdict.get("mint", "")[:8]
    v = verdict.get("verdict", "OK")
    icon = {"BLOCK": "🚫", "WARN": "⚠️", "OK": "✅"}.get(v, "•")
    lines = [f"{icon} [FCR] {sym}: {v} (score {float(verdict.get('score', 0.0)):.2f})"]

    clusters = verdict.get("clusters") or []
    if clusters:
        lines.append("   Кластеры «одного источника»:")
        for c in clusters[:4]:
            lines.append(f"   • {_format_cluster_line(c)}")
            if c.get("funding_burst"):
                lines.append(f"       └ бурст раздачи за {c['funding_spread_sec']}с")
            if c.get("launch_prep_count"):
                lines.append(f"       └ launch-prep: {c['launch_prep_count']}/{c['wallet_count']} первым делом купили этот токен")
            if c.get("has_creator"):
                lines.append(f"       └ в сеть входит создатель (дев)")
    else:
        wa = verdict.get("wallet_analysis") or {}
        lines.append(f"   Когорта: {wa.get('cohort_size', 0)} кошельков, "
                     f"прослежено {wa.get('traced', 0)}, кластеров не найдено")

    metrics = verdict.get("metrics") or {}
    if metrics:
        lines.append(
            f"   Рынок: liq ${float(metrics.get('liquidity', 0)):,.0f} · "
            f"m5 {float(metrics.get('m5', 0)):+.1f}% · m1 {float(metrics.get('m1', 0)):+.1f}% · "
            f"{int(metrics.get('buys_m5', 0))}b/{int(metrics.get('sells_m5', 0))}s · "
            f"возраст {float(metrics.get('age_min', 0)):.0f}м"
        )

    for r in (verdict.get("reasons") or [])[:6]:
        lines.append(f"   ↳ {r}")
    if verdict.get("degraded"):
        lines.append("   ↳ ⚠️ данных не хватило — fail-open (вход разрешён)")
    return "\n".join(lines)


# =============================================================================
# CLI: python3 first_candle_rug_guard.py <mint> [symbol]
# =============================================================================

async def _cli(mint: str, symbol: str = "") -> None:
    pair_data = None
    try:
        import market_data
        pair_data = await market_data.get_token_data(mint)
    except Exception as e:
        print(f"⚠️ не удалось получить данные пула: {type(e).__name__}")
    verdict = await pre_entry_rug_check(mint, pair_data, symbol)
    print(verdict.get("report", ""))
    print(f"\nJSON: {json.dumps({k: v for k, v in verdict.items() if k != 'report'}, ensure_ascii=False, default=str)}")
    try:
        from http_client import close_session
        await close_session()
    except Exception:
        pass


def main() -> None:
    import sys
    if len(sys.argv) < 2:
        print("Использование: python3 first_candle_rug_guard.py <mint> [symbol]")
        return
    mint = sys.argv[1].strip()
    symbol = sys.argv[2].strip() if len(sys.argv) > 2 else ""
    asyncio.run(_cli(mint, symbol))


if __name__ == "__main__":
    main()
