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
        return False, "blacklist"
    if str(info.get("trading_cooldown", "0")) == "1":
        return False, "trading_cooldown"

    # 2. Бэкдоры владельца и бесконечный минт
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
        max_creator = float(_cfg("ROBINHOOD_MAX_CREATOR_PCT", 0.20) if chain == "robinhood" else _cfg("GOPLUS_MAX_CREATOR_PCT", 0.08))
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
    # На Robinhood GoPlus не индексирует PoolManager LP.
    # На Base: проверяем LP для стандартных Uniswap V2 пулов (защита от рагпулла CHUBBY).
    # Исключение только для V3/V4 / Aerodrome Slipstream / Clanker NFT-пулов.
    _dex = str((pair_data or {}).get("dexId", "")).lower()
    _labels = [str(lb).lower() for lb in ((pair_data or {}).get("labels") or [])]
    _p_addr_v = str((pair_data or {}).get("pairAddress") or "")
    _is_nft_pool = any(v in _dex for v in ("v3", "v4", "slipstream")) or any("v3" in lb or "v4" in lb or "slipstream" in lb for lb in _labels) or len(_p_addr_v) > 42
    if chain not in ("robinhood",) and (chain != "base" or not _is_nft_pool):
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
                min_lp_lock = float(_cfg("BSC_MIN_LP_LOCKED_PCT", 0.70) if chain == "bsc" else _cfg("GOPLUS_MIN_LP_LOCKED_PCT", 0.40))
                if locked_burned < min_lp_lock:
                    return False, f"LP не заблокирован ({locked_burned*100:.0f}% < {min_lp_lock*100:.0f}%) — риск рагпулла"
            elif pair_data is not None and not _is_nft_pool:
                return False, f"lp-unindexed (GoPlus ещё не видит лок LP пула V2 на {chain})"
        except Exception as _e_lp:
            if chain == "bsc":
                return False, f"ошибка проверки лока LP на BSC ({type(_e_lp).__name__})"

    return True, ""


RAW_SOLANA_INFO: dict = {}  # mint -> raw goplus dict


def get_solana_holder_stats(mint: str) -> dict:
    """Извлекает метрики холдеров из кэша GoPlus Solana для анализатора."""
    info = RAW_SOLANA_INFO.get(mint) or {}
    if not info:
        return {"top_10_pct": 0.0, "dev_pct": 0.0, "has_data": False}
    holders = info.get("holders") or []
    if not isinstance(holders, list) or not holders:
        return {"top_10_pct": 0.0, "dev_pct": 0.0, "has_data": False}
    top10_sum = 0.0
    dev_pct = 0.0
    valid_holders = []
    for h in holders:
        pct = _tax_frac(h.get("percent", 0)) * 100.0
        tag = str(h.get("tag", "")).lower()
        if "raydium" in tag or "meteora" in tag or "orca" in tag or "pump" in tag or pct > 65.0:
            continue
        valid_holders.append(pct)
    if valid_holders:
        dev_pct = valid_holders[0]
        top10_sum = sum(valid_holders[:10])
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
    if str(info.get("default_account_state", "0")) in ("1", "frozen"):
        return False, "default_account_frozen (Token-2022 счета покупателей заморожены)"
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
    if str(info.get("balance_mutable_authority", "0")) in ("1", "true"):
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

    # 6. Концентрация у китов и топ-10 холдеров (защита от Cabal/Инсайдерских дампов)
    holders = info.get("holders") or []
    if holders and isinstance(holders, list):
        top10_sum = 0.0
        for h in holders[:10]:
            pct = _tax_frac(h.get("percent", 0))
            tag = str(h.get("tag", "")).lower()
            if "raydium" in tag or "meteora" in tag or "orca" in tag or "pump" in tag or pct > 0.65:
                continue
            max_single = float(_cfg("SOLANA_MAX_SINGLE_WHALE_PCT", 0.08))
            if pct > max_single:
                return False, f"кит держит {pct*100:.1f}% (>{max_single*100:.0f}%) — риск дампа"
            top10_sum += pct
        max_top10 = float(_cfg("SOLANA_MAX_TOP10_PCT", 0.35))
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
                   "кит", "дамп", "freezable", "closable", "balance_mutable", "кабал",
                   "frozen", "non_transferable", "transfer_hook", "default_account"):
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


