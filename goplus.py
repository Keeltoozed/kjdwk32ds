"""GoPlus Security API — общедоступная модель риска токена.

Бесплатно, без API-ключа. Ловит то, от чего умирают сделки: honeypot'ы
(продать нельзя в принципе) и грабительские налоги (sell tax 20-99% =
мгновенные -30% на выходе). Свои гейты это не видят: Mint Authority может
быть чистой, а налог зашит в transfer-логику.

Покрытие:
- Solana: отдельный endpoint /api/v1/solana/token_security
- EVM: ethereum (1), bsc (56), base (8453)
- Robinhood Chain (4663) GoPlus не знает — скип (fail-open).

Квоты: free щадим — все запросы через fetch_json (семафор + троттлинг),
плюс свой кэш вердиктов (дефолт 1ч): финалистов на покупку единицы в час.
При недоступности API — fail-open (вход разрешён, в лог): лучше пропустить
проверку, чем встать всем ботом.
"""
import time as _t
import config

EVM_CHAIN_IDS = {"ethereum": "1", "bsc": "56", "base": "8453", "robinhood": "4663"}

_CACHE: dict = {}  # (chain, addr_lower) -> (ts, (ok, reason))
CALLS = {"hit": 0, "miss": 0, "block": 0}


def _cfg(name, default):
    try:
        import config as _c
        return getattr(_c, name, default)
    except Exception:
        return default


def _tax_frac(raw) -> float:
    """Налог к доле: GoPlus отдаёт долю ('0.05' = 5%), но страхуемся —
    значение >1 трактуем как проценты."""
    try:
        v = float(raw or 0)
    except Exception:
        return 0.0
    if v > 1.0:
        v /= 100.0
    return max(0.0, v)


def _verdict_evm(info: dict, chain: str = "", pair_data: dict = None):
    """Разбор EVM-карточки. Возвращает (ok, reason)."""
    if not isinstance(info, dict) or not info:
        return False, "no-data"
    # 1. Honeypot & прямые ограничения
    if str(info.get("is_honeypot", "0")) == "1":
        return False, "honeypot"
    if str(info.get("cannot_sell_all", "0")) == "1":
        return False, "cannot_sell_all"
    if str(info.get("cannot_buy", "0")) == "1":
        return False, "cannot_buy"
    if str(info.get("is_blacklisted", "0")) == "1":
        return False, "blacklist (черный список / блокировка кошельков)"
    if str(info.get("transfer_pausable", "0")) == "1":
        return False, "transfer_pausable (заморозка / пауза переводов владельцем)"
    if str(info.get("is_whitelisted", "0")) == "1":
        return False, "whitelist (белый список — продажа запрещена покупателям)"
    if str(info.get("trading_cooldown", "0")) == "1":
        return False, "trading_cooldown"

    # 2. Бэкдоры владельца, бесконечный минт и манипуляции лимитами
    if str(info.get("is_mintable", "0")) == "1":
        return False, "mintable"
    if str(info.get("can_take_back_ownership", "0")) == "1":
        return False, "take_back_ownership"
    if str(info.get("hidden_owner", "0")) == "1":
        return False, "hidden_owner"
    if str(info.get("selfdestruct", "0")) == "1":
        return False, "selfdestruct"
    if str(info.get("external_call", "0")) == "1":
        return False, "external_call"
    if str(info.get("anti_whale_modifiable", "0")) == "1":
        return False, "anti_whale_modifiable (риск обнуления лимита продажи девом)"
    if str(info.get("slippage_modifiable", "0")) == "1":
        return False, "slippage_modifiable (риск установки 100% налога девом)"
    if str(info.get("personal_slippage_modifiable", "0")) == "1":
        return False, "personal_slippage_modifiable (персональный налог на покупателей)"
    if str(info.get("is_open_source", "1")) == "0":
        return False, "closed_source (закрытый код контракта — невозможно проверить на скрытую заморозку)"

    # 3. Налоги
    buy = _tax_frac(info.get("buy_tax", 0))
    sell = _tax_frac(info.get("sell_tax", 0))
    if buy > float(_cfg("GOPLUS_MAX_BUY_TAX", 0.10)):
        return False, f"buy_tax {buy * 100:.0f}%"
    if sell > float(_cfg("GOPLUS_MAX_SELL_TAX", 0.10)):
        return False, f"sell_tax {sell * 100:.0f}%"
    worst, worst_k = 0.0, ""
    for k, v in info.items():
        if "tax" in str(k).lower() and k not in ("buy_tax", "sell_tax"):
            t = _tax_frac(v)
            if t > worst:
                worst, worst_k = t, k
    if worst > float(_cfg("GOPLUS_MAX_SELL_TAX", 0.10)):
        return False, f"{worst_k} {worst * 100:.0f}%"

    # 4. Концентрация у создателя / владельца (защита от дев-дампов вроде 1KTO100M/ORRIS/FEE/Speed)
    try:
        creator_pct = _tax_frac(info.get("creator_percent", 0))
        max_creator = float(_cfg("ROBINHOOD_MAX_CREATOR_PCT", 0.20) if chain == "robinhood" else _cfg("GOPLUS_MAX_CREATOR_PCT", 0.10))
        if creator_pct > max_creator:
            return False, f"creator_pct {creator_pct * 100:.1f}% (>{max_creator*100:.0f}%)"
    except Exception:
        pass
    try:
        owner_pct = _tax_frac(info.get("owner_percent", 0))
        max_owner = float(_cfg("ROBINHOOD_MAX_OWNER_PCT", 0.20) if chain == "robinhood" else _cfg("GOPLUS_MAX_OWNER_PCT", 0.10))
        if owner_pct > max_owner:
            return False, f"owner_pct {owner_pct * 100:.1f}% (>{max_owner*100:.0f}%)"
    except Exception:
        pass

    # 4.5. Концентрация у топ-10 китов (Сивил атака / кошельки дева / бандлы)
    try:
        holders = info.get("holders") or []
        if holders and isinstance(holders, list):
            top10_sum = 0.0
            for h in holders[:10]:
                is_contract = int(h.get("is_contract", 0) or 0)
                is_locked = int(h.get("is_locked", 0) or 0)
                # Исключаем пул ликвидности (DEX pair) и проверенные контракты-локеры
                if is_contract == 1 or is_locked == 1:
                    continue
                pct = _tax_frac(h.get("percent", 0))
                max_single = float(_cfg("GOPLUS_MAX_SINGLE_WHALE_PCT", 0.15))
                if pct > max_single:
                    return False, f"кит держит {pct*100:.1f}% (>{max_single*100:.0f}%) — риск дампа"
                top10_sum += pct
            max_top10 = float(_cfg("GOPLUS_MAX_TOP10_PCT", 0.40))
            if top10_sum > max_top10:
                return False, f"топ-10 китов держат {top10_sum*100:.1f}% (>{max_top10*100:.0f}%) — дамп-риск"
    except Exception:
        pass

    # 5. Проверка блокировки / сжигания LP (защита от вывода ликвидности девом на EVM)
    # 5. Проверка блокировки LP (для сетей с внешними локерами — BSC)
    # На Robinhood и Base GoPlus не индексирует локальные протокольные локеры/NFT-пулы (Clanker, Virtuals, Aerodrome, PoolManager).
    if chain in ("bsc",):
        try:
            lp_holders = info.get("lp_holders") or []
            if lp_holders and isinstance(lp_holders, list) and len(lp_holders) > 0:
                locked_burned = 0.0
                for lph in lp_holders:
                    is_locked = int(lph.get("is_locked", 0) or 0)
                    addr = str(lph.get("address", "")).lower()
                    pct = _tax_frac(lph.get("percent", 0))
                    if is_locked == 1 or "dead" in addr or addr == "0x0000000000000000000000000000000000000000":
                        locked_burned += pct
                min_lp_lock = float(_cfg("BSC_MIN_LP_LOCKED_PCT", 0.70))
                if locked_burned < min_lp_lock:
                    return False, f"LP не заблокирован ({locked_burned*100:.0f}% < {min_lp_lock*100:.0f}%) — риск рагпулла"
            elif pair_data is not None:
                return False, f"lp-unindexed (GoPlus ещё не видит лок LP пула V2 на {chain})"
        except Exception as _e_lp:
            return False, f"ошибка проверки лока LP на BSC ({type(_e_lp).__name__})"

    return True, ""


RAW_SOLANA_INFO: dict = {}  # mint -> raw goplus dict


_SOL_DEX_AUTHORITIES = {
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",  # Raydium AMM V4
    "D4FPEruKEHrG5NTsqazoqfv8zsFWApqTjuzFLUQDkTz5",  # Raydium CP-Swap
    "GpMZbSM2GgvTKHJirzeGfMFoaZ8UR2X7F4v8vHTvxFbL",  # Raydium CPMM / LaunchLab Vault Authority
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",  # Raydium CLMM
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",  # PumpSwap AMM
    "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg",  # Pump.fun / PumpSwap Migration Authority
    "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB",  # Meteora Dynamic Pools
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",  # Meteora DLMM
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",  # Orca Whirlpools
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",  # Pump.fun Bonding Curve
    "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1",  # Pump.fun Authority
}

_SOL_POOL_OR_CEX_TAGS = (
    "raydium", "meteora", "orca", "pump", "pumpswap", "amm", "pool", "vault",
    "lp", "lock", "burn", "dead", "dex", "binance", "coinbase", "bybit",
    "okx", "gate", "mexc", "kucoin", "bitget", "kraken", "wintermute",
)


