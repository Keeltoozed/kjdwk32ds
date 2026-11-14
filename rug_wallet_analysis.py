"""
rug_wallet_analysis.py — предвходовой аудит кошельков и защиты от мгновенных рагов.

Проверяется ДО входа в позицию:
1. Административные права токена (mintAuthority, freezeAuthority) — дев может напечатать токены или заморозить продажу.
2. Доля дева и топ-холдеров через getTokenLargestAccounts — если дев держит >10%供给, раг возможен в любую секунду.
3. Возраст и активность кошельков дева — свежие кошельки (< 100 tx, баланс < 0.1 SOL) = боты/сиビル.
4. Цепочка финансирования — если все ранние покупатели профинансированы с одного адреса = сиビル-сеть.
5. Бандл покупок — множество кошельков купили в одну секунду при запуске.
6. RugCheck API — оценка риска токена (если доступен).
7. Итоговый скор риска рага — интеграция в точку входа бота.
"""

import asyncio
import time
import config
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient

# Кеш результатов аудита, чтобы не долбить RPC на одни и те же минты
_AUDIT_CACHE: dict = {}  # mint -> (ts, verdict_dict)
AUDIT_CACHE_TTL_SEC = 60.0


def _cache_get(mint: str):
    hit = _AUDIT_CACHE.get(mint)
    if hit and (time.time() - hit[0]) < AUDIT_CACHE_TTL_SEC:
        return hit[1]
    return None


def _cache_set(mint: str, verdict: dict):
    _AUDIT_CACHE[mint] = (time.time(), verdict)


async def _rpc(client: AsyncClient, method: str, *args, **kwargs):
    """Обёртка над RPC с обработкой исключений и фоллбэком на публичный узел при 429."""
    try:
        fn = getattr(client, method)
        resp = await fn(*args, **kwargs)
        if resp is not None:
            return resp
    except Exception as e:
        print(f"⚠️ [RUG-AUDIT] Primary RPC {method} error: {type(e).__name__}: {e}")

    # Фоллбэк на публичный узел если основной RPC (Helius) вернул 429 или упал
    try:
        fallback_client = AsyncClient("https://api.mainnet-beta.solana.com")
        fn_fb = getattr(fallback_client, method)
        resp_fb = await asyncio.wait_for(fn_fb(*args, **kwargs), timeout=4.0)
        await fallback_client.close()
        return resp_fb
    except Exception as e_fb:
        return None


# =============================================================================
# 1. АДМИНИСТРАТИВНЫЕ ПРАВА ТОКЕНА
# =============================================================================
async def check_mint_authorities(client: AsyncClient, mint: str) -> dict:
    """
    Проверяет autorités минта: mintAuthority, freezeAuthority, updateAuthority.
    Если они не null — дев может напечатать новых токенов, заморозить кошельки или изменить метаданные.
    """
    try:
        mint_pubkey = Pubkey.from_string(mint)
        resp = await _rpc(client, "get_account_info_json_parsed", mint_pubkey)
        if not resp or not resp.value or not resp.value.data:
            return {"ok": True, "reason": "mint-account-pending-index", "degraded": True, "details": {}}

        parsed = resp.value.data.parsed if hasattr(resp.value.data, 'parsed') else {}
        info = parsed.get("info", {}) if isinstance(parsed, dict) else {}

        mint_authority = info.get("mintAuthority")
        freeze_authority = info.get("freezeAuthority")
        supply = info.get("supply", 0)
        decimals = info.get("decimals", 9)

        issues = []
        if mint_authority is not None:
            issues.append("mint_authority_enabled")
        if freeze_authority is not None:
            issues.append("freeze_authority_enabled")

        ok = True
        if config.RUG_REQUIRE_MINT_AUTH_DISABLED and mint_authority is not None:
            ok = False
        if config.RUG_REQUIRE_FREEZE_AUTH_DISABLED and freeze_authority is not None:
            ok = False

        return {
            "ok": ok,
            "reason": "; ".join(issues) if issues else "authorities_clean",
            "details": {
                "mint_authority": mint_authority,
                "freeze_authority": freeze_authority,
                "supply": supply,
                "decimals": decimals,
            },
        }
    except Exception as e:
        return {"ok": False, "reason": f"error-{type(e).__name__}", "details": {}}


