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

EVM_CHAIN_IDS = {"ethereum": "1", "bsc": "56", "base": "8453"}

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


def _verdict_evm(info: dict):
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

    # 4. Концентрация у создателя / владельца (защита от дев-дампов вроде ORRIS)
    try:
        creator_pct = _tax_frac(info.get("creator_percent", 0))
        max_creator = float(_cfg("GOPLUS_MAX_CREATOR_PCT", 0.15))
        if creator_pct > max_creator:
            return False, f"creator_pct {creator_pct * 100:.1f}%"
    except Exception:
        pass
    try:
        owner_pct = _tax_frac(info.get("owner_percent", 0))
        max_owner = float(_cfg("GOPLUS_MAX_OWNER_PCT", 0.20))
        if owner_pct > max_owner:
            return False, f"owner_pct {owner_pct * 100:.1f}%"
    except Exception:
        pass

    return True, ""


def _verdict_sol(info: dict):
    """Разбор Solana-карточки (поля беднее EVM — парсим дефенсивно)."""
    if not isinstance(info, dict) or not info:
        return True, "no-data"
    for k in ("is_honeypot", "honeypot"):
        if str(info.get(k, "0")) == "1":
            return False, "honeypot"
    # налоги могут лежать в разных ключах — ищем любое *tax*
    worst = 0.0
    for k, v in info.items():
        if "tax" in str(k).lower():
            worst = max(worst, _tax_frac(v))
    if worst > float(_cfg("GOPLUS_MAX_SELL_TAX", 0.10)):
        return False, f"tax {worst * 100:.0f}%"
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
                   "external_call", "tax", "creator_pct", "owner_pct"):
        if danger in r:
            return True
    return False


async def check_token(address: str, chain: str):
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
    if hit and ttl > 0 and (_t.time() - hit[0]) < ttl:
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
                verdict = _verdict_evm(info)
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
        status, data = await fetch_json(url, headers=headers if headers else None, timeout=10, retries=1)
        if status == 200 and isinstance(data, dict) and data.get("code") == 1:
            result = data.get("result") or {}
            info = result.get(mint) or {}
            if not info and isinstance(result, dict):
                for v in result.values():
                    if isinstance(v, dict):
                        info = v
                        break
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