def _extract_goplus_non_pool_holders(info: dict) -> list:
    """Возвращает доли реальных холдеров [pct_frac, ...] без AMM-пулов, залоченных сейфов и CEX.
    На PumpSwap и Meteora DLMM GoPlus часто оставляет tag='' у пула ликвидности (~20.7% сапплая),
    из-за чего пул ошибочно принимался за 'кита >15%' и блокировал 100% мигрировавших токенов."""
    holders = info.get("holders") or []
    if not isinstance(holders, list) or not holders:
        return []

    dex_list = info.get("dex") or []
    has_dex = isinstance(dex_list, list) and len(dex_list) > 0
    pool_addrs = set(_SOL_DEX_AUTHORITIES)
    if isinstance(dex_list, list):
        for d in dex_list:
            if isinstance(d, dict):
                for fk in ("id", "pool_address", "address", "account", "lp_address"):
                    val = str(d.get(fk) or "").strip()
                    if val:
                        pool_addrs.add(val)
    for lph in (info.get("lp_holders") or []):
        if isinstance(lph, dict):
            val = str(lph.get("account") or lph.get("address") or "").strip()
            if val and int(lph.get("is_locked", 0) or 0) == 1:
                pool_addrs.add(val)

    any_tagged_pool = False
    for h in holders[:6]:
        if not isinstance(h, dict):
            continue
        t_low = str(h.get("tag", "") or "").lower()
        acc = str(h.get("account") or h.get("token_account") or h.get("address") or "").strip()
        own = str(h.get("owner") or "").strip()
        if int(h.get("is_locked", 0) or 0) == 1 or acc in pool_addrs or own in pool_addrs or any(k in t_low for k in _SOL_POOL_OR_CEX_TAGS):
            any_tagged_pool = True
            break

    cleaned = []
    skipped_untagged_vault = False
    for idx, h in enumerate(holders[:12]):
        if not isinstance(h, dict):
            continue
        pct = _tax_frac(h.get("percent", 0))
        if pct <= 0.0 or pct > 0.65:
            continue
        if int(h.get("is_locked", 0) or 0) == 1:
            continue
        tag = str(h.get("tag", "") or "").lower()
        acc = str(h.get("account") or h.get("token_account") or h.get("address") or "").strip()
        own = str(h.get("owner") or "").strip()
        if acc in pool_addrs or own in pool_addrs or any(k in tag for k in _SOL_POOL_OR_CEX_TAGS):
            continue
        # Если токен уже торгуется на DEX (has_dex), но GoPlus не разметил ни один AMM-пул тегом,
        # то крупнейший токен-аккаунт #0 (14%..65%) — это неразмеченный AMM vault (PumpSwap 20.7% / Meteora).
        if idx == 0 and has_dex and not any_tagged_pool and not skipped_untagged_vault and not tag and pct >= 0.14:
            skipped_untagged_vault = True
            continue
        cleaned.append(pct)
    return cleaned


def get_solana_holder_stats(mint: str) -> dict:
    """Извлекает метрики холдеров из кэша GoPlus Solana для анализатора."""
    info = RAW_SOLANA_INFO.get(mint) or {}
    if not info:
        return {"top_10_pct": 0.0, "dev_pct": 0.0, "has_data": False}
    holders = info.get("holders") or []
    if not isinstance(holders, list) or not holders:
        return {"top_10_pct": 0.0, "dev_pct": 0.0, "has_data": False}
    valid_holders = [p * 100.0 for p in _extract_goplus_non_pool_holders(info)]
    top10_sum = sum(valid_holders[:10]) if valid_holders else 0.0
    # Не приравниваем крупнейшего холдера к деву, если creator_percent явно задан в GoPlus
    creator_raw = info.get("creator_percent")
    if creator_raw is not None and str(creator_raw).strip() != "":
        dev_pct = _tax_frac(creator_raw) * 100.0
    else:
        dev_pct = 0.0
    return {
        "top_10_pct": top10_sum,
        "dev_pct": dev_pct,
        "has_data": True,
        "raw_holders_count": len(holders)
    }


def _verdict_sol(info: dict):
    """Глубокий аудит Solana-токена через GoPlus (Mint/Freeze authority, налоги, киты)."""
    if not isinstance(info, dict) or not info:
        return True, "no-data"
    
    # 1. Прямой Honeypot & Token-2022 бэкдоры
    for k in ("is_honeypot", "honeypot"):
        if str(info.get(k, "0")) == "1":
            return False, "honeypot"
    # В Solana Token-2022: 0 = Uninitialized, 1 = Initialized (нормальный рабочий токен!), 2 = Frozen (заморожен).
    # Ошибка 'in ("1", "frozen")' блокировала 100% живых токенов Solana!
    if str(info.get("default_account_state", "0")) in ("2", "frozen"):
        return False, "default_account_frozen (Token-2022 счета покупателей заморожены по умолчанию)"
    dasu = info.get("default_account_state_upgradable")
    if isinstance(dasu, dict):
        if str(dasu.get("status", "0")) == "1" or len(dasu.get("authority", []) or []) > 0:
            return False, "default_account_state_upgradable (дев может заморозить счета)"
    elif str(dasu) in ("1", "true"):
        return False, "default_account_state_upgradable"
    if str(info.get("non_transferable", "0")) in ("1", "true"):
        return False, "non_transferable (Token-2022 запрет передачи)"
    th = info.get("transfer_hook")
    if isinstance(th, dict) and (str(th.get("status", "0")) == "1" or len(th.get("authority", []) or []) > 0):
        return False, "transfer_hook (Token-2022 перехват трансферов)"

    # 2. Mintable (права бесконечного довыпуска токенов девом для дампа)
    mintable = info.get("mintable")
    if isinstance(mintable, dict):
        if str(mintable.get("status", "0")) == "1" or len(mintable.get("authority", []) or []) > 0:
            return False, "mintable (не отозваны права минта)"
    elif str(mintable) in ("1", "true"):
        return False, "mintable"

    # 3. Freezable (права заморозки кошельков покупателей — скрытый honeypot)
    freezable = info.get("freezable")
    if isinstance(freezable, dict):
        if str(freezable.get("status", "0")) == "1" or len(freezable.get("authority", []) or []) > 0:
            return False, "freezable (не отозваны права заморозки)"
    elif str(freezable) in ("1", "true"):
        return False, "freezable"

    # 4. Closable & Balance Mutable (бэкдоры управления балансами)
    closable = info.get("closable")
    if isinstance(closable, dict) and str(closable.get("status", "0")) == "1":
        return False, "closable (дев может закрыть токен-аккаунт)"
    bma = info.get("balance_mutable_authority")
    if isinstance(bma, dict):
        if str(bma.get("status", "0")) == "1" or len(bma.get("authority", []) or []) > 0:
            return False, "balance_mutable (дев может изменить баланс)"
    elif str(bma) in ("1", "true"):
        return False, "balance_mutable (дев может изменить баланс)"

    # 5. Налоги и скрытые комиссии transfer_fee
    worst = 0.0
    for k, v in info.items():
        if "tax" in str(k).lower():
            worst = max(worst, _tax_frac(v))
    tf = info.get("transfer_fee") or {}
    if isinstance(tf, dict) and tf:
        worst = max(worst, _tax_frac(tf.get("rate", 0)))
    if worst > float(_cfg("GOPLUS_MAX_SELL_TAX", 0.10)):
        return False, f"tax {worst * 100:.0f}%"

    # 6. Концентрация у китов и топ-10 холдеров (без AMM-пулов PumpSwap/Meteora/Raydium и CEX)
    non_pool_pcts = _extract_goplus_non_pool_holders(info)
    if non_pool_pcts:
        max_single = float(_cfg("SOLANA_MAX_SINGLE_WHALE_PCT", 0.18))
        if non_pool_pcts[0] > max_single:
            return False, f"кит держит {non_pool_pcts[0]*100:.1f}% (>{max_single*100:.0f}%) — риск дампа"
        top10_sum = sum(non_pool_pcts[:10])
        max_top10 = float(_cfg("SOLANA_MAX_TOP10_PCT", 0.38))
        if top10_sum > max_top10:
            return False, f"топ-10 китов держат {top10_sum*100:.1f}% (>{max_top10*100:.0f}%) — кабал-риск"

    return True, ""


def is_hard_scam(reason: str) -> bool:
    """Возвращает True ТОЛЬКО если причина — реальный риск контракта токена.
    Сетевые ошибки, троттлинг (4029), 429 и отсутствие данных возвращают False."""
    if not reason:
        return False
    r = str(reason).lower()
    if r in ("no-data", "unindexed", "unsupported", "off", "no-addr",
             "goplus-rate-limit", "goplus-server-error"):
        return False
    if r.startswith("error") or r.startswith("goplus-status-") or r.startswith("goplus-err-"):
        return False
    if r.startswith("goplus-code-"):
        code_str = r.replace("goplus-code-", "").strip()
        if code_str in ("4029", "3", "2024", "0", ""):
            return False
    # Известные уязвимости / ловушки контракта
    for danger in ("honeypot", "cannot_sell", "cannot_buy", "blacklist", "cooldown",
                   "mintable", "ownership", "hidden_owner", "selfdestruct",
                   "external_call", "tax", "creator_pct", "owner_pct", "lp", "рагпулл",
                   "кит", "дамп", "freeze", "freezable", "closable", "balance_mutable", "кабал",
                   "frozen", "non_transferable", "transfer_hook", "default_account", "заморозка",
                   "pause", "transfer_pausable", "whitelist", "anti_whale", "slippage", "closed_source", "rugcheck"):
        if danger in r:
            return True
    return False


