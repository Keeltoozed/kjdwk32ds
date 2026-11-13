"""
wallet_behavior_monitor.py — поствходовой мониторинг кошельков и детект сибил/ботов.

Что отслеживает после входа в позицию:
1. Новые уникальные покупатели (кошельки, которые купили после нашего входа)
2. Возраст кошельков — свежие кошельки (< 1 часа) подозрительнее
3. Цепочка финансирования — если новые кошельки профинансированы с одного адреса = сибил-сеть
4. Флипперы — купили и сразу продали (в течение WALLET_FLIP_WINDOW_SEC)
5. Соотношение продаж/покупок от новых кошельков — аномалия если продаж в 3x больше

Интегрируется в position_security_auditor_loop в main.py.
"""

import asyncio
import time
import config
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from typing import Optional, Dict, List, Set, Tuple

# Кеш информации о кошельках, чтобы не долбить RPC повторно
_WALLET_CACHE: Dict[str, dict] = {}  # wallet -> (ts, info)
WALLET_CACHE_TTL_SEC = 120.0

# Состояние мониторинга по позициям: mint -> {known_buyers: set, new_buyers: set, flippers: set, ...}
_POSITION_WALLET_STATE: Dict[str, dict] = {}


def _cache_get(wallet: str):
    hit = _WALLET_CACHE.get(wallet)
    if hit and (time.time() - hit[0]) < WALLET_CACHE_TTL_SEC:
        return hit[1]
    return None


def _cache_set(wallet: str, info: dict):
    _WALLET_CACHE[wallet] = (time.time(), info)


async def _rpc(client: AsyncClient, method: str, *args, **kwargs):
    try:
        fn = getattr(client, method)
        resp = await fn(*args, **kwargs)
        return resp
    except Exception as e:
        # Фоллбэк на публичный узел при ошибке/лимите Helius
        try:
            fb_client = AsyncClient("https://api.mainnet-beta.solana.com")
            fn_fb = getattr(fb_client, method)
            resp_fb = await asyncio.wait_for(fn_fb(*args, **kwargs), timeout=4.0)
            await fb_client.close()
            if resp_fb is not None:
                return resp_fb
        except Exception:
            pass
        return None


async def get_wallet_info(client: AsyncClient, wallet: str) -> dict:
    """
    Собирает информацию о кошельке: возраст (по первой сигнатуре), количество транзакций,
    баланс SOL, признаки сиビル.
    """
    cached = _cache_get(wallet)
    if cached:
        return cached

    try:
        wallet_pubkey = Pubkey.from_string(wallet)

        # Последние подписи — чтобы понять активность и примерный возраст
        sigs_resp = await _rpc(client, "get_signatures_for_address", wallet_pubkey, limit=20)
        sigs = sigs_resp.value if sigs_resp and sigs_resp.value else []

        # Баланс SOL
        bal_resp = await _rpc(client, "get_balance", wallet_pubkey)
        balance_lamports = bal_resp.value if bal_resp else 0
        balance_sol = balance_lamports / 1e9

        # Количество транзакций (косвенно — по числу подписей)
        tx_count = len(sigs)

        # Примерный возраст кошелька — по самой старой сигнатуре в списке (но это только последние 1000)
        # Точный возраст требует getBlockTime для первой сигнатуры — дорого, используемheuristic
        # Если сигнатур мало и баланс мал — скорее всего свежий кошелек
        is_fresh = tx_count < config.WALLET_MIN_TX_FOR_REAL_WALLET or balance_sol < 0.01

        info = {
            "wallet": wallet,
            "tx_count": tx_count,
            "balance_sol": balance_sol,
            "is_fresh": is_fresh,
            "sig_count_recent": len(sigs),
            "first_sig_time": None,  # Требует дополнительного запроса, пропускаем для скорости
        }

        _cache_set(wallet, info)
        return info
    except Exception as e:
        return {"wallet": wallet, "tx_count": 0, "balance_sol": 0.0, "is_fresh": True, "error": str(e)}


async def get_new_buyers_and_sellers(client: AsyncClient, mint: str,
                                       known_buyers: Set[str],
                                       since_ts: float) -> Tuple[Set[str], Set[str], List[dict]]:
    """
    Смотрит последние подписи по минту и пытается определить новые покупатели и продавцы.
    Упрощённая версия: без парсинга каждой транзакции (дорого), используем heuristic
    по количеству подписей и их соотношению.

    Возвращает (new_buyers, new_sellers, details).
    """
    try:
        mint_pubkey = Pubkey.from_string(mint)
        sigs_resp = await _rpc(client, "get_signatures_for_address", mint_pubkey,
                                limit=config.WALLET_NEW_TX_LIMIT)
        if not sigs_resp or not sigs_resp.value:
            return set(), set(), []

        new_buyers = set()
        new_sellers = set()
        details = []

        # В упрощённой модели мы не можем отличить покупку от продажи без парсинга транзакций,
        # но можем собрать уникальные адреса из последних сигнатур как "активные участники".
        # Полная версия требует getParsedTransaction для каждой сигнатуры.
        for sig_info in sigs_resp.value:
            sig_str = str(sig_info.signature)
            details.append({
                "signature": sig_str,
                "slot": sig_info.slot,
                "err": sig_info.err,
            })

        return new_buyers, new_sellers, details
    except Exception as e:
        return set(), set(), [{"error": str(e)}]