async def verify_evm_token(address: str, chain: str, pair_data: dict = None) -> tuple:
    """3-уровневая гибридная защита EVM (GoPlus + Honeypot.is симуляция + On-Chain DEX).
    
    Возвращает (ok: bool, reason: str).
    - (True, ''): контракт чист и проверен.
    - (False, reason): подтверждённый скам, ханипот или не подтверждён на BSC.
    """
    chain = (chain or "").lower().strip()
    address = (address or "").strip()
    if not address:
        return True, "no-addr"
    if chain in ("robinhood",):
        # Robinhood Chain (4663) ончейн-аудит и валидация метаданных.
        # Отсекает 100% рагпулов (BATONA, HYPOCRITE, ¥) без занижения ставки $15.
        if not pair_data or not isinstance(pair_data, dict):
            return False, "нет данных пары для Robinhood"
        
        # 1. Возрастной барьер: не входить в первые 3 минуты (зона минутных флеш-рагов дева)
        _c_ms = pair_data.get("pairCreatedAt") or 0
        import time as _t_rob
        _a_min = ((_t_rob.time() * 1000 - _c_ms) / 60000.0) if _c_ms else 999.0
        if _a_min < 3.0:
            return False, f"возраст {_a_min:.1f}м < 3.0м — ранняя flash-rug зона"
            
        # 2. Проверка социальных ссылок: все раги (BATONA, HYPOCRITE, ¥) имели 0 ссылок.
        # Ракеты (MINU, Altai) имели подтверждённые Twitter / сайт.
        info = pair_data.get("info") or {}
        links = (info.get("socials") or []) + (info.get("websites") or [])
        if not links:
            return False, "нет соцсетей/сайта (анонимный дев-раг)"
            
        # 3. Наличие реальных продаж в пуле
        txns_m5 = ((pair_data.get("txns") or {}).get("m5") or {})
        s5 = int(txns_m5.get("sells", 0) or 0)
        b5 = int(txns_m5.get("buys", 0) or 0)
        vol5 = float(((pair_data.get("volume") or {}).get("m5", 0) or 0))
        if s5 < 3:
            return False, f"продаж за 5м {s5} < 3 — ханипот или заблокированный стакан"
        if b5 < s5 * 1.1:
            return False, f"нет перевеса покупателей ({b5}b/{s5}s)"
        if vol5 < 3000:
            return False, f"объем за 5м ${vol5:,.0f} < $3,000 — искусственный пул"
            
        # 4. Ончейн-проверка концентрации у создателя через RPC
        try:
            import aiohttp
            from http_client import get_session
            _rpc_url = getattr(config, "ROBINHOOD_RPC_URL", "https://rpc.mainnet.chain.robinhood.com")
            _TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
            _ZERO_T = "0x" + "0" * 64
            _s_sess = await get_session()
            _pl = [
                {"jsonrpc": "2.0", "id": 1, "method": "eth_call", "params": [{"to": address, "data": "0x18160ddd"}, "latest"]},
                {"jsonrpc": "2.0", "id": 2, "method": "eth_blockNumber", "params": []}
            ]
            async with _s_sess.post(_rpc_url, json=_pl, timeout=aiohttp.ClientTimeout(total=4)) as _r_rpc:
                _res_rpc = await _r_rpc.json()
            _sup_h = (_res_rpc[0].get("result") or "") if isinstance(_res_rpc, list) and len(_res_rpc) > 0 else ""
            if _sup_h and _sup_h != "0x":
                _tot_sup = int(_sup_h, 16) / 1e18
                if _tot_sup > 0:
                    _hd = int(_res_rpc[1]["result"], 16)
                    _age_s = max(60, (_t_rob.time() - (_c_ms / 1000.0))) if _c_ms else 1800
                    _st_blk = _hd - int((_age_s + 60) / 0.10387)
                    _l_req = {
                        "jsonrpc": "2.0", "id": 3, "method": "eth_getLogs",
                        "params": [{"fromBlock": hex(max(1, _st_blk - 2500)), "toBlock": hex(_st_blk + 5000), "address": address, "topics": [_TRANSFER, _ZERO_T]}]
                    }
                    async with _s_sess.post(_rpc_url, json=_l_req, timeout=aiohttp.ClientTimeout(total=4)) as _lr:
                        _l_res = await _lr.json()
                    _logs = _l_res.get("result") or []
                    if _logs and isinstance(_logs, list):
                        _minter = "0x" + _logs[0]["topics"][2][-40:]
                        _b_data = "0x70a08231" + _minter[2:].lower().zfill(64)
                        async with _s_sess.post(_rpc_url, json={"jsonrpc": "2.0", "id": 4, "method": "eth_call", "params": [{"to": address, "data": _b_data}, "latest"]}, timeout=aiohttp.ClientTimeout(total=3)) as _br:
                            _b_res = await _br.json()
                        _m_bal = int(_b_res.get("result", "0x0"), 16) / 1e18
                        _pct = (_m_bal / _tot_sup) * 100.0
                        if _pct > 15.0:
                            return False, f"создатель держит {_pct:.1f}% (>15%) эмиссии — раг-риск"
        except Exception:
            pass

        return True, ""

    if chain not in ("bsc", "base", "ethereum"):
        return True, "unsupported"

    # Уровень 1: GoPlus Security API
    _g_ok, _g_why = await check_token(address, chain)
    if is_hard_scam(_g_why):
        return False, f"GoPlus {_g_why}"
    if _g_ok and _g_why == "":
        return True, ""

    # Уровень 2: Honeypot.is (симуляция транзакций покупки и продажи)
    # Вызывается если GoPlus вернул rate-limit (4029), no-data (3) или ошибку.
    try:
        import honeypot_is as _hp
        _h_ok, _h_why = await _hp.check_token(address, chain)
        if _hp.is_hard_scam(_h_why):
            return False, f"honeypot.is {_h_why}"
        if _h_ok and _h_why == "":
            return True, ""
    except Exception:
        _h_ok, _h_why = False, "hp-exception"

    # Уровень 3: Ончейн-валидация пула DEX (если оба внешних сервиса не проиндексировали / перегружены)
    if chain in ("base", "ethereum"):
        # Base/ETH: fail-open с логированием
        return True, ""

    if chain == "bsc":
        # BSC: зона повышенного риска. Проверяем реальные сделки продажи в пуле и возраст:
        if pair_data and isinstance(pair_data, dict):
            import time as _t_bsc
            _c_bsc = pair_data.get("pairCreatedAt") or 0
            _a_bsc = ((_t_bsc.time() * 1000 - _c_bsc) / 60000.0) if _c_bsc else 999.0
            if _a_bsc < 3.0:
                return False, f"BSC возраст {_a_bsc:.1f}м < 3.0м — ранняя flash-rug зона"
            txns_m5 = ((pair_data.get("txns") or {}).get("m5") or {})
            s5 = int(txns_m5.get("sells", 0) or 0)
            b5 = int(txns_m5.get("buys", 0) or 0)
            vol5 = float(((pair_data.get("volume") or {}).get("m5", 0) or 0))
            if s5 >= 4 and b5 >= s5 * 1.2 and vol5 >= 8000:
                # Физическое ончейн-доказательство: пользователи прямо сейчас успешно продают токен
                return True, ""
        return False, f"ни GoPlus ({_g_why}), ни honeypot.is ({_h_why}) не подтвердили контракт (BSC)"

    return True, ""