async def check_solana(mint: str):
    """Solana-гейт: (True, '') — можно брать; (False, причина) — блок."""
    if not _cfg("GOPLUS_ENABLED", True):
        return True, "off"
    if not mint:
        return True, "no-addr"
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
                "83e2007e": "isBlacklisted",
                "8456cb59": "пауза торговли pause()",
                "03a118d5": "пауза торговли pause(bool)",
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

        # 2. Проверка реальной ликвидности пула (Uniswap V4 PoolManager vs V2/V3 Pair & LP Burn)
        if 2 in _res_map and 3 in _res_map:
            _tot_h = _res_map[2].get("result", "0x")
            _pm_h = _res_map[3].get("result", "0x")
            if _tot_h and _tot_h != "0x":
                _tot_v = int(_tot_h, 16)
                _pm_v = int(_pm_h, 16) if _pm_h and _pm_h != "0x" else 0
                if _tot_v > 0 and _is_v4:
                    _frac = _pm_v / _tot_v
                    if _frac < 0.005:
                        return False, f"пул Uniswap V4 опустошён девом (остаток {_frac*100:.4f}% — V4 LP Rug)"
                    if not is_open_pos:
                        # Токен обязан держать >= 8% эмиссии в V4 PoolManager (нормальные пулы 13-16%, микро-пулы/ранний слив < 8%).
                        _min_v4_frac = 0.08
                        if _frac < _min_v4_frac:
                            return False, f"картонный пул Uniswap V4 (в пуле всего {_frac*100:.1f}% эмиссии < {_min_v4_frac*100:.0f}% — V4 LP-rug риск)"
                if _tot_v > 0 and not _is_v4 and 4 in _res_map:
                    _pair_h = _res_map[4].get("result", "0x")
                    _pair_v = int(_pair_h, 16) if _pair_h and _pair_h != "0x" else 0
                    _p_frac = _pair_v / _tot_v
                    if _p_frac < 0.005:
                        return False, f"пул V2/V3 опустошён девом (остаток {_p_frac*100:.4f}% — LP Rug)"
                    if not is_open_pos and not _has_reputation and chain in ("robinhood", "base"):
                        if _p_frac < 0.05:
                            return False, f"картонный пул V2/V3 (в пуле всего {_p_frac*100:.1f}% эмиссии < 5% без соцсетей — схема LP-rug)"
        if len(_pair_addr) == 42 and chain in ("robinhood", "base", "bsc"):
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
                        if _burnt_pct < 80.0 and (chain == "robinhood" or not _has_reputation):
                            return False, f"ликва пула V2 не сожжена ({_burnt_pct:.1f}% < 80%) — риск вывода ликвидности девом"
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
    if chain in ("robinhood",):
        # Robinhood Chain (4663) ончейн-аудит безопасности и honeypot:
        if not pair_data or not isinstance(pair_data, dict):
            return False, "нет данных пары для Robinhood"
        
        # 1. Защита от Honeypot: проверка продаваемости
        txns_m5 = ((pair_data.get("txns") or {}).get("m5") or {})
        txns_h1 = ((pair_data.get("txns") or {}).get("h1") or {})
        s5 = int(txns_m5.get("sells", 0) or 0)
        s1 = int(txns_h1.get("sells", 0) or 0)
        b5 = int(txns_m5.get("buys", 0) or 0)
        if s5 == 0 and s1 == 0 and b5 >= 6:
            return False, f"0 продаж при {b5} покупках — Honeypot (нельзя продать)"
        if (b5 + s5) >= 25 and (s5 / (b5 + s5)) < 0.04:
            return False, f"аномалия продаж m5 {s5}s/{b5}b — honeypot-ловушка"

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

    # Если GoPlus подтвердил токен И Honeypot.is подтвердил (или unsupported) — чистый вход
    if _g_ok and _g_why == "" and _h_ok:
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
            _p_addr = str(pair_data.get("pairAddress") or "")
            _is_nft_p = any(v in _dex_id for v in ("v3", "v4", "slipstream")) or any("v3" in lb or "slipstream" in lb for lb in _lbls) or len(_p_addr) > 42
            
            if not _is_nft_p:
                if not _g_ok or _g_why != "":
                    return False, f"пул V2 на {chain} не верифицирован GoPlus по LP-локу ({_g_why or 'no-data'}) — защита от LP-рагпулла"

            # Для проверенных NFT-пулов (V3/V4/Aerodrome Slipstream/Clanker):
            if _is_nft_p:
                if "v4" in _lbls or len(_p_addr) > 42:
                    if not _g_ok and is_hard_scam(_g_why):
                        return False, f"пул Uniswap V4 на {chain}: {_g_why}"
                if _h_ok and _h_why == "" and s5 >= 2:
                    return True, ""
                if s5 >= 2 and b5 >= s5:
                    return True, ""

            if _age_min < 15.0:
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