async def check_token(address: str, chain: str, force_refresh: bool = False, pair_data: dict = None):
    """EVM-гейт: (True, '') — можно брать; (False, причина) — блок."""
    chain = (chain or "").lower()
    if not _cfg("GOPLUS_ENABLED", True):
        return True, "off"
    if not address:
        return True, "no-addr"
    cid = EVM_CHAIN_IDS.get(chain)
    if not cid:
        return True, "unsupported"  # Robinhood Chain и др. GoPlus не знает
    key = (chain, address.lower())
    ttl = float(_cfg("GOPLUS_CACHE_SEC", 3600) or 0)
    hit = _CACHE.get(key)
    if not force_refresh and hit and ttl > 0 and (_t.time() - hit[0]) < ttl:
        CALLS["hit"] += 1
        return hit[1]
    CALLS["miss"] += 1
    verdict = (False, "no-data")
    try:
        from http_client import fetch_json
        url = (f"https://api.gopluslabs.io/api/v1/token_security/{cid}"
               f"?contract_addresses={address}")
        headers = {}
        api_key = str(_cfg("GOPLUS_API_KEY", "") or "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        status, data = await fetch_json(url, headers=headers if headers else None, timeout=10, retries=1)
        if status == 200 and isinstance(data, dict):
            code = data.get("code")
            if code == 1:
                result = data.get("result") or {}
                info = result.get(address) or result.get(address.lower()) or {}
                if not info and isinstance(result, dict):
                    # регистронезависимый поиск ключа в ответе
                    for k, v in result.items():
                        if str(k).lower() == address.lower() and isinstance(v, dict):
                            info = v
                            break
                    if not info:
                        for v in result.values():
                            if isinstance(v, dict):
                                info = v
                                break
                verdict = _verdict_evm(info, chain=chain, pair_data=pair_data)
            elif code == 4029:
                verdict = (False, "goplus-rate-limit")
            elif code == 3:
                verdict = (False, "no-data")
            elif code == 2024:
                verdict = (True, "unsupported")
            else:
                verdict = (False, f"goplus-code-{code}")
        elif status == 429:
            verdict = (False, "goplus-rate-limit")
        elif status in (500, 502, 503, 504):
            verdict = (False, "goplus-server-error")
        else:
            verdict = (False, f"goplus-status-{status}")
    except Exception as e:
        verdict = (False, f"error-{type(e).__name__}")
    if ttl > 0:
        if verdict[0] or is_hard_scam(verdict[1]):
            _ts = _t.time()
        elif verdict[1] == "goplus-rate-limit":
            _ts = _t.time() - ttl + 10
        else:
            _ts = _t.time() - ttl + 30
        _CACHE[key] = (_ts, verdict)
        if len(_CACHE) > 2000:
            _CACHE.clear()
    if is_hard_scam(verdict[1]):
        CALLS["block"] += 1
    return verdict


SOLANA_RPC_URLS = [
    "https://api.mainnet-beta.solana.com",
    "https://rpc.ankr.com/solana",
    "https://solana-rpc.publicnode.com",
]
_HELIUS_429_UNTIL: float = 0.0
_RPC_BACKOFF_UNTIL: dict = {}  # rpc_url -> unix_ts


def _note_rpc_status(rpc_url: str, status: int) -> None:
    """Circuit-breaker для Solana RPC: при 429/401/403 (исчерпан лимит Helius или блок ноды) отключает URL на 30 мин."""
    global _HELIUS_429_UNTIL
    if status in (401, 403, 429):
        now = _t.time()
        if "helius" in str(rpc_url or "").lower():
            if now >= _HELIUS_429_UNTIL:
                print(f"⚡ [RPC-BREAKER] Helius вернул HTTP {status} (квота исчерпана) -> пауза Helius 30м, переключаюсь на прямой Solana RPC.")
            _HELIUS_429_UNTIL = now + 1800.0
        else:
            _RPC_BACKOFF_UNTIL[str(rpc_url)] = now + 600.0


def _get_solana_rpcs() -> list:
    """Возвращает приоритетный список здоровых Solana RPC без мёртвых/залимиченных ключей."""
    import os as _os
    now = _t.time()
    out = []
    custom_rpc = (_os.getenv("SOLANA_RPC_URL") or getattr(config, "SOLANA_RPC_URL", "") or "").strip()
    if custom_rpc and custom_rpc.startswith("http"):
        if ("helius" not in custom_rpc.lower() or now >= _HELIUS_429_UNTIL) and now >= _RPC_BACKOFF_UNTIL.get(custom_rpc, 0.0):
            out.append(custom_rpc)
    for u in SOLANA_RPC_URLS:
        if u not in out and now >= _RPC_BACKOFF_UNTIL.get(u, 0.0):
            out.append(u)
    if now >= _HELIUS_429_UNTIL:
        h_rpc = (_os.getenv("HELIUS_RPC_URL") or getattr(config, "HELIUS_RPC_URL", "") or "").strip()
        if h_rpc and h_rpc.startswith("http") and h_rpc not in out:
            out.append(h_rpc)
    return out or ["https://api.mainnet-beta.solana.com"]


async def check_onchain_solana_security(mint: str) -> tuple:
    """Прямой ончейн-аудит Solana через RPC getAccountInfo(mint, jsonParsed).
    
    Защищает от скрытой заморозки (Freeze Authority), бесконечного минта (Mint Authority)
    и вредоносных Token-2022 расширений (Transfer Hook / Permanent Delegate / Frozen Default).
    Не зависит от сторонних API (GoPlus, RugCheck) и лимитов 429.
    
    Возвращает (ok: bool, reason: str).
    """
    if not mint or len(mint) < 32 or mint.startswith("0x"):
        return True, ""
    
    # Исключаем системные токены (WSOL, USDC, USDT)
    if mint in ("So11111111111111111111111111111111111111112",
                "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"):
        return True, ""

    try:
        from http_client import get_session
        import aiohttp
        session = await get_session()
        rpcs = _get_solana_rpcs()

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [mint, {"encoding": "jsonParsed"}]
        }
        
        account_info = None
        for rpc in rpcs:
            try:
                async with session.post(rpc, json=payload, timeout=aiohttp.ClientTimeout(total=3.5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if isinstance(data, dict) and data.get("error"):
                            err_msg = str(data.get("error")).lower()
                            if "max usage" in err_msg or "429" in err_msg or "rate" in err_msg:
                                _note_rpc_status(rpc, 429)
                                continue
                        val = (data.get("result") or {}).get("value")
                        if val is not None:
                            account_info = val
                            break
                    else:
                        _note_rpc_status(rpc, resp.status)
            except Exception:
                continue

        if not account_info:
            return True, ""

        data_obj = account_info.get("data") or {}
        parsed = data_obj.get("parsed") or {}
        info = parsed.get("info") or {}
        
        # 1. Проверка Freeze Authority (права заморозки токенов покупателей девом — скрытый honeypot)
        freeze_auth = info.get("freezeAuthority")
        PUMPFUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
        SYSTEM_PROGRAM = "11111111111111111111111111111111"
        SAFE_FREEZE = {None, "", SYSTEM_PROGRAM}
        if freeze_auth and freeze_auth not in SAFE_FREEZE:
            return False, f"Freeze Authority активна у кошелька {freeze_auth[:8]} — 100% риск заморозки токена девом!"

        # 2. Проверка Mint Authority (права бесконечного минта девом для дампа)
        mint_auth = info.get("mintAuthority")
        SAFE_MINT = {None, "", SYSTEM_PROGRAM, PUMPFUN_PROGRAM}
        if mint_auth and mint_auth not in SAFE_MINT:
            return False, f"Mint Authority активна у кошелька {mint_auth[:8]} — риск бесконечного довыпуска токенов девом!"

        # 3. Проверка вредоносных Token-2022 расширений
        extensions = info.get("extensions") or []
        for ext in extensions:
            ext_type = str((ext or {}).get("extension") or "").lower()
            if "transferhook" in ext_type:
                return False, "Token-2022 Transfer Hook (перехват трансфера — скрытый honeypot)"
            if "permanentdelegate" in ext_type:
                return False, "Token-2022 Permanent Delegate (дев может сжечь/украсть токены с кошелька)"
            if "defaultaccountstate" in ext_type:
                state_val = str(((ext or {}).get("state") or {}).get("accountState") or "").lower()
                if state_val == "frozen":
                    return False, "Token-2022 DefaultAccountState: Frozen (все счета покупателей заморожены)"
            if "confidentialtransfer" in ext_type:
                return False, "Token-2022 Confidential Transfer (скрытый баланс)"

        return True, ""
    except Exception:
        return True, ""


async def check_solana(mint: str):
    """Solana-гейт: (True, '') — можно брать; (False, причина) — блок."""
    if not _cfg("GOPLUS_ENABLED", True):
        return True, "off"
    if not mint:
        return True, "no-addr"

    # 0. Мгновенный ончейн-аудит Solana (Freeze Authority, Mint Authority, Token-2022) через прямой RPC
    _onchain_ok, _onchain_why = await check_onchain_solana_security(mint)
    if not _onchain_ok:
        return False, _onchain_why

    key = ("solana", mint)
    ttl = float(_cfg("GOPLUS_CACHE_SEC", 3600) or 0)
    hit = _CACHE.get(key)
    if hit and ttl > 0 and (_t.time() - hit[0]) < ttl:
        CALLS["hit"] += 1
        return hit[1]
    CALLS["miss"] += 1
    verdict = (True, "no-data")
    try:
        from http_client import fetch_json
        url = ("https://api.gopluslabs.io/api/v1/solana/token_security"
               f"?contract_addresses={mint}")
        headers = {}
        api_key = str(_cfg("GOPLUS_API_KEY", "") or "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        status, data = await fetch_json(url, headers=headers, timeout=8, retries=1)
        if status == 200 and isinstance(data, dict) and data.get("code") == 1:
            result = data.get("result") or {}
            info = result.get(mint) or {}
            if not info and isinstance(result, dict):
                for v in result.values():
                    if isinstance(v, dict):
                        info = v
                        break
            RAW_SOLANA_INFO[mint] = info
            verdict = _verdict_sol(info)

        # 4. Аудит через RugCheck API (защита от скам-пулов и дампов дева):
        if verdict[0]:
            try:
                rc_status, rc_data = await fetch_rugcheck_report_cached(mint, ttl=120.0, timeout=5)
                if rc_status == 200 and isinstance(rc_data, dict):
                    rc_score = rc_data.get("score", 0) or 0
                    # Вычитаем штрафы за стандартный шум DEX/AMM-пулов и launchpad (unlocked LP, copycat,
                    # а также текстовые предупреждения о концентрации, где RugCheck путает пул PumpSwap с китом —
                    # реальных холдеров мы уже проверили выше в п.3 с точным исключением DEX_POOL_AUTHORITIES!):
                    _allowed_rc_noise = (
                        "low liquidity", "copycat", "symbol mimic",
                        "large amount of lp unlocked", "lp provider", "single lp provider",
                        "lp not locked", "unlocked lp",
                        "single holder", "high holder concentration", "top 10 holders", "high ownership"
                    )
                    rc_noise_penalty = sum(
                        float(rk.get("score", 0) or 0)
                        for rk in (rc_data.get("risks") or [])
                        if any(k in str(rk.get("name", "")).lower() for k in _allowed_rc_noise)
                    )
                    clean_score = max(0.0, rc_score - rc_noise_penalty)
                    tok_meta = rc_data.get("token") if isinstance(rc_data.get("token"), dict) else rc_data
                    freeze_val = tok_meta.get("freezeAuthority") or rc_data.get("freezeAuthority")
                    mint_val = tok_meta.get("mintAuthority") or rc_data.get("mintAuthority")
                    if clean_score >= 2000:
                        verdict = (False, f"rugcheck-score {clean_score:.0f} >= 2000 (высокий риск скама/слива)")
                    elif freeze_val and str(freeze_val) not in ("11111111111111111111111111111111", "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"):
                        verdict = (False, "rugcheck: freeze authority не отозвана")
                    elif mint_val and str(mint_val) not in ("11111111111111111111111111111111", "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"):
                        verdict = (False, "rugcheck: mint authority не отозвана")
                    else:
                        # Проверка критических рисков создателя/контракта
                        for rk in (rc_data.get("risks") or []):
                            _rkn = str(rk.get("name", "")).lower()
                            _rkl = str(rk.get("level", "")).lower()
                            if _rkl == "danger" and any(k in _rkn for k in ("creator holding", "creator history", "honeypot", "transfer fee", "permanent delegate")):
                                verdict = (False, f"rugcheck: {rk.get('name')} (опасный риск контракта/дева)")
                                break
            except Exception:
                pass
    except Exception:
        verdict = (True, "error")
    if ttl > 0:
        _CACHE[key] = (_t.time(), verdict)
        if len(_CACHE) > 2000:
            _CACHE.clear()
    if is_hard_scam(verdict[1]):
        CALLS["block"] += 1
    return verdict


EVM_RPC_URLS = {
    "robinhood": ["https://rpc.mainnet.chain.robinhood.com"],
    "base": ["https://base-rpc.publicnode.com", "https://mainnet.base.org"],
    "bsc": ["https://bsc-rpc.publicnode.com", "https://bsc-dataseed.binance.org"],
    "ethereum": ["https://ethereum-rpc.publicnode.com", "https://eth.llamarpc.com"],
}

UNISWAP_V4_POOL_MANAGERS = {
    "robinhood": "8366a39cc670b4001a1121b8f6a443a643e40951",
    "base": "498581ff718922c3f8e6a244956af099b2652b2b",
    "bsc": "28e2ea090877bf75740558f6bfb36a5ffee9e9df",
    "ethereum": "000000000004444c5dc75cb358380d2e3de08a90",
}


async def check_onchain_evm_security(address: str, chain: str, pair_data: dict = None, is_open_pos: bool = False) -> tuple:
    """Прямой ончейн-аудит байткода и реальных резервов пула (Uniswap V4 PoolManager / V2 LP) через RPC.
    Защищает от серийных 15-минутных LP-rug ботов (шаблоны 9312 на Robinhood и 3256 на Base: 1, VC, Satoshi, STK),
    которые заливают 2% эмиссии в V4 пул, накручивают +80% и выдергивают всю ликвидность, пока DexScreener показывает старую цену."""
    chain = (chain or "").lower().strip()
    address = (address or "").strip()
    if not address or chain not in EVM_RPC_URLS:
        return True, ""

    _cfg_rpc = getattr(config, f"{chain.upper()}_RPC_URL", None)
    _rpc_candidates = ([_cfg_rpc] if _cfg_rpc else []) + [u for u in EVM_RPC_URLS[chain] if u != _cfg_rpc]
    try:
        from http_client import get_session
        import aiohttp
        _s_sess = await get_session()

        _pd = pair_data if isinstance(pair_data, dict) else {}
        _pair_addr = str(_pd.get("pairAddress") or "")
        _labels = [str(x).lower() for x in (_pd.get("labels") or [])]
        _dex_id = str(_pd.get("dexId") or "").lower()
        _v4_pm_raw = UNISWAP_V4_POOL_MANAGERS.get(chain)
        _is_v4 = ("v4" in _labels) or ("v4" in _dex_id) or (len(_pair_addr) > 42)

        # Единый батч-запрос: eth_getCode + totalSupply() + balanceOf(Uniswap_V4_PoolManager) + balanceOf(pairAddress)
        _batch_pl = [
            {"jsonrpc": "2.0", "id": 1, "method": "eth_getCode", "params": [address, "latest"]},
            {"jsonrpc": "2.0", "id": 2, "method": "eth_call", "params": [{"to": address, "data": "0x18160ddd"}, "latest"]},
            {"jsonrpc": "2.0", "id": 5, "method": "eth_getStorageAt", "params": [address, "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc", "latest"]},
            {"jsonrpc": "2.0", "id": 6, "method": "eth_call", "params": [{"to": address, "data": "0x8da5cb5b"}, "latest"]},
        ]
        if _v4_pm_raw:
            _V4_PM = _v4_pm_raw.zfill(64)
            _batch_pl.append(
                {"jsonrpc": "2.0", "id": 3, "method": "eth_call", "params": [{"to": address, "data": f"0x70a08231{_V4_PM}"}, "latest"]}
            )
        if len(_pair_addr) == 42 and _pair_addr.startswith("0x"):
            _PAIR_HEX = _pair_addr[2:].lower().zfill(64)
            _batch_pl.append(
                {"jsonrpc": "2.0", "id": 4, "method": "eth_call", "params": [{"to": address, "data": f"0x70a08231{_PAIR_HEX}"}, "latest"]}
            )

        _res_map = {}
        _rpc_url = _rpc_candidates[0]
        for _u in _rpc_candidates:
            try:
                async with _s_sess.post(_u, json=_batch_pl, timeout=aiohttp.ClientTimeout(total=4)) as _r_b:
                    _raw_b = await _r_b.json()
                if isinstance(_raw_b, list):
                    _tmp = {item.get("id"): item for item in _raw_b if isinstance(item, dict)}
                    if 1 in _tmp and "result" in _tmp[1] and "error" not in _tmp[1]:
                        _res_map = _tmp
                        _rpc_url = _u
                        break
            except Exception:
                continue

        if not _res_map:
            if chain == "robinhood":
                return False, "ошибка ончейн-аудита Robinhood (RPC недоступен)"
            return True, ""

        # 1. Байткод-аудит (шаблоны серийных LP-рагов и скрытые селекторы минта/паузы/блэклиста)
        _bytecode = str(_res_map[1].get("result") or "").lower()
        if _bytecode in ("0x", "0x0", ""):
            return False, "нет байткода (не смарт-контракт)"
        _info_pd = _pd.get("info") or {}
        _links_pd = (_info_pd.get("socials") or []) + (_info_pd.get("websites") or [])
        _c_ms_pd = _pd.get("pairCreatedAt") or 0
        _age_pd = ((_t.time() * 1000 - _c_ms_pd) / 60000.0) if _c_ms_pd else 999.0
        _liq_pd = float((_pd.get("liquidity") or {}).get("usd", 0) or 0)
        _vol1_pd = float(((_pd.get("volume") or {}).get("h1", 0) or 0))
        _tx_h1 = (_pd.get("txns") or {}).get("h1") or {}
        _s1_pd = int(_tx_h1.get("sells", 0) or 0)
        _is_mature = (_liq_pd >= 25000.0 and _vol1_pd >= 10000.0 and _s1_pd >= 30 and _age_pd >= 15.0)
        _has_reputation = bool(_links_pd) or _is_mature

        # Реальные серийные скамы отсекаются бэкдорами байткода (минт/пауза/блэклист),
        # проверкой доли эмиссии в пуле (_frac < 25%), Honeypot-симуляцией и балансом дева (creator_pct > 20%).
        # Стандартные байткоды OpenZeppelin/Clanker (3256, 7710 и др.) НЕ блокируем, чтобы не терять ракеты (CAPA, 1, BOO, GENNY, CURE)!

        if not is_open_pos:
            _dangerous_selectors = {
                "40c10f19": "скрытый минт mint(address,uint256)",
                "a0712d68": "скрытый минт mint(uint256)",
                "42f21696": "блэклист blacklist(address)",
                "83e2007e": "isBlacklisted(address)",
                "8456cb59": "пауза торговли pause()",
                "03a118d5": "пауза торговли pause(bool)",
                "0f34085f": "заморозка freeze(address)",
                "b3555543": "заморозка freezeAccount(address,bool)",
                "9e6027a4": "заморозка freeze(address,bool)",
                "2e08c6a5": "заморозка setFreeze(address,bool)",
                "6a17b35f": "блэклист setBlacklist(address,bool)",
                "12b184ea": "блэклист setBlackList(address,bool)",
                "16c38b34": "пауза setPaused(bool)",
                "bedb86fb": "пауза setPause(bool)",
                "2f3277bb": "пауза setTrading(bool)",
                "5140f09a": "пауза setTradingEnabled(bool)",
                "cb5f1f77": "пауза enableTrading(bool)",
            }
            for _sel, _desc in _dangerous_selectors.items():
                if _sel in _bytecode:
                    return False, f"бэкдор в байткоде токена: {_desc}"

        # 1.2. Проверка EIP-1967 прокси-бэкдора (модифицируемый контракт)
        if not is_open_pos and 5 in _res_map:
            _impl = str(_res_map[5].get("result") or "").lower()
            if _impl not in ("0x", "0x0", ""):
                try:
                    if int(_impl, 16) != 0:
                        return False, "модифицируемый прокси-контракт (EIP-1967) — риск скрытого обновления в ханипот"
                except Exception:
                    pass

        # 1.5. Структурный ABI-отпечаток диспетчера (НЕ ЗАВИСИТ ОТ ДЛИНЫ БАЙТКОДА!):
        # Любой официальный лаунчпад (Clanker, RobinPump, Zora, Virtuals) либо деплоит EIP-1167 прокси (<=130 симв.),
        # либо содержит >= 5 специальных селекторов лаунчпада/локера/EIP-2612 помимо голого ERC-20.
        # Ручной голый ERC-20 (extra_selectors <= 2) опасен на V2/V3/V4 ТОЛЬКО тогда, когда у токена НЕТ соцсетей (not _links_pd):
        # если у токена есть соцсети (DOPE, GRGKO), то это нормальный проект, использовавший стандартный OpenZeppelin ERC-20!
        _is_bare_manual_erc20 = False
        if not is_open_pos and len(_bytecode) > 150:
            import re as _re_bc
            _BARE_ERC20_SELS = {
                "06fdde03", "095ea7b3", "18160ddd", "23b872dd", "313ce567",
                "70a08231", "95d89b41", "a9059cbb", "dd62ed3e",
                "42966c68", "79cc6790", "39509351", "a457c2d7",
                "8da5cb5b", "715018a6", "f2fde38b", "3c130d90",
            }
            _disp_head = _bytecode[:1400]
            _found_sels = set(_re_bc.findall(r"63([0-9a-f]{8})(?:14|11)", _disp_head))
            if len(_found_sels) >= 6 and len(_found_sels - _BARE_ERC20_SELS) <= 2:
                _is_bare_manual_erc20 = True

        # 1.6. Защита от серийных LP-rug фабрик на Uniswap V4:
        # На Base легитимные мем-ракеты запускаются исключительно через Clanker (0xb20000000000...).
        # Ручные V4-пулы на Base (DOAN, DADOGE, FINUL, SXSI, P2L, JEET) — это 100% серийные рагпуллы скамеров!
        # На Robinhood Chain Uniswap V4 — это основной DEX (EMBER, SEASON, KIMCHI), его блокировать нельзя!
        _q_amt_pd = float((_pd.get("liquidity") or {}).get("quote", 0.0) or 0.0)
        _q_sym_pd = str((_pd.get("quoteToken") or {}).get("symbol", "")).upper()
        _is_clanker_v4 = address.lower().startswith("0xb20000000000")

        if not is_open_pos and _is_v4:
            # На всех сетях перед входом в V4 требуем реальный объем quote-актива (ETH >= 0.15 для Clanker, >= 0.35 для остальных)
            # Это фильтрует фантомные V4 пулы дева (0.00001 ETH) и разрешает реальные ракеты (FROPE, STARMIND, QUENTRA с 1.8+ ETH)
            _min_v4_q = 0.15 if _is_clanker_v4 else 0.35
            if ("ETH" in _q_sym_pd or "WETH" in _q_sym_pd) and 0 < _q_amt_pd < _min_v4_q:
                return False, f"фантомный пул Uniswap V4 (в пуле всего {_q_amt_pd:.4f} ETH < {_min_v4_q:.2f} ETH — ловушка)"
            elif "USD" in _q_sym_pd and 0 < _q_amt_pd < 800.0:
                return False, f"фантомный пул Uniswap V4 (в пуле всего ${_q_amt_pd:.0f} USD < $800 — ловушка)"

        _is_v3 = ("v3" in _labels) or ("v3" in _dex_id) or ("slipstream" in _dex_id)

        # 2. Проверка реальной ликвидности пула (Uniswap V4 PoolManager vs V3 vs V2)
        _tot_v = 0
        if 2 in _res_map:
            _tot_h = _res_map[2].get("result", "0x")
            if _tot_h and _tot_h != "0x":
                try:
                    _tot_v = int(_tot_h, 16)
                except Exception:
                    _tot_v = 0

        # 2.1. Uniswap V4 PoolManager аудит (on-chain)
        if _is_v4:
            _pm_h = _res_map.get(3, {}).get("result", "0x")
            _pm_v = int(_pm_h, 16) if _pm_h and _pm_h != "0x" else 0
            _pm_frac = (_pm_v / _tot_v) if _tot_v > 0 else 0.0

            # Если пул уже опустошён (modifyLiquidity rugpull):
            if not _is_clanker_v4 and _tot_v > 0 and (_pm_v == 0 or _pm_frac < 0.002):
                return False, f"пул Uniswap V4 опустошён девом ({_pm_frac*100:.5f}% в пуле, {_pm_v/1e18:,.1f} токенов — V4 LP Rug)"

            # Защита перед входом (not is_open_pos):
            if not is_open_pos:
                # На Base: ручные V4 пулы без Clanker и без соцсетей — 100% 15-минутная серийная раг-фабрика
                if chain == "base" and not _is_clanker_v4 and not _has_reputation:
                    if _is_bare_manual_erc20 or _pm_frac < 0.20:
                        return False, f"ручной картонный пул Uniswap V4 на Base (в пуле всего {_pm_frac*100:.1f}% эмиссии < 20% без Clanker/соцсетей — схема LP-rug)"

                # На Robinhood: пул должен иметь минимум 2.5% эмиссии
                if chain == "robinhood" and not _has_reputation and _tot_v > 0:
                    if _pm_frac < 0.025:
                        return False, f"картонный пул Uniswap V4 на Robinhood (в пуле всего {_pm_frac*100:.2f}% эмиссии < 2.5% без соцсетей — схема LP-rug)"

        # 2.2. Uniswap V3 / Aerodrome Slipstream аудит
        elif _is_v3:
            _pair_h = _res_map.get(4, {}).get("result", "0x")
            _pair_v = int(_pair_h, 16) if _pair_h and _pair_h != "0x" else 0
            _p_frac = (_pair_v / _tot_v) if _tot_v > 0 else 0.0

            if _tot_v > 0 and len(_pair_addr) == 42:
                if is_open_pos and _pair_v == 0:
                    return False, f"пул Uniswap V3 опустошён девом (0 токенов в пуле — V3 LP Rug)"
                if not is_open_pos and not _has_reputation and _pair_v > 0 and _p_frac < 0.025:
                    return False, f"картонный пул V3 (в пуле всего {_p_frac*100:.2f}% эмиссии < 2.5% без соцсетей — схема LP-rug)"

        # 2.3. Uniswap V2 стандартный аудит
        else:
            if 4 in _res_map and _tot_v > 0:
                _pair_h = _res_map[4].get("result", "0x")
                _pair_v = int(_pair_h, 16) if _pair_h and _pair_h != "0x" else 0
                _p_frac = _pair_v / _tot_v
                if _p_frac < 0.005:
                    return False, f"пул V2 опустошён девом (остаток {_p_frac*100:.4f}% — LP Rug)"
                if not is_open_pos and not _has_reputation and chain in ("robinhood", "base"):
                    if _p_frac < 0.05:
                        return False, f"картонный пул V2 (в пуле всего {_p_frac*100:.1f}% эмиссии < 5% без соцсетей — схема LP-rug)"
                    if ("ETH" in _q_sym_pd or "WETH" in _q_sym_pd) and 0 < _q_amt_pd < 0.35:
                        return False, f"фантомный пул V2 (в пуле всего {_q_amt_pd:.8f} ETH < 0.35 ETH — скам-ловушка)"

        # 3. Проверка сжигания ликвидности (LP Burn / Lock) — ТОЛЬКО ДЛЯ СТАНДАРТНЫХ V2 ПУЛОВ!
        # (Никогда не запускаем для V3 или V4, у которых нет ERC-20 LP токена!)
        if not is_open_pos and not _is_v4 and not _is_v3 and len(_pair_addr) == 42 and chain in ("robinhood", "bsc", "base", "ethereum"):
            _DEAD = "000000000000000000000000000000000000dead".zfill(64)
            _ZERO = "0000000000000000000000000000000000000000".zfill(64)
            _pl_lp = [
                {"jsonrpc": "2.0", "id": 10, "method": "eth_call", "params": [{"to": _pair_addr, "data": "0x18160ddd"}, "latest"]},
                {"jsonrpc": "2.0", "id": 11, "method": "eth_call", "params": [{"to": _pair_addr, "data": f"0x70a08231{_DEAD}"}, "latest"]},
                {"jsonrpc": "2.0", "id": 12, "method": "eth_call", "params": [{"to": _pair_addr, "data": f"0x70a08231{_ZERO}"}, "latest"]},
            ]
            async with _s_sess.post(_rpc_url, json=_pl_lp, timeout=aiohttp.ClientTimeout(total=4)) as _r_lp:
                _res_lp = await _r_lp.json()
            if isinstance(_res_lp, list) and len(_res_lp) >= 3:
                _tot_lp_h = _res_lp[0].get("result", "0x")
                _dead_lp_h = _res_lp[1].get("result", "0x")
                _zero_lp_h = _res_lp[2].get("result", "0x")
                if _tot_lp_h and _tot_lp_h != "0x" and "error" not in _res_lp[0]:
                    _tot_lp = int(_tot_lp_h, 16)
                    _dead_lp = int(_dead_lp_h, 16) if _dead_lp_h and _dead_lp_h != "0x" else 0
                    _zero_lp = int(_zero_lp_h, 16) if _zero_lp_h and _zero_lp_h != "0x" else 0
                    if _tot_lp > 0:
                        _burnt_pct = ((_dead_lp + _zero_lp) / _tot_lp) * 100.0
                        # Строгая защита для Base и Robinhood (не менее 80% LP сожжено без подтверждённых соцсетей)
                        if _burnt_pct < 80.0:
                            if chain in ("robinhood", "base") and not _has_reputation:
                                return False, f"ликва пула V2 не сожжена ({_burnt_pct:.1f}% < 80%) — риск вывода ликвидности девом"
                            elif chain == "bsc":
                                # На BSC легитимные проекты блокируют LP через PinkSale/Unicrypt/TeamFinance (не 0xdead).
                                # Лок строго проверяется через GoPlus lp_holders (is_locked == 1) на Уровне 1 (min 70%).
                                pass
    except Exception as _e_oc:
        if chain == "robinhood":
            return False, f"ошибка ончейн-аудита Robinhood ({type(_e_oc).__name__}) — блок по безопасности"

    return True, ""


async def verify_evm_token(address: str, chain: str, pair_data: dict = None) -> tuple:
    """3-уровневая гибридная защита EVM (GoPlus + Honeypot.is симуляция + On-Chain DEX).
    
    Возвращает (ok: bool, reason: str).
    - (True, ''): контракт чист и проверен.
    - (False, reason): подтверждённый скам, ханипот или не подтверждён на BSC/Base.
    """
    chain = (chain or "").lower().strip()
    address = (address or "").strip()
    if not address:
        return True, "no-addr"

    # 0. Защита от Honeypot для ВСЕХ сетей: проверка реальной продаваемости (нельзя продать токен)
    if pair_data and isinstance(pair_data, dict):
        txns_m5 = ((pair_data.get("txns") or {}).get("m5") or {})
        txns_h1 = ((pair_data.get("txns") or {}).get("h1") or {})
        s5 = int(txns_m5.get("sells", 0) or 0)
        s1 = int(txns_h1.get("sells", 0) or 0)
        b5 = int(txns_m5.get("buys", 0) or 0)
        if s5 == 0 and s1 == 0 and b5 >= 5:
            return False, f"0 продаж при {b5} покупках — Honeypot (токен заморожен/нельзя продать)"
        if (b5 + s5) >= 20 and (s5 / (b5 + s5)) < 0.05:
            return False, f"аномалия продаж m5 {s5}s/{b5}b (менее 5% продаж) — honeypot-ловушка"

    if chain in ("robinhood",):
        # Robinhood Chain (4663) ончейн-аудит безопасности и honeypot:
        if not pair_data or not isinstance(pair_data, dict):
            return False, "нет данных пары для Robinhood"

        # 2. Ончейн-аудит байткода и LP-резервов через RPC (селекторы бэкдоров + V4 PoolManager fraction >= 8% / V2 LP burn)
        _oc_ok, _oc_why = await check_onchain_evm_security(address, "robinhood", pair_data, is_open_pos=False)
        if not _oc_ok:
            return False, _oc_why

        # 3. GoPlus Security API (проверка creator_pct, налогов, honeypot ДО входа!)
        _g_ok, _g_why = await check_token(address, "robinhood", pair_data=pair_data)
        if not _g_ok and is_hard_scam(_g_why):
            return False, f"GoPlus {_g_why}"

        return True, ""

    if chain not in ("bsc", "base", "ethereum"):
        return True, "unsupported"

    # Уровень 0: Прямой ончейн-аудит байткода и Uniswap V4 PoolManager резервов (ловит 15м V4 LP-rug фабрику на Base/ETH/BSC до входа!)
    _oc_ok, _oc_why = await check_onchain_evm_security(address, chain, pair_data, is_open_pos=False)
    if not _oc_ok:
        return False, _oc_why

    # Уровень 1: GoPlus Security API
    _g_ok, _g_why = await check_token(address, chain, pair_data=pair_data)
    if is_hard_scam(_g_why):
        return False, f"GoPlus {_g_why}"

    # Уровень 2: Honeypot.is (симуляция транзакций покупки и продажи)
    _h_ok, _h_why = True, ""
    try:
        import honeypot_is as _hp
        _h_ok, _h_why = await _hp.check_token(address, chain)
        if _hp.is_hard_scam(_h_why):
            return False, f"honeypot.is {_h_why}"
    except Exception:
        _h_ok, _h_why = False, "hp-exception"

    # Если GoPlus подтвердил токен И Honeypot.is не нашёл скама (чисто или нет данных / 404) — чистый вход
    try:
        import honeypot_is as _hp
        _h_scam = _hp.is_hard_scam(_h_why)
    except Exception:
        _h_scam = False

    if _g_ok and _g_why == "" and not _h_scam:
        return True, ""

    # Уровень 3: Ончейн-валидация пула DEX (если внешний сервис ещё не успел проиндексировать токен)
    if chain in ("base", "ethereum"):
        if pair_data and isinstance(pair_data, dict):
            import time as _t_base
            _c_ms = pair_data.get("pairCreatedAt") or 0
            _age_min = ((_t_base.time() * 1000 - _c_ms) / 60000.0) if _c_ms else 999.0
            txns_m5 = ((pair_data.get("txns") or {}).get("m5") or {})
            s5 = int(txns_m5.get("sells", 0) or 0)
            b5 = int(txns_m5.get("buys", 0) or 0)
            vol5 = float(((pair_data.get("volume") or {}).get("m5", 0) or 0))

            # Для стандартных Uniswap V2 пулов на Base:
            # Нельзя пускать пул, пока GoPlus не подтвердил блокировку/сжигание LP (защита от CHUBBY и ANTHROPIC LP-rug)!
            _dex_id = str((pair_data or {}).get("dexId", "")).lower()
            _lbls = [str(x).lower() for x in (pair_data.get("labels") or [])]
            _is_nft_p = any(v in _dex_id for v in ("v3", "slipstream")) or any("v3" in lb or "slipstream" in lb for lb in _lbls) or address.lower().startswith("0xb20000000000")
            
            if not _is_nft_p:
                if not _g_ok or _g_why != "":
                    return False, f"пул V2 на {chain} не верифицирован GoPlus по LP-локу ({_g_why or 'no-data'}) — защита от LP-рагпулла"

            # Для проверенных NFT-пулов (V3/V4/Aerodrome Slipstream/Clanker):
            if _is_nft_p:
                if not _g_ok and is_hard_scam(_g_why):
                    return False, f"пул {chain}: GoPlus {_g_why}"
                try:
                    import honeypot_is as _hp
                    if not _h_ok and _hp.is_hard_scam(_h_why):
                        return False, f"пул {chain}: Honeypot.is {_h_why}"
                except Exception:
                    pass
                # Clanker фабрика (0xb20000000000...) — верифицированный immutable контракт, не блокируется при unindexed GoPlus
                if address.lower().startswith("0xb20000000000") and (b5 + s5) >= 6 and vol5 >= 1500 and not is_hard_scam(_g_why):
                    return True, ""
                if s5 >= 3 and b5 >= int(s5 * 1.1) and not is_hard_scam(_g_why):
                    return True, ""

            if _age_min < 15.0:
                if not _g_ok and is_hard_scam(_g_why):
                    return False, f"контракт {chain}: GoPlus {_g_why}"
                try:
                    import honeypot_is as _hp
                    if not _h_ok and _hp.is_hard_scam(_h_why):
                        return False, f"контракт {chain}: Honeypot.is {_h_why}"
                except Exception:
                    pass
                if s5 < 4 or b5 < s5 * 1.2 or vol5 < 3000:
                    return False, f"контракт {chain} не верифицирован (GoPlus: {_g_why}, Honeypot: {_h_why}) и слабый стакан — ждём проверки"
                return True, ""
        return False, f"контракт {chain} не подтверждён по безопасности (GoPlus: {_g_why})"

    if chain == "bsc":
        # На BSC НЕТ лазеек: если GoPlus или Honeypot.is не подтвердили контракт и лок LP — вход СТРОГО ЗАПРЕЩЕН!
        _fail_reasons = []
        if not _g_ok or _g_why:
            _fail_reasons.append(f"GoPlus: {_g_why or 'не подтвержден'}")
        if not _h_ok or _h_why:
            _fail_reasons.append(f"Honeypot.is: {_h_why or 'симуляция провалена'}")
        return False, f"BSC отклонён по безопасности ({', '.join(_fail_reasons)})"

    return True, ""


# =============================================================================
# LAYER 7 & LAYER 8 (@savipww Auditor Extensions):
# - Layer 7: RugCheck /insiders/graph (реальные связанные кластеры финансирования/трансферов)
# - Layer 8: SPL Token Account Delegations (approve-дрейнер на топ-аккаунтах через getMultipleAccounts)
# =============================================================================
_DELEGATION_CACHE: dict = {}  # mint -> (ts, (ok, reason))
_INSIDER_GRAPH_CACHE: dict = {}  # mint -> (ts, (ok, reason))


async def check_spl_holder_delegations(mint: str, holder_token_accounts: list = None, total_supply_ui: float = 1_000_000_000.0) -> tuple:
    """
    Layer 8 Auditor: Проверяет скрытые SPL/Token-2022 делегации (approve drainers) у топ-20 держателей.
    В отличие от тяжелого getProgramAccounts (который банят публичные RPC), использует 1 легкий вызов
    getMultipleAccounts(jsonParsed) по топ-20 токен-аккаунтам (~60мс на любом RPC!).
    """
    if not mint:
        return True, ""
    now = _t.time()
    cached = _DELEGATION_CACHE.get(mint)
    if cached and (now - cached[0]) < 180.0:
        return cached[1]

    import aiohttp
    from collections import Counter
    from http_client import get_session

    rpcs = _get_solana_rpcs()

    SAFE_DELEGATES = {
        "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",
        "D4FPEruKEHrG5NTsqazoqfv8zsFWApqTjuzFLUQDkTz5",
        "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB",
        "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",
        "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
        "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1",
        "11111111111111111111111111111111",
    }

    max_del_accs = int(_cfg("AUDITOR_MAX_DELEGATED_ACCOUNTS", 3))
    max_del_pct = float(_cfg("AUDITOR_MAX_DELEGATED_PCT", 5.0))

    try:
        session = await get_session()
        accs = [str(a).strip() for a in (holder_token_accounts or []) if a and str(a).strip()][:20]

        # Если список токен-аккаунтов не передан — берем топ-20 напрямую через getTokenLargestAccounts
        if not accs:
            largest_payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenLargestAccounts", "params": [mint]}
            for rpc in rpcs:
                try:
                    async with session.post(rpc, json=largest_payload, timeout=aiohttp.ClientTimeout(total=3.0)) as resp:
                        if resp.status == 200:
                            ldata = await resp.json()
                            vals = ((ldata.get("result") or {}).get("value")) or []
                            accs = [str(x.get("address") or "").strip() for x in vals[:20] if x.get("address")]
                            if accs:
                                break
                        else:
                            _note_rpc_status(rpc, resp.status)
                except Exception:
                    continue

        if not accs:
            return True, ""

        payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "getMultipleAccounts",
            "params": [accs[:20], {"encoding": "jsonParsed"}],
        }
        parsed_accounts = None
        for rpc in rpcs:
            try:
                async with session.post(rpc, json=payload, timeout=aiohttp.ClientTimeout(total=3.5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        val = (data.get("result") or {}).get("value")
                        if isinstance(val, list):
                            parsed_accounts = val
                            break
                    else:
                        _note_rpc_status(rpc, resp.status)
            except Exception:
                continue

        if not parsed_accounts:
            return True, ""

        delegate_counts = Counter()
        delegated_ui_total = 0.0
        for acc_obj in parsed_accounts:
            if not isinstance(acc_obj, dict):
                continue
            p_info = (((acc_obj.get("data") or {}).get("parsed") or {}).get("info")) or {}
            owner = str(p_info.get("owner") or "")
            delegate = str(p_info.get("delegate") or "").strip()
            if not delegate or delegate in SAFE_DELEGATES or owner in SAFE_DELEGATES:
                continue
            del_amt_obj = p_info.get("delegatedAmount") or {}
            del_ui = float(del_amt_obj.get("uiAmount") or 0.0)
            tok_ui = float((p_info.get("tokenAmount") or {}).get("uiAmount") or 0.0)
            eff_del_ui = min(del_ui, tok_ui) if del_ui > 0 else 0.0
            if eff_del_ui <= 0:
                continue
            delegate_counts[delegate] += 1
            delegated_ui_total += eff_del_ui

        if delegate_counts:
            top_del, cnt = delegate_counts.most_common(1)[0]
            if cnt >= max_del_accs:
                res = (False, f"spl-delegation-drain: {cnt} top wallets delegated to {top_del[:8]}... (drain trap)")
                _DELEGATION_CACHE[mint] = (now, res)
                return res
            if total_supply_ui > 0:
                del_pct = (delegated_ui_total / total_supply_ui) * 100.0
                if del_pct >= max_del_pct:
                    res = (False, f"spl-delegation-drain: {del_pct:.1f}% supply delegated to {top_del[:8]}... (>={max_del_pct:.0f}%)")
                    _DELEGATION_CACHE[mint] = (now, res)
                    return res

        res = (True, "")
        _DELEGATION_CACHE[mint] = (now, res)
        return res
    except Exception:
        return True, ""


async def check_rugcheck_insider_graph(mint: str, total_supply_raw: float = 0.0, pool_accounts: set = None) -> tuple:
    """
    Layer 7 Auditor: Проверяет реальный ончейн-граф связанных кошельков через RugCheck /insiders/graph.
    Отсеивает безобидных пылевых ботов (<0.35% на кошелек) и блокирует реальные инсайдерские бандлы,
    когда >= 5 связанных кошельков суммарно держат >= 12% эмиссии (или >= 4 кошельков держат >= 18%).
    """
    if not mint:
        return True, ""
    now = _t.time()
    cached = _INSIDER_GRAPH_CACHE.get(mint)
    if cached and (now - cached[0]) < 180.0:
        return cached[1]

    max_bundle_n = int(_cfg("AUDITOR_MAX_BUNDLE_WALLETS", 5))
    max_bundle_pct = float(_cfg("AUDITOR_MAX_BUNDLE_PCT", 12.0))
    excl = set(pool_accounts or set()) | {
        "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",
        "D4FPEruKEHrG5NTsqazoqfv8zsFWApqTjuzFLUQDkTz5",
        "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
        "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB",
        "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",
        "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
        "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1",
    }

    try:
        from http_client import fetch_json
        url = f"https://api.rugcheck.xyz/v1/tokens/{mint}/insiders/graph"
        st, data = await fetch_json(url, timeout=4, retries=0)
        if st != 200 or not isinstance(data, list):
            return True, ""

        # Стандартная эмиссия Pump.fun с 6 decimals = 1e15 raw (защита если передан UI supply 1e9 вместо raw 1e15)
        eff_supply = float(total_supply_raw) if total_supply_raw and total_supply_raw > 0 else 1_000_000_000_000_000.0
        if 0 < eff_supply < 1e12 and mint.endswith("pump"):
            eff_supply = eff_supply * 1e6

        hub_nets_count = 0
        hub_total_nodes = 0
        hub_total_pct = 0.0

        for net in data:
            if not isinstance(net, dict):
                continue
            nodes = net.get("nodes") or []
            links = net.get("links") or []
            if len(nodes) < 4:
                continue
            real_Cluster_pcts = []
            valid_participants = 0
            seen_ids = set()
            for nd in nodes:
                if not isinstance(nd, dict) or not nd.get("participant", True):
                    continue
                if str(nd.get("type") or "").lower() in ("token_account", "tokenaccount", "ata", "amm", "pool", "vault"):
                    continue
                nid = str(nd.get("id") or "")
                if not nid or nid in excl or nid in seen_ids:
                    continue
                seen_ids.add(nid)
                valid_participants += 1
                hold_raw = float(nd.get("holdings") or 0.0)
                if hold_raw <= 0:
                    continue
                pct = (hold_raw / eff_supply) * 100.0
                # Игнорируем пул (>50%) и пылевые аирдроп-кошельки (<0.35% эмиссии), чтобы не резать живые ракеты вроде ANSEM/REGRETAMINE
                if 0.35 <= pct <= 50.0:
                    real_Cluster_pcts.append(pct)

            n_real = len(real_Cluster_pcts)
            sum_pct = min(100.0, sum(real_Cluster_pcts))
            if (n_real >= max_bundle_n and sum_pct >= max_bundle_pct) or (n_real >= 4 and sum_pct >= max_bundle_pct * 1.5):
                res = (False, f"insider-graph-bundle: {n_real} linked wallets hold {sum_pct:.1f}% supply")
                _INSIDER_GRAPH_CACHE[mint] = (now, res)
                return res

            # Детектор Collector-Hub сетей (кейсы SOLFLY и FYAT: по 10-12 суб-кошельков покупают лесенкой и сливают токены на 1 хаб-кошелёк,
            # поэтому у отправителей holdings=0, а на хабе аккумулируется 0.7%..5% эмиссии в каждой из 4-10 сетей):
            if valid_participants >= 4 and len(links) >= 3 and 0.35 <= sum_pct <= 35.0:
                hub_nets_count += 1
                hub_total_nodes += valid_participants
                hub_total_pct += sum_pct
                if valid_participants >= 8 and sum_pct >= 8.0:
                    res = (False, f"insider-collector-hub: {valid_participants} linked sub-wallets consolidated {sum_pct:.1f}% supply into hub")
                    _INSIDER_GRAPH_CACHE[mint] = (now, res)
                    return res

        if hub_nets_count >= 3 and hub_total_nodes >= 15 and hub_total_pct >= 8.0:
            res = (
                False,
                f"insider-collector-swarm: {hub_nets_count} transfer hubs ({hub_total_nodes} linked sub-wallets) consolidated {hub_total_pct:.1f}% supply (staircase rug)",
            )
            _INSIDER_GRAPH_CACHE[mint] = (now, res)
            return res

        res = (True, "")
        _INSIDER_GRAPH_CACHE[mint] = (now, res)
        return res
    except Exception:
        return True, ""


_STAIRCASE_CACHE: dict = {}  # key -> (ts, (ok, reason))


def analyze_trade_tape_staircase(trades_raw: list) -> tuple:
    """
    Чистый детерминированный разбор ленты сделок пула (Layer 9 Auditor, GMGN/Axiom Anti-Bundler).
    Ловит:
    1) Синхронные пачки продаж Jito-бандлера (4+ разных кошельков в 1 секунду на тонкой ленте или 8+ в любую секунду — кейс NOTHING 20:17:58Z и 20:19:04Z).
    2) Синхронные пачки покупок бандлера в 1 блок/секунду (7+ разных кошельков).
    3) Клонированные некруглые размеры покупок суб-кошельков (4+ кошельков с точностью до 0.0001 SOL).
    4) Искусственную «лесенку» (Staircase Bump-Bot): >=30 покупок, >=90% одноразовых кошельков, низкий разброс CV<=0.72, дефицит мелкого ритейла (<$18 <=22%) и >=58% ордеров в узком коридоре бота ($30-$145).
    Возвращает (is_clean: bool, reason: str, recent_buyer_wallets: list).
    """
    from collections import defaultdict
    import statistics

    if not isinstance(trades_raw, list) or not trades_raw:
        return True, "", []

    buys = []
    sells = []
    for item in trades_raw:
        attrs = (item.get("attributes") if isinstance(item, dict) and "attributes" in item else item) or {}
        if not isinstance(attrs, dict):
            continue
        kind = str(attrs.get("kind") or "").lower()
        if kind == "buy":
            buys.append(attrs)
        elif kind == "sell":
            sells.append(attrs)

    # 1. Синхронный сброс суб-кошельков в одну и ту же секунду (Jito Sell-All / Skim Burst: кейсы NOTHING 73/s, GETAJOB 16/s, 14/s, 14/s)
    sell_by_sec = defaultdict(set)
    for s in sells:
        ts = str(s.get("block_timestamp") or "")
        w = str(s.get("tx_from_address") or "")
        if ts and w:
            sell_by_sec[ts].add(w)
    if sell_by_sec:
        top_s_ts, top_s_wallets = max(sell_by_sec.items(), key=lambda kv: len(kv[1]))
        max_s_1s = len(top_s_wallets)
        multi_sec_bursts = sum(1 for ws in sell_by_sec.values() if len(ws) >= 6)
        # 10+ уникальных продавцов в одну секунду ИЛИ 2+ секунды с синхронными пачками по 6+ продавцов (кейс GETAJOB: 16, 14, 14, 11)
        # На реальных ракетах (ASTRALIS max=3, FYAT max=6 один раз) этого не бывает!
        if max_s_1s >= 10 or multi_sec_bursts >= 2 or (len(sells) >= 20 and (max_s_1s / float(len(sells))) >= 0.35 and max_s_1s >= 7):
            return False, f"bundler-sync-sells: {max_s_1s} wallets sold in 1s ({top_s_ts}, bursts={multi_sec_bursts})", []

    # 1.5. Детектор 1-Shot Fast-Flip Wash Bot (кейс BALLFIN: 54 из 126 кошельков сделали ровно 1 buy >= $35 и 1 sell через 1-30с для накрутки фейкового объёма $60k/m перед рагом)
    try:
        from datetime import datetime as _dt_tape
        w_tape = defaultdict(list)
        for t_item in trades_raw:
            t_attrs = (t_item.get("attributes") if isinstance(t_item, dict) else None) or t_item
            if not isinstance(t_attrs, dict):
                continue
            w_addr = str(t_attrs.get("tx_from_address") or "")
            k_type = str(t_attrs.get("kind") or "").lower()
            ts_str = str(t_attrs.get("block_timestamp") or "")
            u_val = float(t_attrs.get("volume_in_usd") or 0.0)
            if w_addr and k_type in ("buy", "sell") and ts_str:
                try:
                    t_epoch = _dt_tape.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
                    w_tape[w_addr].append((t_epoch, k_type, u_val))
                except Exception:
                    pass
        if len(w_tape) >= 12:
            fast_flip_1shot = 0
            for w_addr, evs in w_tape.items():
                evs.sort(key=lambda x: x[0])
                b_evs = [e for e in evs if e[1] == "buy"]
                s_evs = [e for e in evs if e[1] == "sell"]
                if len(b_evs) == 1 and len(s_evs) == 1 and b_evs[0][2] >= 35.0:
                    dt_flip = s_evs[0][0] - b_evs[0][0]
                    if 0.0 <= dt_flip <= 30.0:
                        fast_flip_1shot += 1
            flip_ratio = fast_flip_1shot / float(len(w_tape))
            if fast_flip_1shot >= 8 or (fast_flip_1shot >= 5 and flip_ratio >= 0.20):
                return (
                    False,
                    f"wash-volume-fast-flip-bot: {fast_flip_1shot}/{len(w_tape)} ({flip_ratio*100:.0f}%) one-shot wallets bought & sold within <=30s",
                    [],
                )
    except Exception:
        pass

    # 2. Синхронный закуп бандлера в одну секунду
    buy_by_sec = defaultdict(set)
    b_wallets = []
    b_usd = []
    nonround_sol_wallets = defaultdict(set)
    COMMON_PRESETS = [0.05, 0.1, 0.2, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 5.0, 10.0]
    for b in buys:
        ts = str(b.get("block_timestamp") or "")
        w = str(b.get("tx_from_address") or "")
        usd = float(b.get("volume_in_usd") or 0.0)
        sol_amt = float(b.get("from_token_amount") or 0.0)
        if ts and w:
            buy_by_sec[ts].add(w)
        if w:
            b_wallets.append(w)
        if usd > 0:
            b_usd.append(usd)
        # Исключаем человеческие пресеты (0.1, 0.5, 1.0 SOL) и стандартные вычеты комиссий (0.0978, 0.4938 SOL)
        if w and 0.35 <= sol_amt <= 25.0:
            is_preset = any(abs(sol_amt - p) <= 0.018 for p in COMMON_PRESETS)
            is_round = (abs(sol_amt * 10.0 - round(sol_amt * 10.0)) <= 0.02)
            if not is_preset and not is_round:
                nonround_sol_wallets[round(sol_amt, 4)].add(w)

    if buy_by_sec and len(buys) <= 120:
        top_b_ts, top_b_wallets = max(buy_by_sec.items(), key=lambda kv: len(kv[1]))
        if len(top_b_wallets) >= 12:
            return False, f"bundler-sync-buys: {len(top_b_wallets)} wallets bought in 1s ({top_b_ts})", []

    # 3. Клонированные некруглые покупки бота (например 0.8459 SOL с 6+ разных суб-кошельков дева, как в NOTHING)
    if nonround_sol_wallets:
        best_amt, amt_wallets = max(nonround_sol_wallets.items(), key=lambda kv: len(kv[1]))
        if len(amt_wallets) >= 6:
            return False, f"staircase-cloned-buys: {len(amt_wallets)} wallets bought identical {best_amt:.4f} SOL", []

    # 4. Статистический профиль искусственной «лесенки» (Staircase Bot: кейс NOTHING)
    if len(b_usd) >= 30 and len(b_wallets) >= 30:
        uniq_r = len(set(b_wallets)) / float(len(b_wallets))
        small_r = sum(1 for v in b_usd if v < 18.0) / float(len(b_usd))
        mid_r = sum(1 for v in b_usd if 30.0 <= v <= 145.0) / float(len(b_usd))
        mean_u = statistics.mean(b_usd)
        cv = (statistics.pstdev(b_usd) / mean_u) if mean_u > 0 else 9.9
        if uniq_r >= 0.92 and cv <= 0.65 and small_r <= 0.18 and mid_r >= 0.65:
            return (
                False,
                f"staircase-bot-pump: {uniq_r*100:.0f}% one-shot wallets, {mid_r*100:.0f}% uniform $30-$145 buys, CV={cv:.2f}",
                [],
            )

    uniq_recent_buyers = list(dict.fromkeys(b_wallets))[:15]
    return True, "", uniq_recent_buyers


async def check_staircase_and_sybil_farm(mint: str, pair_address: str = "", holder_owners: list = None) -> tuple:
    """
    Layer 9 + Layer 10 Auditor (Защита от Staircase-ботов и ферм одноразовых суб-кошельков дева):
    - Layer 9: Анализ последних сделок пула через GeckoTerminal /trades (синхронные сбросы Jito, лесенка ордеров).
    - Layer 10: Мгновенная (~60мс) проверка нативных SOL-балансов топ-холдеров/покупателей через RPC getMultipleAccounts:
      если >=85% кошельков сидят только на газовой пыли (<0.08 SOL) и ни у одного нет >=0.20 SOL — это ботоферма суб-кошельков!
    """
    if not mint or not _cfg("AUDITOR_STAIRCASE_ENABLED", True):
        return True, ""

    now = _t.time()
    cache_key = f"{mint}:{pair_address or ''}"
    cached = _STAIRCASE_CACHE.get(cache_key)
    if cached and (now - cached[0]) < 90.0:
        return cached[1]
    if len(_STAIRCASE_CACHE) > 1500:
        _STAIRCASE_CACHE.clear()

    recent_buyers = []
    # --- LAYER 9: Лента сделок пула (GeckoTerminal /trades через единый пейсер market_data) ---
    clean_pair = str(pair_address or "").replace("solana_", "").strip()
    if clean_pair and len(clean_pair) >= 32 and not clean_pair.startswith("0x"):
        try:
            import market_data
            gt_resp = await market_data._gt_get(f"/networks/solana/pools/{clean_pair}/trades", retries=0, timeout=5)
            trades_list = (gt_resp or {}).get("data") if isinstance(gt_resp, dict) else None
            if isinstance(trades_list, list) and trades_list:
                t_ok, t_why, recent_buyers = analyze_trade_tape_staircase(trades_list)
                if not t_ok:
                    res = (False, t_why)
                    _STAIRCASE_CACHE[cache_key] = (now, res)
                    return res
        except Exception:
            pass

    # --- LAYER 10: RPC getMultipleAccounts проверка фермы газовой пыли у холдеров/покупателей (~60мс) ---
    SAFE_SYSTEM_OWNERS = {
        "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",
        "D4FPEruKEHrG5NTsqazoqfv8zsFWApqTjuzFLUQDkTz5",
        "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB",
        "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",
        "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
        "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1",
        "11111111111111111111111111111111",
        clean_pair,
    }
    candidate_wallets = []
    for w in list(holder_owners or []) + list(recent_buyers or []):
        ws = str(w or "").strip()
        if len(ws) >= 32 and not ws.startswith("0x") and ws not in SAFE_SYSTEM_OWNERS and ws not in candidate_wallets:
            candidate_wallets.append(ws)
        if len(candidate_wallets) >= 15:
            break

    if len(candidate_wallets) >= 8:
        try:
            import aiohttp
            from http_client import get_session
            session = await get_session()
            payload = {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "getMultipleAccounts",
                "params": [candidate_wallets[:15], {"encoding": "base64"}],
            }
            vals = None
            for rpc in _get_solana_rpcs():
                try:
                    async with session.post(rpc, json=payload, timeout=aiohttp.ClientTimeout(total=3.0)) as resp:
                        if resp.status == 200:
                            rdata = await resp.json()
                            v = (rdata.get("result") or {}).get("value")
                            if isinstance(v, list):
                                vals = v
                                break
                        else:
                            _note_rpc_status(rpc, resp.status)
                except Exception:
                    continue
            if isinstance(vals, list) and len(vals) >= 8:
                sols = [float((acc.get("lamports", 0) if isinstance(acc, dict) else 0) or 0) / 1e9 for acc in vals]
                max_sol = max(sols) if sols else 0.0
                dust_cnt = sum(1 for s in sols if s < 0.010)
                dust_ratio = dust_cnt / float(len(sols))
                if max_sol < 0.035 and dust_ratio >= 0.90:
                    res = (
                        False,
                        f"sybil-gas-dust-farm: {dust_cnt}/{len(sols)} wallets hold <0.010 SOL (max {max_sol:.3f} SOL — disposable sub-wallets)",
                    )
                    _STAIRCASE_CACHE[cache_key] = (now, res)
                    return res
        except Exception:
            pass

    res = (True, "")
    _STAIRCASE_CACHE[cache_key] = (now, res)
    return res


_RC_REPORT_CACHE: dict = {}  # mint -> (ts, status, data_dict_or_none)


async def fetch_rugcheck_report_cached(mint: str, ttl: float = 120.0, timeout: int = 7) -> tuple:
    """Единый кэшированный запрос к RugCheck /v1/tokens/{mint}/report для всех движков (SCANNER, JUP, GROK-JEV, COPY).
    Экономит лимит 30 RPM на api.rugcheck.xyz и исключает ложные блокировки при параллельных проверках одного минта."""
    if not mint:
        return 0, None
    now = _t.time()
    if len(_RC_REPORT_CACHE) > 1500:
        _RC_REPORT_CACHE.clear()
    hit = _RC_REPORT_CACHE.get(mint)
    if hit:
        ts, st, data = hit
        eff_ttl = ttl if (st == 200 and isinstance(data, dict)) else (15.0 if st in (400, 404) else 5.0)
        if (now - ts) < eff_ttl:
            return st, data
    from http_client import fetch_json
    url = f"https://api.rugcheck.xyz/v1/tokens/{mint}/report"
    st, body = await fetch_json(url, timeout=timeout, retries=1)
    if st == 200 and isinstance(body, dict):
        _RC_REPORT_CACHE[mint] = (now, 200, body)
        return 200, body
    _RC_REPORT_CACHE[mint] = (now, int(st or 0), None)
    return int(st or 0), None