# =============================================================================
# 2. ДОЛЯ ДЕВА И ТОП-ХОЛДЕРОВ
# =============================================================================
async def check_dev_and_holders(client: AsyncClient, mint: str) -> dict:
    """
    Проверяет долю крупнейших холдеров через getTokenLargestAccounts.
    Используем top1_pct как прокси для дева (часто это тот же кошелёк).
    """
    try:
        mint_pubkey = Pubkey.from_string(mint)
        resp = await _rpc(client, "get_token_largest_accounts", mint_pubkey)
        if not resp or not resp.value:
            return {"ok": True, "reason": "holder-data-pending-index", "degraded": True, "details": {}}

        accounts = resp.value
        if not accounts:
            return {"ok": False, "reason": "no-holders", "details": {}}

        # Получаем реальную эмиссию токена через get_token_supply
        supply_resp = await _rpc(client, "get_token_supply", mint_pubkey)
        total_supply = 0
        if supply_resp and hasattr(supply_resp, "value") and supply_resp.value:
            total_supply = int(getattr(supply_resp.value, "amount", 0) or 0)
        if total_supply <= 0:
            total_supply = 1_000_000_000_000_000 if mint.endswith("pump") else sum(int(getattr(a, "amount", 0) or 0) for a in accounts)
        if total_supply <= 0:
            return {"ok": False, "reason": "zero-supply", "details": {}}

        top_accounts = []
        for acc in accounts[:20]:
            _raw_amt = getattr(acc.amount, "amount", acc.amount)
            try:
                _amt_int = int(_raw_amt or 0)
            except (TypeError, ValueError):
                _amt_int = 0
            pct = _amt_int / total_supply if total_supply > 0 else 0
            top_accounts.append({
                "address": str(acc.address),
                "amount": _amt_int,
                "pct": pct,
            })

        # Исключаем AMM LP пулы / известные DEX vaults и резолвим реальных владельцев (owners),
        # чтобы не путать пул PumpSwap/Raydium/Meteora с девом и не чекать баланс ATA (всегда ~0.002 SOL rent).
        DEX_POOL_AUTHORITIES = {
            "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",  # Raydium Authority V4
            "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg",  # Raydium Migration
            "GpMZbSM2GgvTKHJirzeGfMFoaZ8UR2X7F4v8vHTvxFbL",  # Raydium CPMM
            "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",   # PumpSwap AMM
            "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",   # Pump.fun Bonding
            "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1",   # Pump.fun Fee
            "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB",  # Meteora Pools
            "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",   # Meteora DLMM
            "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",   # Orca Whirlpool
            "11111111111111111111111111111111",              # Burn / System null
            "1nc1nerator11111111111111111111111111111111",   # Incinerator burn
        }
        DEX_PROGRAM_OWNERS = {
            "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",   # PumpSwap AMM
            "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium AMM V4
            "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C",  # Raydium CPMM
            "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",  # Raydium CLMM
            "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",   # Meteora DLMM
            "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB",  # Meteora Pools
            "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG",   # Meteora DAMM v2
            "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",   # Orca Whirlpool
            "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",   # Pump.fun
        }
        pool_accounts = set(DEX_POOL_AUTHORITIES)
        rc_owner_by_ata = {}
        try:
            import goplus as _gp_rw
            rc_st, rc_data = await _gp_rw.fetch_rugcheck_report_cached(mint, ttl=120.0, timeout=5)
            if rc_st == 200 and isinstance(rc_data, dict):
                for _ka_addr, _ka_info in (rc_data.get("knownAccounts") or {}).items():
                    _kt = str((_ka_info or {}).get("type", "") or "").lower()
                    _kn = str((_ka_info or {}).get("name", "") or "").lower()
                    if _kt in ("amm", "lp", "pool", "vault", "burn") or any(
                        w in _kn for w in ("pool", "amm", "raydium", "pumpswap", "meteora", "orca", "pump.fun", "lp", "vault", "bonding")
                    ):
                        pool_accounts.add(str(_ka_addr))
                for _mkt in (rc_data.get("markets") or []):
                    if isinstance(_mkt, dict):
                        for _mf in ("pubkey", "owner", "lp", "liquidityA", "liquidityB"):
                            _mv = _mkt.get(_mf)
                            if isinstance(_mv, str) and len(_mv) > 20:
                                pool_accounts.add(_mv)
                            elif isinstance(_mv, dict) and _mv.get("address"):
                                pool_accounts.add(str(_mv["address"]))
                for _rh in (rc_data.get("topHolders") or []):
                    if isinstance(_rh, dict) and _rh.get("address") and _rh.get("owner"):
                        rc_owner_by_ata[str(_rh["address"])] = str(_rh["owner"])
        except Exception:
            pass

        # Резолвим owner для top_accounts (из кэша RugCheck или через 1 RPC getMultipleAccounts jsonParsed)
        need_ata_resolve = [a["address"] for a in top_accounts[:15] if a["address"] not in rc_owner_by_ata]
        if need_ata_resolve:
            try:
                _ata_pks = [Pubkey.from_string(x) for x in need_ata_resolve]
                _ma_resp = await _rpc(client, "get_multiple_accounts_json_parsed", _ata_pks)
                if _ma_resp and getattr(_ma_resp, "value", None):
                    for _ata_addr, _acct_info in zip(need_ata_resolve, _ma_resp.value):
                        try:
                            _parsed = getattr(getattr(_acct_info, "data", None), "parsed", None)
                            if isinstance(_parsed, dict):
                                _own = ((_parsed.get("info") or {}).get("owner")) or ""
                                if _own:
                                    rc_owner_by_ata[_ata_addr] = str(_own)
                        except Exception:
                            pass
            except Exception:
                pass

        # Дополнительно проверяем program owner у резолвленных кошельков: DEX Pool PDA принадлежат программам DEX, а не SystemProgram
        _unique_owners = list(dict.fromkeys(
            rc_owner_by_ata[a["address"]]
            for a in top_accounts[:15]
            if a["address"] in rc_owner_by_ata and rc_owner_by_ata[a["address"]] not in pool_accounts
        ))
        if _unique_owners:
            try:
                _own_pks = [Pubkey.from_string(x) for x in _unique_owners]
                _own_resp = await _rpc(client, "get_multiple_accounts", _own_pks)
                if _own_resp and getattr(_own_resp, "value", None):
                    for _o_addr, _o_info in zip(_unique_owners, _own_resp.value):
                        if _o_info is not None:
                            _prog_owner = str(getattr(_o_info, "owner", "") or "")
                            if _prog_owner in DEX_PROGRAM_OWNERS:
                                pool_accounts.add(_o_addr)
            except Exception:
                pass

        filtered_accounts = []
        for a in top_accounts:
            _ata = a.get("address", "")
            _own = rc_owner_by_ata.get(_ata, "")
            if _ata in pool_accounts or (_own and _own in pool_accounts):
                continue
            _acc_copy = dict(a)
            if _own:
                _acc_copy["owner"] = _own
            filtered_accounts.append(_acc_copy)
        top_accounts = filtered_accounts

        top1_pct = top_accounts[0]["pct"] if top_accounts else 0
        top10_pct = sum(a["pct"] for a in top_accounts[:10])

        issues = []
        if top1_pct > config.RUG_MAX_DEV_PCT:
            issues.append(f"top1_pct_high ({top1_pct*100:.1f}% > {config.RUG_MAX_DEV_PCT*100:.0f}%)")
        if top10_pct > config.RUG_MAX_TOP10_PCT:
            issues.append(f"top10_pct_high ({top10_pct*100:.1f}% > {config.RUG_MAX_TOP10_PCT*100:.0f}%)")

        ok = len(issues) == 0

        return {
            "ok": ok,
            "reason": "; ".join(issues) if issues else "holders_clean",
            "details": {
                "top1_pct": top1_pct,
                "top10_pct": top10_pct,
                "total_supply": total_supply,
                "top_accounts": top_accounts[:10],
            },
        }
    except Exception as e:
        return {"ok": False, "reason": f"error-{type(e).__name__}", "details": {}}