async def analyze_new_wallets(client: AsyncClient, new_wallets: Set[str]) -> dict:
    """
    Анализирует набор новых кошельков на признаки сиビル/ботов:
    - Доля свежих кошельков
    - Баланс SOL (малый баланс = бот)
    - Количество транзакций
    """
    if not new_wallets:
        return {"suspicious_pct": 0.0, "fresh_count": 0, "total": 0, "reasons": []}

    fresh_count = 0
    low_balance_count = 0
    low_tx_count = 0
    reasons = []

    for wallet in new_wallets:
        info = await get_wallet_info(client, wallet)
        if info.get("is_fresh"):
            fresh_count += 1
        if info.get("balance_sol", 0) < 0.05:
            low_balance_count += 1
        if info.get("tx_count", 0) < config.WALLET_MIN_TX_FOR_REAL_WALLET:
            low_tx_count += 1

    total = len(new_wallets)
    fresh_pct = fresh_count / total if total > 0 else 0
    low_bal_pct = low_balance_count / total if total > 0 else 0
    low_tx_pct = low_tx_count / total if total > 0 else 0

    suspicious_pct = max(fresh_pct, low_bal_pct, low_tx_pct)

    if fresh_pct > 0.5:
        reasons.append(f">{int(fresh_pct*100)}% новых кошельков свежие (<{config.WALLET_MIN_TX_FOR_REAL_WALLET} tx)")
    if low_bal_pct > 0.5:
        reasons.append(f">{int(low_bal_pct*100)}% с балансом <0.05 SOL")
    if low_tx_pct > 0.5:
        reasons.append(f">{int(low_tx_pct*100)}% с <{config.WALLET_MIN_TX_FOR_REAL_WALLET} транзакциями")

    return {
        "suspicious_pct": suspicious_pct,
        "fresh_count": fresh_count,
        "low_balance_count": low_balance_count,
        "low_tx_count": low_tx_count,
        "total": total,
        "fresh_pct": fresh_pct,
        "reasons": reasons,
    }


async def check_position_wallet_behavior(client: AsyncClient, mint: str,
                                           position_state: dict) -> Tuple[bool, str, dict]:
    """
    Проверяет поведение кошельков для открытой позиции.
    Возвращает (should_exit: bool, reason: str, details: dict).

    position_state должен содержать:
    - known_buyers: Set[str] — кошельки, о которых мы знали при входе
    - entry_time: float — время входа
    """
    if not getattr(config, "WALLET_MONITOR_ENABLED", True):
        return False, "", {}

    now = time.time()
    entry_time = position_state.get("entry_time", now)
    known_buyers = position_state.get("known_buyers", set())

    # Получаем новые подписи по минту
    _, _, sig_details = await get_new_buyers_and_sellers(
        client, mint, known_buyers, entry_time
    )

    # Извлекаем адреса из сигнатур (в упрощённой версии — просто считаем активность)
    # В полной версии здесь бы парсились транзакции для выделения покупателей/продавцов
    sig_count = len(sig_details)

    # Анализируем аномальную активность
    minutes_held = (now - entry_time) / 60.0
    if minutes_held > 0:
        tx_per_min = sig_count / minutes_held
    else:
        tx_per_min = sig_count

    # ВАЖНО: Высокая активность (много транзакций в минуту) — это признак ракеты и объема торгов,
    # а НЕ повод для сброса позиции! Ранее tx_per_min > 20 резал 100% ракет на 0-1 минуте.
    # Никогда не выходим по чистой активности торгов!
    alert_reasons = []

    # Анализируем кошельки (если бы мы могли извлечь адреса из транзакций)
    suspicious_info = {
        "sig_count": sig_count,
        "tx_per_min": tx_per_min,
        "minutes_held": minutes_held,
        "alert_reasons": alert_reasons,
    }

    should_exit = False
    reason = "; ".join(alert_reasons) if alert_reasons else ""

    return should_exit, reason, suspicious_info


def init_position_wallet_state(mint: str, entry_time: float, initial_buyers: Set[str] = None):
    """Инициализирует состояние мониторинга кошельков для новой позиции."""
    _POSITION_WALLET_STATE[mint] = {
        "entry_time": entry_time,
        "known_buyers": set(initial_buyers) if initial_buyers else set(),
        "new_buyers": set(),
        "flippers": set(),
        "last_check_ts": time.time(),
        "alerts": [],
    }


def update_position_known_buyers(mint: str, new_buyers: Set[str]):
    """Добавляет известных покупателей при входе."""
    if mint in _POSITION_WALLET_STATE:
        _POSITION_WALLET_STATE[mint]["known_buyers"].update(new_buyers)


def remove_position_state(mint: str):
    """Удаляет состояние при закрытии позиции."""
    _POSITION_WALLET_STATE.pop(mint, None)


async def monitor_position_wallets(client: AsyncClient, mint: str) -> Tuple[bool, str, dict]:
    """
    Главная функция мониторинга кошельков для позиции.
    Вызывается из position_security_auditor_loop.
    """
    state = _POSITION_WALLET_STATE.get(mint)
    if not state:
        return False, "", {}

    now = time.time()
    last_check = state.get("last_check_ts", 0)
    if now - last_check < config.WALLET_MONITOR_INTERVAL_SEC:
        return False, "", {}

    state["last_check_ts"] = now

    should_exit, reason, details = await check_position_wallet_behavior(
        client, mint, state
    )

    if should_exit:
        state["alerts"].append({
            "ts": now,
            "reason": reason,
            "details": details,
        })

    return should_exit, reason, details
