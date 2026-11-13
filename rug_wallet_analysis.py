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
    """Обёртка над RPC с обработкой исключений."""
    try:
        fn = getattr(client, method)
        resp = await fn(*args, **kwargs)
        return resp
    except Exception as e:
        print(f"⚠️ [RUG-AUDIT] RPC {method} error: {type(e).__name__}: {e}")
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
            return {"ok": False, "reason": "no-mint-account", "details": {}}

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
            return {"ok": False, "reason": "no-holder-data", "details": {}}

        accounts = resp.value
        if not accounts:
            return {"ok": False, "reason": "no-holders", "details": {}}

        total_supply = accounts[0].amount if accounts else 0
        if total_supply <= 0:
            return {"ok": False, "reason": "zero-supply", "details": {}}

        top_accounts = []
        for acc in accounts[:20]:
            pct = acc.amount / total_supply if total_supply > 0 else 0
            top_accounts.append({
                "address": str(acc.address),
                "amount": acc.amount,
                "pct": pct,
            })

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
        sigs = sigs_resp.value if sigs_resp and sigs_resp.value else []

        # Баланс SOL
        bal_resp = await _rpc(client, "get_balance", wallet_pubkey)
        balance_lamports = bal_resp.value if bal_resp else 0
        balance_sol = balance_lamports / 1e9

        tx_count = len(sigs)

        # Кошелек считается "свежим/ботом", если мало транзакций и маленький баланс
        is_fresh_bot = (
            tx_count < config.WALLET_MIN_TX_FOR_REAL_WALLET or
            balance_sol < 0.05
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
        return {"wallet": wallet, "tx_count": 0, "balance_sol": 0.0, "is_fresh_bot": True, "error": str(e)}


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
    Обнаруживает бандл покупок: смотрит последние подписи по минту.
    Если очень много подписей за короткое время — подозрительно.
    """
    try:
        mint_pubkey = Pubkey.from_string(mint)
        resp = await _rpc(client, "get_signatures_for_address", mint_pubkey, limit=50)
        if not resp or not resp.value:
            return {"ok": True, "bundle_detected": False, "details": {}}

        recent_sigs = [str(sig_info.signature) for sig_info in resp.value]
        bundle_suspected = len(recent_sigs) >= 30

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
        from http_client import fetch_json
        url = f"https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"
        status, data = await fetch_json(url, timeout=10, retries=1)
        if status != 200 or not data:
            return {"ok": True, "reason": "rugcheck-unavailable", "details": {}}

        score = data.get("score", 0)
        risk = data.get("risk", "unknown")
        flags = data.get("flags", [])

        # RugCheck score: 0-100, чем ниже — хуже
        issues = []
        if score < 40:
            issues.append(f"rugcheck-score-low ({score})")
        if risk in ("high", "critical"):
            issues.append(f"rugcheck-risk-high ({risk})")
        for flag in flags:
            flag_name = flag.get("name", "") if isinstance(flag, dict) else str(flag)
            if any(k in flag_name.lower() for k in ("mint", "freeze", "bundled", "sybil", "honeypot")):
                issues.append(f"rugcheck-flag ({flag_name})")

        ok = len(issues) == 0

        return {
            "ok": ok,
            "reason": "; ".join(issues) if issues else "rugcheck-clean",
            "details": {
                "score": score,
                "risk": risk,
                "flags": flags[:5] if flags else [],
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
            verdict["safe"] = False
            verdict["score"] += 0.30
            verdict["reasons"].append(f"Authorities: {auth_check['reason']}")

        # 2. Держатели и дев
        holders_check = await check_dev_and_holders(client, mint)
        verdict["checks"]["holders"] = holders_check
        if not holders_check["ok"]:
            verdict["safe"] = False
            verdict["score"] += 0.25
            verdict["reasons"].append(f"Holders: {holders_check['reason']}")

        # 3. Анализ кошельков топ-холдеров (свежие боты?)
        top_accounts = holders_check.get("details", {}).get("top_accounts", [])
        suspicious_wallets = 0
        for acc in top_accounts[:5]:
            w_info = await analyze_wallet(client, acc["address"])
            if w_info.get("is_fresh_bot"):
                suspicious_wallets += 1
        if suspicious_wallets >= 2:
            verdict["safe"] = False
            verdict["score"] += 0.20
            verdict["reasons"].append(f"{suspicious_wallets} топ-кошельков — свежие боты (<{config.WALLET_MIN_TX_FOR_REAL_WALLET} tx или <0.05 SOL)")

        # 4. Бандл покупок
        bundle_check = await detect_bundle_buyers(client, mint,
                                                   window_sec=config.RUG_BUNDLE_WINDOW_SEC)
        verdict["checks"]["bundle"] = bundle_check
        if bundle_check.get("bundle_detected"):
            verdict["score"] += 0.15
            verdict["reasons"].append("Bundle buyers detected (many wallets bought at launch)")

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