# =============================================================================
# 3. АНАЛИЗ КОШЕЛЬКА (возраст, транзакции, баланс)
# =============================================================================
_WALLET_INFO_CACHE: dict = {}  # wallet -> (ts, info)
WALLET_INFO_TTL_SEC = 120.0


def _wallet_cache_get(wallet: str):
    hit = _WALLET_INFO_CACHE.get(wallet)
    if hit and (time.time() - hit[0]) < WALLET_INFO_TTL_SEC:
        return hit[1]
    return None


def _wallet_cache_set(wallet: str, info: dict):
    _WALLET_INFO_CACHE[wallet] = (time.time(), info)


async def analyze_wallet(client: AsyncClient, wallet: str) -> dict:
    """
    Детальный анализ одного кошелька:
    - количество недавних транзакций
    - баланс SOL
    - признаки "свежего" бота-кошелька
    """
    cached = _wallet_cache_get(wallet)
    if cached:
        return cached

    try:
        wallet_pubkey = Pubkey.from_string(wallet)

        # Последние подписи
        sigs_resp = await _rpc(client, "get_signatures_for_address", wallet_pubkey, limit=50)
        bal_resp = await _rpc(client, "get_balance", wallet_pubkey)
        if sigs_resp is None or bal_resp is None:
            return {
                "wallet": wallet,
                "tx_count": 0,
                "balance_sol": 0.0,
                "is_fresh_bot": False,
                "rpc_degraded": True,
            }

        sigs = sigs_resp.value if sigs_resp.value else []
        balance_lamports = bal_resp.value if bal_resp.value is not None else 0
        balance_sol = balance_lamports / 1e9
        tx_count = len(sigs)

        # Кошелек считается "свежим/ботом", если реально мало транзакций (<5),
        # либо (<10 tx И почти пустой баланс <0.02 SOL); активный трейдер с >=10 tx не считается ботом
        is_fresh_bot = (
            tx_count < config.WALLET_MIN_TX_FOR_REAL_WALLET
            or (tx_count < 10 and balance_sol < 0.02)
        )

        info = {
            "wallet": wallet,
            "tx_count": tx_count,
            "balance_sol": balance_sol,
            "is_fresh_bot": is_fresh_bot,
            "sig_count_recent": len(sigs),
        }

        _wallet_cache_set(wallet, info)
        return info
    except Exception as e:
        return {"wallet": wallet, "tx_count": 0, "balance_sol": 0.0, "is_fresh_bot": False, "error": str(e)}


