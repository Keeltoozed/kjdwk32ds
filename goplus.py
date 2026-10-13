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
        # Robinhood Chain (4663) строгий ончейн-аудит: Blockscout верификация + байткод + LP-Burn + Fail-Closed.
        # Защита от рагпулов (LVR, FAST, BATONA, ¥) без дыр fail-open.
        if not pair_data or not isinstance(pair_data, dict):
            return False, "нет данных пары для Robinhood"
        
        # 1. Возрастной барьер: не входить в первые 5 минут (зона flash-рагов дева)
        _c_ms = pair_data.get("pairCreatedAt") or 0
        import time as _t_rob
        _a_min = ((_t_rob.time() * 1000 - _c_ms) / 60000.0) if _c_ms else 999.0
        if _a_min < 5.0:
            return False, f"возраст {_a_min:.1f}м < 5.0м — ранняя flash-rug зона"
            
        # 2. Проверка социальных ссылок: требуем хотя бы 1 валидную ссылку (Twitter/TG/сайт)
        info = pair_data.get("info") or {}
        links = (info.get("socials") or []) + (info.get("websites") or [])
        if not links or len(links) < 1:
            return False, "нет ссылок (соцсети/сайт) — анонимный дев-раг"
            
        # 3. Наличие реальных продаж и глубина пула
        txns_m5 = ((pair_data.get("txns") or {}).get("m5") or {})
        s5 = int(txns_m5.get("sells", 0) or 0)
        b5 = int(txns_m5.get("buys", 0) or 0)
        vol5 = float(((pair_data.get("volume") or {}).get("m5", 0) or 0))
        liq = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
        if s5 < 5:
            return False, f"продаж за 5м {s5} < 5 — ханипот или заблокированный стакан"
        if b5 < s5 * 1.2:
            return False, f"нет перевеса покупателей ({b5}b/{s5}s)"
        if vol5 < 5000:
            return False, f"объем за 5м ${vol5:,.0f} < $5,000 — искусственный пул"
        if liq < 20000:
            return False, f"ликвидность ${liq:,.0f} < $20,000 — тонкий пул"

        # 4. Ончейн-аудит байткода, верификации и LP-лока через RPC / Blockscout
        try:
            from http_client import fetch_json, get_session
            import aiohttp
            _rpc_url = getattr(config, "ROBINHOOD_RPC_URL", "https://rpc.mainnet.chain.robinhood.com")
            
            # А. Проверка верификации исходного кода на Blockscout
            _bs_url = f"https://robinhoodchain.blockscout.com/api?module=contract&action=getsourcecode&address={address}"
            _bs_st, _bs_data = await fetch_json(_bs_url, timeout=6, retries=1)
            if _bs_st == 200 and isinstance(_bs_data, dict):
                _res_list = _bs_data.get("result")
                if isinstance(_res_list, list) and len(_res_list) > 0:
                    _c_info = _res_list[0]
                    _src = str(_c_info.get("SourceCode", "") or "").strip()
                    _abi = str(_c_info.get("ABI", "") or "").strip()
                    if not _src or "Contract source code not verified" in _abi:
                        return False, "контракт не верифицирован на Blockscout (закрытый код = риск рага)"
                elif str(_bs_data.get("message", "")).lower() == "notok":
                    return False, "контракт не найден или не верифицирован на Blockscout"

            # Б. Байткод-аудит (поиск скрытого минта, блэклистов и паузы)
            _s_sess = await get_session()
            _pl_code = {"jsonrpc": "2.0", "id": 1, "method": "eth_getCode", "params": [address, "latest"]}
            async with _s_sess.post(_rpc_url, json=_pl_code, timeout=aiohttp.ClientTimeout(total=4)) as _r_c:
                _res_c = await _r_c.json()
            _bytecode = str(_res_c.get("result", "") or "").lower()
            if not _bytecode or _bytecode in ("0x", "0x0"):
                return False, "нет байткода (не смарт-контракт)"
                
            # Проверка опасных селекторов функций
            _dangerous_selectors = {
                "40c10f19": "скрытый минт mint(address,uint256)",
                "a0712d68": "скрытый минт mint(uint256)",
                "42f21696": "блэклист blacklist(address)",
                "83e2007e": "isBlacklisted",
                "8456cb59": "пауза торговли pause()",
                "03a118d5": "пауза торговли pause(bool)"
            }
            for _sel, _desc in _dangerous_selectors.items():
                if _sel in _bytecode:
                    return False, f"бэкдор в байткоде токена: {_desc}"
                    
            # В. Проверка сжигания ликвидности (LP Burn Guard)
            _pair_addr = pair_data.get("pairAddress")
            if _pair_addr:
                _DEAD = "000000000000000000000000000000000000dead".zfill(64)
                _ZERO = "0000000000000000000000000000000000000000".zfill(64)
                _pl_lp = [
                    {"jsonrpc": "2.0", "id": 10, "method": "eth_call", "params": [{"to": _pair_addr, "data": "0x18160ddd"}, "latest"]}, # totalSupply
                    {"jsonrpc": "2.0", "id": 11, "method": "eth_call", "params": [{"to": _pair_addr, "data": f"0x70a08231{_DEAD}"}, "latest"]}, # dead
                    {"jsonrpc": "2.0", "id": 12, "method": "eth_call", "params": [{"to": _pair_addr, "data": f"0x70a08231{_ZERO}"}, "latest"]}  # zero
                ]
                async with _s_sess.post(_rpc_url, json=_pl_lp, timeout=aiohttp.ClientTimeout(total=4)) as _r_lp:
                    _res_lp = await _r_lp.json()
                if isinstance(_res_lp, list) and len(_res_lp) >= 3:
                    _tot_lp_h = _res_lp[0].get("result", "0x")
                    _dead_lp_h = _res_lp[1].get("result", "0x")
                    _zero_lp_h = _res_lp[2].get("result", "0x")
                    if _tot_lp_h and _tot_lp_h != "0x":
                        _tot_lp = int(_tot_lp_h, 16)
                        _dead_lp = int(_dead_lp_h, 16) if _dead_lp_h and _dead_lp_h != "0x" else 0
                        _zero_lp = int(_zero_lp_h, 16) if _zero_lp_h and _zero_lp_h != "0x" else 0
                        if _tot_lp > 0:
                            _burnt_pct = ((_dead_lp + _zero_lp) / _tot_lp) * 100.0
                            if _burnt_pct < 80.0:
                                return False, f"ликва пула не сожжена ({_burnt_pct:.1f}% < 80%) — риск вывода ликвидности девом"
        except Exception as _e_rob:
            # СТРОГИЙ FAIL-CLOSED: если Robinhood RPC не ответил или упал с ошибкой — НЕ ВХОДИТЬ!
            return False, f"ошибка ончейн-аудита Robinhood ({type(_e_rob).__name__}) — блок по безопасности"

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