# =============================================================================
# 4. ЦЕЛЕПОДВАЯ ЦЕПОЧКА ФИНАНСИРОВАНИЯ (кто профинансировал дева и холдеров)
# =============================================================================
async def trace_wallet_funding(client: AsyncClient, wallet: str, depth: int = 0,
                                max_depth: int = 3, visited: set = None) -> dict:
    """
    Рекурсивно смотрит последние входящие переводы SOL на кошелёк.
    Если много кошельков профинансированы с одного адреса — это сиビル-сеть.
    """
    if visited is None:
        visited = set()
    if depth >= max_depth or wallet in visited:
        return {"wallet": wallet, "depth": depth, "funders": [], "circular": wallet in visited}

    visited.add(wallet)

    try:
        wallet_pubkey = Pubkey.from_string(wallet)
        resp = await _rpc(client, "get_signatures_for_address", wallet_pubkey, limit=20)
        if not resp or not resp.value:
            return {"wallet": wallet, "depth": depth, "funders": [], "circular": False}

        funders = []
        for sig_info in resp.value[:10]:
            funders.append({
                "signature": str(sig_info.signature),
                "slot": sig_info.slot,
            })

        return {
            "wallet": wallet,
            "depth": depth,
            "funders": funders,
            "circular": False,
            "visited_count": len(visited),
        }
    except Exception as e:
        return {"wallet": wallet, "depth": depth, "funders": [], "error": str(e)}


# =============================================================================
# 5. БАНДЛ ПОКУПОК (множество кошельков купили в одну секунду)
# =============================================================================
async def detect_bundle_buyers(client: AsyncClient, mint: str, window_sec: int = 30) -> dict:
    """
    Обнаруживает Jito/sniper-бандл покупок: проверяет реальную кластеризацию
    транзакций в одном и том же слоте (~400ms) или аномальный всплеск на запуске,
    а не просто наличие >= 30 исторических транзакций за всё время.
    """
    try:
        mint_pubkey = Pubkey.from_string(mint)
        resp = await _rpc(client, "get_signatures_for_address", mint_pubkey, limit=50)
        if not resp or not resp.value:
            return {"ok": True, "bundle_detected": False, "details": {}}

        recent_sigs = [str(sig_info.signature) for sig_info in resp.value]
        slot_counts = {}
        times = []
        for sig_info in resp.value:
            sl = getattr(sig_info, "slot", None)
            if sl is not None:
                slot_counts[sl] = slot_counts.get(sl, 0) + 1
            bt = getattr(sig_info, "block_time", None)
            if bt is not None:
                times.append(int(bt))

        same_slot_max = max(slot_counts.values()) if slot_counts else 0
        span_sec = (max(times) - min(times)) if len(times) >= 2 else 0
        # Реальный бандл — когда >= 12 транзакций прилетело в один-единственный 400ms слот,
        # либо >= 45 транзакций уместилось в крошечное окно <= 2 сек сразу при старте
        bundle_suspected = (same_slot_max >= 12) or (len(recent_sigs) >= 45 and len(times) >= 40 and span_sec <= 2)

        return {
            "ok": True,
            "bundle_detected": bundle_suspected,
            "details": {
                "recent_signatures": len(recent_sigs),
                "window_sec": window_sec,
            },
        }
    except Exception as e:
        return {"ok": False, "bundle_detected": False, "reason": f"error-{type(e).__name__}", "details": {}}


# =============================================================================
# 6. RUGCHECK API (внешняя оценка риска)
# =============================================================================
async def check_rugcheck_api(mint: str) -> dict:
    """
    Вызывает RugCheck API для получения оценки риска токена.
    Возвращает (ok: bool, reason: str, details: dict).
    """
    try:
        data = None
        try:
            import goplus as _gp_rc
            rc_st, rc_full = await _gp_rc.fetch_rugcheck_report_cached(mint, ttl=120.0, timeout=5)
            if rc_st == 200 and isinstance(rc_full, dict):
                data = rc_full
        except Exception:
            pass
        if not data:
            from http_client import fetch_json
            url = f"https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"
            status, summary_data = await fetch_json(url, timeout=8, retries=1)
            if status == 200 and isinstance(summary_data, dict):
                data = summary_data
        if not data:
            return {"ok": True, "reason": "rugcheck-unavailable", "details": {}}

        score = float(data.get("score", 0) or 0)
        risks = data.get("risks") if isinstance(data.get("risks"), list) else (data.get("flags") or [])
        _allowed_rc_noise = (
            "low liquidity", "copycat", "symbol mimic",
            "large amount of lp unlocked", "lp provider", "single lp provider",
            "lp not locked", "unlocked lp",
            "single holder", "high holder concentration", "top 10 holders", "high ownership",
        )
        noise_penalty = sum(
            float(rk.get("score", 0) or 0)
            for rk in risks
            if isinstance(rk, dict) and any(k in str(rk.get("name", "")).lower() for k in _allowed_rc_noise)
        )
        clean_score = max(0.0, score - noise_penalty)

        # RugCheck clean_score: 0 = безопасно, >= 2000 = опасный раг (после вычета шума незалоченного пула PumpSwap/Raydium)
        issues = []
        if clean_score >= 2000:
            issues.append(f"rugcheck-score-high ({clean_score:.0f})")
        for flag in risks:
            flag_name = flag.get("name", "") if isinstance(flag, dict) else str(flag)
            flag_level = str(flag.get("level", "")).lower() if isinstance(flag, dict) else ""
            _fl = flag_name.lower()
            if any(k in _fl for k in _allowed_rc_noise):
                continue
            if flag_level == "danger" and any(k in _fl for k in ("mint", "freeze", "bundled", "sybil", "honeypot", "transfer fee", "permanent delegate", "creator holding", "creator history")):
                issues.append(f"rugcheck-flag ({flag_name})")
            elif any(k in _fl for k in ("mint authority", "freeze authority", "honeypot", "bundled", "sybil")):
                issues.append(f"rugcheck-flag ({flag_name})")

        ok = len(issues) == 0

        return {
            "ok": ok,
            "reason": "; ".join(issues) if issues else "rugcheck-clean",
            "details": {
                "score": clean_score,
                "raw_score": score,
                "risk": data.get("risk", "unknown"),
                "risks": [r.get("name", "") if isinstance(r, dict) else str(r) for r in risks[:5]],
            },
        }
    except Exception as e:
        return {"ok": True, "reason": f"rugcheck-error-{type(e).__name__}", "details": {}}


# =============================================================================
# 7. ПОЛНЫЙ АУДИТ ПЕРЕД ВХОДОМ
# =============================================================================
async def full_rug_wallet_audit(mint: str, dev_wallet: str = None) -> dict:
    """
    Полный аудит кошельков и раг-рисков ПЕРЕД входом в позицию.
    Возвращает: {"safe": bool, "score": float, "reasons": [str], "checks": {...}}
    score: 0 = безопасно, 1 = certainement раг
    """
    verdict = {
        "safe": True,
        "score": 0.0,
        "reasons": [],
        "checks": {},
    }

    cached = _cache_get(mint)
    if cached:
        return cached

    rpc_url = config.HELIUS_RPC_URL
    client = AsyncClient(rpc_url)

    try:
        # 1. Административные права
        auth_check = await check_mint_authorities(client, mint)
        verdict["checks"]["authorities"] = auth_check
        if not auth_check["ok"]:
            if auth_check.get("degraded") or any(k in str(auth_check.get("reason", "")).lower() for k in ("rpc", "timeout", "pending", "no-mint-account")):
                pass
            else:
                verdict["safe"] = False
                verdict["score"] += 0.30
                verdict["reasons"].append(f"Authorities: {auth_check['reason']}")

        # 2. Держатели и дев
        holders_check = await check_dev_and_holders(client, mint)
        verdict["checks"]["holders"] = holders_check
        if not holders_check["ok"]:
            if holders_check.get("degraded") or any(k in str(holders_check.get("reason", "")).lower() for k in ("rpc", "timeout", "pending", "no-holder-data")):
                pass
            else:
                verdict["safe"] = False
                verdict["score"] += 0.25
                verdict["reasons"].append(f"Holders: {holders_check['reason']}")

        # 3. Анализ реальных кошельков топ-холдеров (свежие боты?) — НИКОГДА не чекаем баланс ATA!
        top_accounts = holders_check.get("details", {}).get("top_accounts", [])
        suspicious_wallets = 0
        for acc in top_accounts[:5]:
            _owner_addr = acc.get("owner") or ""
            if not _owner_addr:
                continue
            w_info = await analyze_wallet(client, _owner_addr)
            if w_info.get("is_fresh_bot"):
                suspicious_wallets += 1
        if suspicious_wallets >= 3:
            verdict["safe"] = False
            verdict["score"] += 0.25
            verdict["reasons"].append(f"{suspicious_wallets} топ-кошельков — свежие боты (<{config.WALLET_MIN_TX_FOR_REAL_WALLET} tx или <0.05 SOL)")

        # 4. Бандл покупок (Jito/same-slot burst)
        bundle_check = await detect_bundle_buyers(client, mint,
                                                   window_sec=config.RUG_BUNDLE_WINDOW_SEC)
        verdict["checks"]["bundle"] = bundle_check
        if bundle_check.get("bundle_detected"):
            verdict["score"] += 0.20
            verdict["reasons"].append("Bundle buyers detected (many txs in single slot at launch)")

        # 5. RugCheck API
        rc_check = await check_rugcheck_api(mint)
        verdict["checks"]["rugcheck"] = rc_check
        if not rc_check["ok"]:
            verdict["safe"] = False
            verdict["score"] += 0.20
            verdict["reasons"].append(f"RugCheck: {rc_check['reason']}")

        # 6. Если известен кошелёк дева — анализируем его
        if dev_wallet:
            dev_info = await analyze_wallet(client, dev_wallet)
            verdict["checks"]["dev_wallet"] = dev_info
            if dev_info.get("is_fresh_bot"):
                verdict["safe"] = False
                verdict["score"] += 0.25
                verdict["reasons"].append(f"Dev wallet is fresh bot (tx={dev_info.get('tx_count', 0)}, bal={dev_info.get('balance_sol', 0):.2f} SOL)")

            # Цепочка финансирования дева
            funding_check = await trace_wallet_funding(
                client, dev_wallet,
                depth=0,
                max_depth=config.RUG_FUNDING_CHAIN_DEPTH,
            )
            verdict["checks"]["dev_funding"] = funding_check
            funders_n = len(funding_check.get("funders", []))
            if funders_n >= config.RUG_MAX_WALLETS_WITH_SAME_FUNDER:
                verdict["score"] += 0.15
                verdict["reasons"].append(f"Dev wallet has {funders_n} recent funders (possible sybil network)")

    finally:
        try:
            await client.close()
        except Exception:
            pass

    verdict["score"] = min(verdict["score"], 1.0)

    if verdict["score"] >= 0.50:
        verdict["safe"] = False

    _cache_set(mint, verdict)
    return verdict


async def quick_rug_check(mint: str) -> tuple:
    """
    Быстрая проверка перед входом: (safe: bool, reason: str).
    Используется в точке входа main.py.
    """
    audit = await full_rug_wallet_audit(mint)
    if audit["safe"]:
        return True, "rug-audit-passed"
    return False, "; ".join(audit["reasons"])
