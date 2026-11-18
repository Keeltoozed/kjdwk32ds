import os
from typing import Optional, Tuple
from dotenv import load_dotenv

load_dotenv()

DEXSCREENER_LATEST = "https://api.dexscreener.com/token-boosts/latest/v1" 
DEXSCREENER_PROFILES = "https://api.dexscreener.com/token-profiles/latest/v1" 
DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/tokens/"

HELIUS_API_KEY = (os.getenv("HELIUS_API_KEY") or "").strip().strip('"').strip("'")
HELIUS_RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}" if HELIUS_API_KEY else "https://api.mainnet-beta.solana.com"
PUMPPORTAL_API_KEY = (os.getenv("PUMPPORTAL_API_KEY") or "").strip().strip('"').strip("'")
PUMPPORTAL_WSS = f"wss://pumpportal.fun/api/data?api-key={PUMPPORTAL_API_KEY}" if PUMPPORTAL_API_KEY else "wss://pumpportal.fun/api/data"

RUGCHECK_API = "https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"

def _clean_supabase_url(raw: str) -> str:
    u = (raw or "").strip().strip('"').strip("'").rstrip("/")
    if not u:
        return "https://kfyrvmupvwnvuqkeuxun.supabase.co"
    for suffix in ("/rest/v1", "/auth/v1", "/realtime/v1", "/storage/v1"):
        if u.endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
    if "supabase.com/dashboard/project/" in u:
        try:
            ref = u.split("/dashboard/project/")[1].split("/")[0].split("?")[0]
            if ref:
                return f"https://{ref}.supabase.co"
        except Exception:
            pass
    return u

SUPABASE_URL = _clean_supabase_url(os.getenv("SUPABASE_URL"))
SUPABASE_KEY = (os.getenv("SUPABASE_KEY") or "").strip().strip('"').strip("'")

PAPER_PORTFOLIO_FILE = "portfolio.json"
PORTFOLIO_SNAPSHOT_MINT = (os.getenv("PORTFOLIO_SNAPSHOT_MINT", "") or "").strip().strip('"').strip("'") or "PORTFOLIO_ROCKET_V4_20USD"
INITIAL_BALANCE_USD = 20.0
REINVEST_PERCENT = 20.0
VIRTUAL_POSITION_SIZE_USD = 2.0
TRADE_AMOUNT_USD = 2.0

COMPOUNDING_ENABLED = True
COMPOUNDING_PCT = 0.10
MAX_COMPOUNDING_SIZE_USD = 25.0
DYNAMIC_REINVEST_ENABLED = True
BASE_TRADE_AMOUNT_USD = 2.0
MAX_POSITION_SIZE_USD = 5.0
REINVEST_MIN_POOL_LIQ_USD = 5000.0
SCOUT_SIZE_USD = 2.0
LOTTERY_SIZE_USD = 2.0
MAX_CONCURRENT_POSITIONS = 3

# === ЖЕСТКОЕ ОГРАНИЧЕНИЕ УБЫТКА (РИСК-МЕНЕДЖМЕНТ) ===
MAX_DAILY_LOSS_USD = 15.0
KILL_SWITCH_ENABLED = True
STOP_LOSS_PCT = -0.15          # Единый стоп -15%: не выбивает на -11%..-13% откатах ракет, но быстро режет лузеров (в паре с Quick Scratch -8.5%)
STOP_LOSS_UNLOCKED_PCT = -0.12 # Единый стоп -12% для незалоченных пулов Meteora/Raydium/Custom PumpSwap
EMERGENCY_CAP_PCT = -0.22      # Аварийный потолок -22%: срез мгновенного краша
INFANT_WINDOW_MIN = 0.5
INFANT_DUMP_PCT = -0.200       # Срез мгновенного рага в первые 30с (<= -20.0%)

# === ЗАЩИТА ОТ МГНОВЕННОГО ВЫВОДА ЛИКВИДНОСТИ (девы выдергивают пул в 0) ===
LIQ_DRAIN_EXIT_PCT = 0.40      # Выход если ликвидность упала на 40% от пика (режем до полного обнуления)
LIQ_DRAIN_FAST_PCT = 0.30      # Если за один тик (<2с) ликва просела на 30% — мгновенный паник-сейл
LIQ_MIN_STABLE_PCT = 0.80      # Перед входом: ликва не должна просесть более чем на 20% за последние 10 мин
DEV_MAX_TOKEN_PCT = 0.15       # Дев не должен держать более 15% от эмиссии
LIQ_LOCKED_BURNED_MIN_PCT = 0.0   # Разрешаем быстрый вход без задержки на аудит LP (охота за ракетами)

# === ФИЛЬТРЫ ВХОДА (ОХОТА ЗА НАСТОЯЩИМИ РАКЕТАМИ ТИПА FIVEM / WUF) ===
ALLOW_UNLOCKED_LP_POOLS = False           # СТРОГИЙ БЛОК LP lock 0% / burn 0% (защита от рагов TOMÁS, USPR, BALVOTE; Pump.fun и каноничные PumpSwap/Raydium проходят!)
BLOCK_PERMISSIONLESS_LIQ_FDV_TRAP = True   # Блокировать кастомные ловушки дева
REQUIRE_SOCIALS_ENABLED = False            # Не требовать соцсети на старте/зрелости (важна только живая ликвидность и импульс)
MAX_ENTRY_H1_PCT = 180.0       # Было 1500.0: отсекает вертикальный вынос и хаи (фикс Y +944%)
MAX_ENTRY_H6_PCT = 350.0       # Было 500.0: не входим в отработавшие топы
MAX_ENTRY_M5_PCT = 175.0        # Потолок m5 +175% (по 161 сделке: m5>180% = -648% PnL; ракеты Rockstar +154%, DOGPOOP +165%, LIFTD +154%, DACT +59% <= 175%)
MAX_ENTRY_M1_PCT = 60.0         # Потолок 1м свечи +60% для свежих запусков
# MAX_ENTRY_M5_SPIKE_PCT убран — не блокируем вход по m5, пусть ракеты летят
REQUIRE_SOCIALS_MIN_AGE_MIN = 9999.0  # Соцсети не обязательны (отключено)


def get_max_entry_h1_pct(
    age_min: float,
    vol_m5: float = 0.0,
    buys_m5: int = 0,
    sells_m5: int = 0,
    liq_usd: float = 0.0,
    m5_pct: float = 1.5,
) -> float:
    """Return dynamic max h1% entry cap: allow ultra-fresh launches/migrations while rejecting spent tops."""
    _m5_ok = True if m5_pct is None else (float(m5_pct) >= 1.0)
    _flow_ok = vol_m5 >= 2500.0 and buys_m5 >= int(sells_m5 * 1.05) and _m5_ok
    if age_min <= 15.0:
        return 2500.0 if _flow_ok else 1800.0
    if age_min <= 30.0:
        return 600.0 if _flow_ok else 350.0
    if age_min <= 60.0:
        return 400.0 if _flow_ok else 220.0
    return 250.0 if _flow_ok else 150.0


LAUNCHPAD_MINT_SUFFIXES = ("pump", "moon", "bonk", "bags")
UNLOCKED_CUSTOM_MIN_LIQ_USD = 5000.0
UNLOCKED_CUSTOM_MIN_AGE_MIN = 1.9              # 1.9 мин мин. возраст для незалоченных пулов С подтверждёнными соцсетями
UNLOCKED_CUSTOM_NO_SOC_MIN_AGE_MIN = 1.9       # 1.9 мин мин. возраст для незалоченных пулов (отсекает 0-1.5м раги USPR/WCOIN/TOWN, пропускает ANT 2.38м +53% и HOGE +158%)
UNLOCKED_CUSTOM_MIN_VOL5_USD = 6500.0
UNLOCKED_CUSTOM_MIN_BUYS5 = 40


def is_launchpad_mint(mint: str) -> bool:
    """Return True if mint belongs to a canonical Solana launchpad (pump, moon, bonk, BAGS)."""
    return str(mint or "").strip().lower().endswith(LAUNCHPAD_MINT_SUFFIXES)


def is_unmigrated_curve_token(mint: str, pair_data: Optional[dict] = None) -> bool:
    """Return True ONLY if token is on an actual ungraduated bonding curve (pump/pumpfun/moonshot/meteoradbc)."""
    if not mint:
        return False
    mint_str = str(mint).strip().lower()
    pd = pair_data if isinstance(pair_data, dict) else {}
    dex_id = str(pd.get("dexId") or "").strip().lower()

    # 1. Явные пулы AMM DEX (pumpswap, raydium, orca, meteora) — уже мигрировали, это НЕ сырая кривая!
    if dex_id in ("pumpswap", "raydium", "orca", "meteora"):
        return False

    # 2. Токены на сырых кривых pump, pumpfun, moonshot, meteoradbc
    if dex_id in ("pump", "pumpfun", "moonshot", "meteoradbc") or mint_str.endswith("moon"):
        is_known_grad = False
        try:
            from birth_tracker import birth_tracker as _bt_chk
            is_known_grad = _bt_chk.is_graduated(mint)
        except Exception:
            pass
        liq = float((pd.get("liquidity") or {}).get("usd", 0) or 0)
        if is_known_grad and liq >= 6000.0 and dex_id not in ("pump", "pumpfun"):
            return False
        return True

    # 3. Если dexId пустой у .pump токена и ликвидность < $4,500 — это ещё сырой кёрв
    liq = float((pd.get("liquidity") or {}).get("usd", 0) or 0)
    if dex_id == "" and mint_str.endswith("pump") and liq < 4500.0:
        return True

    return False


def is_position_unlocked_lp(
    mint: str,
    pair_data: Optional[dict] = None,
    is_lp_locked_80: bool = False,
) -> bool:
    """Return True if LP can be withdrawn by the pool creator/owner (unlocked risk)."""
    if bool(is_lp_locked_80):
        return False
    pd = pair_data if isinstance(pair_data, dict) else {}
    if bool(pd.get("_pumpswap_canonical")) or bool(pd.get("_pumpswap_lp_burned")) or bool(pd.get("_lp_verified")):
        return False
    if is_unmigrated_curve_token(mint, pd):
        return False
    dex_low = str(pd.get("dexId") or "").strip().lower()
    mint_low = str(mint or "").strip().lower()
    if (
        mint_low.endswith("pump")
        and dex_low in ("", "pump", "pumpfun", "pump-amm", "pumpswap", "raydium")
        and pd.get("_pumpswap_canonical") is not False
        and pd.get("_pumpswap_lp_burned") is not False
    ):
        return False
    return True


def verify_rugcheck_lp_markets(
    markets: list,
    target_pair: str = "",
    raw_liq: float = 0.0,
    symbol: str = "",
    mint: str = "",
    log_unlocked: bool = True,
    allow_pump_amm_lock: bool = True,
) -> bool:
    """Return True only when RugCheck reports genuinely burned or non-phantom locked LP.

    When target_pair is provided and matches a market in RugCheck, verify THAT specific pool
    so an unlocked secondary pool (e.g. Meteora/Raydium side pool on a Pump.fun token)
    cannot slip in behind a safe PumpSwap pool.
    """
    has_verified = False
    target_matched = False
    target_safe = False
    for mkt in (markets or []):
        if not isinstance(mkt, dict):
            continue
        mkt_pubkey = str(mkt.get("pubkey") or "").strip()
        mkt_type = str(mkt.get("marketType") or "").lower()
        lp_info = mkt.get("lp") or {}
        if not isinstance(lp_info, dict):
            continue
        mkt_quote_usd = float(lp_info.get("quoteUSD") or 0.0)
        lp_unlocked = float(lp_info.get("lpUnlocked") or 0.0)
        lp_locked_pct = float(lp_info.get("lpLockedPct") or 0.0)
        lp_burned_pct = float(lp_info.get("lpBurnedPct") or 0.0)
        lp_cur_supply = float(lp_info.get("currentSupply") or lp_info.get("lpLocked") or 0.0)
        lp_tot_supply = float(lp_info.get("tokenSupply") or lp_info.get("lpTotalSupply") or 0.0)
        # Нативный pump_fun / pump_fun_amm пул с lpLockedPct >= 90 и 0 unlocked (либо burned >= 90%) безопасен;
        # кастомные Meteora / Raydium / PumpSwap-клоны без supply и лока не проходят.
        is_native_pump_amm = mkt_type in ("pump_fun", "pump_fun_amm")
        is_phantom_amm = any(k in mkt_type for k in ("meteora", "pumpswap")) or (
            is_native_pump_amm and lp_unlocked > 0 and lp_locked_pct < 90.0
        )
        has_real_lock = lp_locked_pct >= 85.0 and (
            not is_phantom_amm or (lp_tot_supply > 0.0 and lp_cur_supply > 0.0)
        )
        safe_lp = (lp_burned_pct >= 85.0) or (
            "meteora" not in mkt_type
            and not (is_native_pump_amm and not allow_pump_amm_lock)
            and has_real_lock
        )
        real_liquidity = (mkt_quote_usd >= 5000.0) or (
            bool(target_pair) and mkt_pubkey == target_pair and mkt_quote_usd <= 0.0 and raw_liq >= 12000.0
        )
        if target_pair and mkt_pubkey == target_pair:
            target_matched = True
            target_safe = bool(safe_lp and real_liquidity)
        if safe_lp and real_liquidity:
            has_verified = True
        if log_unlocked and lp_unlocked > 0 and not (is_native_pump_amm and lp_locked_pct >= 95.0):
            print(
                f"ℹ️ [RUGCHECK-LP] {symbol} ({mint[:8]}): LP lock {lp_locked_pct:.1f}%, "
                f"burn {lp_burned_pct:.1f}%, unl ${lp_unlocked:,.0f}."
            )
    if target_matched:
        return target_safe
    return has_verified


def is_safe_unlocked_custom_runner(
    liq_usd: float,
    age_min: float,
    vol5_usd: float,
    buys5: int,
    sells5: int,
    has_socials: bool = False,
    is_trap: bool = False,
    dev_pct: float = 0.0,
    top10_pct: float = 0.0,
    fdv_usd: float = 0.0,
    m5_pct: Optional[float] = None,
    pair_data: Optional[dict] = None,
    **kwargs,
) -> bool:
    """
    Допуск высокооборотных DEX-раннеров (PumpSwap, Raydium):
    отсекаем мгновенные ловушки дева с выводом LP через 1 минуту (USPR, WCOIN, TOWN, VINE, GTA6),
    искусственные seeded-LP ловушки (JENSEN, DACITYCOIN, BALVOTE, Starhug) и wash-накрутки (LSC 10.6x),
    но пропускаем настоящие органические DEX-ракеты (HOGE +158%, ANT +53%, RATFACE +29%, GRANDPUMP +214%).
    """
    if is_trap:
        return False

    if isinstance(pair_data, dict):
        _dex_u = str(pair_data.get("dexId") or "").lower()
        if "meteora" in _dex_u and _dex_u != "meteoradbc" and not bool(pair_data.get("_lp_verified")):
            return False
        if is_pumpswap_liq_fdv_trap(pair_data):
            return False

    liq_val = float(liq_usd or 0.0)
    fdv_val = float(fdv_usd or 0.0)
    vol5_val = float(vol5_usd or 0.0)
    age_val = float(age_min or 0.0)
    b5 = int(buys5 or 0)
    s5 = int(sells5 or 0)

    min_liq = float(globals().get("UNLOCKED_CUSTOM_MIN_LIQ_USD", 5000.0))
    if liq_val < min_liq:
        return False

    # Извлечение подтверждённых соцсетей из pair_data
    if isinstance(pair_data, dict):
        _info = pair_data.get("info") or {}
        _socs = _info.get("socials") or []
        _webs = _info.get("websites") or []
        if len(_socs) > 0 or len(_webs) > 0:
            has_socials = True

    # Карантин по возрасту >= 1.9 минуты (отсекает 0-1.5м раги WCOIN, TOWN, VINE, GTA6, USPR)
    min_age = float(globals().get("UNLOCKED_CUSTOM_NO_SOC_MIN_AGE_MIN", 1.9)) if not has_socials else float(globals().get("UNLOCKED_CUSTOM_MIN_AGE_MIN", 1.9))
    if age_val < min_age:
        return False

    # Требуем реальную двухстороннюю торговлю с нормальным средним чеком (HOGE avg $103, ANT avg $47, RATFACE avg $52)
    avg_tx = vol5_val / max(1, b5 + s5)
    if not bool(globals().get("ALLOW_UNLOCKED_LP_POOLS", True)):
        if b5 < 40 or s5 < 10 or vol5_val < max(6500.0, liq_val * 0.80) or avg_tx < 22.0:
            return False
        if fdv_val > 0.0 and liq_val >= (fdv_val * 0.72):
            return False
    else:
        if not has_socials:
            if b5 < 35 or vol5_val < 6500.0 or s5 < 8:
                return False
        else:
            min_vol5 = float(globals().get("UNLOCKED_CUSTOM_MIN_VOL5_USD", 2500.0))
            min_buys5 = int(globals().get("UNLOCKED_CUSTOM_MIN_BUYS5", 15))
            if b5 < min_buys5 or vol5_val < min_vol5:
                return False

    if s5 > 0 and b5 < int(s5 * 1.10):
        return False

    # Seeded-LP приманка (кейсы JENSEN 0.23x, DACITYCOIN 0.06x, Starhug 0.19x): на крупном пуле нужен живой оборот
    if liq_val >= 18000.0 and vol5_val < (liq_val * 0.50):
        return False

    # Синтетическая ловушка Liq/FDV
    if fdv_val > 0.0 and liq_val > (fdv_val * 2.25):
        return False

    # На незалоченном пуле должен быть реальный ценовой импульс, а не плоский флет перед дампом (кейс TOWN +0.7%)
    if m5_pct is not None and float(m5_pct) < 4.0 and vol5_val < (liq_val * 2.0):
        return False

    # Аномальный wash-оборот (>7.0x ликвидности без соцсетей — кейс LSC 10.6x -100%)
    if not has_socials and liq_val > 0 and (vol5_val / liq_val) > 7.0 and age_val < 20.0:
        return False

    return True


def is_distributed_safe_runner(
    liq_usd: float,
    age_min: float,
    vol5_usd: float,
    buys5: int,
    sells5: int,
    top1_pct: float = 0.0,
    top10_pct: float = 0.0,
    dev_pct: float = 0.0,
    is_lp_safe: bool = False,
) -> bool:
    """Exempt liquid, active runners from soft sybil/graph false positives."""
    if liq_usd < 8000.0 or vol5_usd < 5000.0:
        return False
    if buys5 < 25 or (sells5 > 0 and buys5 < int(sells5 * 0.85)):
        return False
    if top1_pct > 18.0 or top10_pct > 45.0 or dev_pct >= 15.0:
        return False
    return True


def is_permissionless_liq_fdv_trap(pair_data: Optional[dict]) -> bool:
    """
    Отключается при BLOCK_PERMISSIONLESS_LIQ_FDV_TRAP = False (снятие ограничений
    на кастомные и незалоченные пулы Meteora/Raydium/PumpSwap).
    """
    if not bool(globals().get("BLOCK_PERMISSIONLESS_LIQ_FDV_TRAP", False)):
        return False
    if not isinstance(pair_data, dict):
        return False
    dex = str(pair_data.get("dexId") or "").lower()
    base_token = pair_data.get("baseToken") or {}
    mint = str(base_token.get("address") or "").lower()

    # Нативные pump.fun bonding curves безопасны (ликва в контракте), но пулы с гигантской посеянной ликвой (>= $25k) без соцсетей — скам (кейс MORAPL $93k liq / 0 soc)
    if dex in ("pump", "pumpfun"):
        return False
        
    liq_usd = float((pair_data.get("liquidity") or {}).get("usd") or 0.0)
    fdv_usd = float(pair_data.get("fdv") or pair_data.get("marketCap") or 0.0)

    # ЖЁСТКИЙ ТРАП (проверяется ДО порога $18k, т.к. посеянный CreatePool часто на $5k-$18k, как AudaCity $15.7k liq / $10.4k FDV):
    # 1) pump-минт на AMM с ликвой >= 75% FDV — посеянный CreatePool (BALVOTE).
    # 2) Любой AMM пул с ликвой >= 110% FDV — физически невозможно без искусственного залива LP (AudaCity $18.5k->$15.7k liq / $14.5k->$10.4k FDV).
    # Соцсети или боты-покупатели НЕ оправдывают такую аномалию!
    if liq_usd >= 3000.0 and fdv_usd > 0:
        if mint.endswith("pump") and liq_usd >= fdv_usd * 0.75:
            return True
        if liq_usd >= fdv_usd * 1.10:
            return True

    if liq_usd < 18000.0:
        return False
        
    # Check socials
    info = pair_data.get("info") or {}
    socials = info.get("socials") or []
    websites = info.get("websites") or []
    has_socials = bool(
        (isinstance(socials, list) and len(socials) > 0)
        or (isinstance(websites, list) and len(websites) > 0)
    )
    if has_socials:
        return False

    # Органический старт с живыми покупателями (кейс $FWOG: 37b/0s -> ракета +184%):
    _txns = pair_data.get("txns", {}).get("m5", {}) if isinstance(pair_data.get("txns"), dict) else {}
    _b5 = int(_txns.get("buys", 0) or 0)
    _s5 = int(_txns.get("sells", 0) or 0)
    if _b5 >= 25 and _b5 >= _s5 * 1.5:
        return False

    # 1. Liq/FDV >= 70% with Liq >= $18k and 0 socials on custom pools (AXIOMCOIN: $43.5k liq / $53.9k fdv = 80.6%)
    if fdv_usd <= 0.0 or liq_usd >= (fdv_usd * 0.70):
        return True
        
    # 2. Liq/FDV >= 60% with Liq >= $30k and 0 socials
    if liq_usd >= 30000.0 and (liq_usd >= fdv_usd * 0.60):
        return True

    return False


# Backward-compatible alias for all existing imports and call sites
is_pumpswap_liq_fdv_trap = is_permissionless_liq_fdv_trap

# Глобальные лимиты раннеров (доступны всем функциям выше по файлу)
JEV_MAX_MCAP_USD = 5000000.0       # Верхняя граница $5M FDV: открывает доступ к мощным раннерам
SOLANA_MAX_AGE_MIN = 240.0         # До 240 минут (4 часа): захватывает консолидации и мощные вторые волны
JEV_MAX_AGE_MIN = 240.0            # До 240 минут


def evaluate_entry_momentum(
    m5: float,
    m1: float,
    h1: float,
    h6: Optional[float],
    age_min: float,
    vol5_usd: float,
    liq_usd: float,
    buys5: int,
    sells5: int,
    min_h1: float = -15.0,
    min_h6: float = -25.0,
    fdv_usd: float = 0.0,
    allow_absorbed_buyers: bool = False,
) -> Tuple[bool, str]:
    """Strict green-momentum rocket classifier: captures explosive runs & healthy reclaims, strictly eliminates dead flat noise and dumping coins."""
    tot_tx = buys5 + sells5
    vel_ratio = vol5_usd / max(liq_usd, 1.0)

    # 0. АНТИ-BUMP-БОТ / WASH-TRADE ФИЛЬТР:
    # Если на монете >= 10 сделок, но средний чек < $4.00 — это накрутка микро-ботом (кейс Tagoh: 489 сделок по $0.35)!
    if tot_tx >= 10 and (vol5_usd / max(1, tot_tx)) < 4.0:
        return False, f"fake-bump-wash-bot avg ${vol5_usd/max(1, tot_tx):.2f} < $4.00 across {tot_tx} txns"

    # 0.5 МЯГКИЙ ОТСЕВ МЁРТВЫХ ПУСТЫШЕК (не блокируем свежие ракеты вроде NADJA/CSI с объёмом $350-$650):
    _min_tx_gate = 6 if age_min <= 2.5 else (8 if age_min <= 5.0 else int(globals().get("MIN_TXNS_M5", 10)))
    if age_min <= 2.5 and buys5 >= 5:
        _min_vol_gate = 300.0
    elif age_min <= 5.0 and buys5 >= 6:
        _min_vol_gate = 450.0
    else:
        _min_vol_gate = float(globals().get("MIN_VOLUME_M5_USD", 500.0))
    _min_buys_req = 5 if age_min <= 2.5 else (6 if age_min <= 5.0 else 10)
    if tot_tx < _min_tx_gate or buys5 < _min_buys_req:
        return False, f"no-demand-txns {buys5}b/{sells5}s (buys < {_min_buys_req} or tot {tot_tx} < {_min_tx_gate})"
    if vol5_usd < _min_vol_gate:
        return False, f"no-demand-vol5 ${vol5_usd:,.0f} < ${_min_vol_gate:,.0f} (dead micro-pool)"
    if liq_usd >= 1500.0 and vel_ratio < 0.08 and vol5_usd < 2000.0:
        return False, f"dead-turnover v/l {vel_ratio:.2f}x < 0.08x (vol5 ${vol5_usd:,.0f} on liq ${liq_usd:,.0f})"

    # 0.6 ДИНАМИЧЕСКИЙ ПОРОГ ОБОРОТА (мягкий для крупных кап):
    if fdv_usd >= 250000.0:
        _req_fdv_vol = min(15000.0, max(3000.0, fdv_usd * 0.015))
        if vol5_usd < _req_fdv_vol:
            return False, f"low-fdv-turnover ${vol5_usd:,.0f} < ${_req_fdv_vol:,.0f} on FDV ${fdv_usd:,.0f}"
    if liq_usd >= 15000.0 and age_min >= 20.0 and vel_ratio < 0.10 and vol5_usd < 4000.0:
        return False, f"low-liq-turnover v/l {vel_ratio:.2f}x < 0.10x on liq ${liq_usd:,.0f}"

    # 1. МЯГКИЙ ГЕЙТ ПАДАЮЩИХ СВЕЧЕЙ (разрешаем вход даже на красной 1м — отскоки часто зажигают ракету):
    _is_m1_surge = (m1 >= 1.5 and buys5 >= max(8, int(sells5 * 1.10)) and vol5_usd >= 1500.0)
    _m5_min_req = 1.0 if _is_m1_surge else 1.5
    if m5 < _m5_min_req:
        return False, f"m5-falling-knife {m5:+.1f}% < +{_m5_min_req:.1f}% (red or flat 5m candle)"
    if m1 < float(globals().get("MIN_M1_PRICE_CHANGE_PCT", -5.0)):
        return False, f"m1-falling-knife {m1:+.1f}% < {float(globals().get('MIN_M1_PRICE_CHANGE_PCT', -5.0)):+.1f}% (1m candle is dumping)"

    # 2. МЯГКИЙ ПЕРЕВЕС ПОКУПАТЕЛЕЙ (не требуем 30% dominance на старте):
    _req_bs_ratio = float(globals().get("DEFAULT_MIN_BUY_SELL_RATIO", 1.05))
    if not allow_absorbed_buyers and sells5 > 4 and buys5 < int(sells5 * _req_bs_ratio):
        return False, f"sellers-dominating {buys5}b/{sells5}s (need buyers > sellers * {_req_bs_ratio:.2f} in 5m)"

    # 2.5. УБИРАЕМ ЖЁСТКИЙ БАРЬЕР FDV < $30,000 — молодые ракеты часто стартуют с FDV $5k-$25k:
    # (защиту от микро-пулов даёт MIN_LIQUIDITY_USD и MIN_VOLUME_M5_USD, а не жёсткий FDV cutoff)
    # if 0 < fdv_usd < 30000.0:  # УБРАНО — блокировал NEIGHBORS $8,844 (+90%), GTAVI $13,722 (+81%), NEGRO $11,575 (+174%)

    _base_max_fdv = float(globals().get("JEV_MAX_MCAP_USD", 1500000.0))
    _max_fdv_allowed = (_base_max_fdv * 1.65) if (vol5_usd >= 15000.0 and buys5 >= int(sells5 * 1.05)) else _base_max_fdv
    if fdv_usd > 0.0 and fdv_usd > _max_fdv_allowed:
        return False, f"mcap-too-large ${fdv_usd:,.0f} > ${_max_fdv_allowed:,.0f} (limited 2x+ upside)"
    _sol_max_age = float(globals().get("SOLANA_MAX_AGE_MIN", 240.0))
    if age_min > _sol_max_age:
        _is_super_breakout = (vol5_usd >= 15000.0 and buys5 >= int(sells5 * 1.20) and m5 >= 4.0 and age_min <= 360.0)
        if not _is_super_breakout:
            return False, f"too-old age {age_min:.0f}m > {_sol_max_age:.0f}m (stale runner)"
    if age_min >= 45.0 and h6 is not None and float(h6 or 0.0) > MAX_ENTRY_H6_PCT:
        return False, f"h6-exhausted {float(h6 or 0.0):+.1f}% > +{MAX_ENTRY_H6_PCT:.0f}% at {age_min:.0f}m (spent top)"

    _max_h1_allowed = get_max_entry_h1_pct(age_min, vol5_usd, buys5, sells5, liq_usd, m5)
    if h1 > _max_h1_allowed:
        return False, f"h1-exhausted {h1:+.0f}% > +{_max_h1_allowed:.0f}% (buying late parabolic top, dump risk)"

    # Потолок m5 для свежих запусков и кривых: не входим в уже отработавший вертикальный пик (+300%..+425%),
    # где девы и первые снайперы фиксируют прибыль в наш вход (кейсы CLAUDHAO +425% -> -82%, CATEPEPE +410% -> -81%, PEPEVRAX +369% -> -79%).
    # Ракеты вроде Rockstar (+154%), LIFTD (+154%), GTA6 (+35%) стартуют до +200% и дают огромный запас хода!
    _is_dex_migration_surge = bool(
        vol5_usd >= 15000.0 and buys5 >= max(15, int(sells5 * 1.30)) and liq_usd >= 15000.0
    )
    _max_m5_dyn = 400.0 if _is_dex_migration_surge else float(globals().get("MAX_ENTRY_M5_PCT", 220.0))
    _max_m1_dyn = max(
        float(globals().get("MAX_ENTRY_M1_PCT", 35.0)),
        60.0 if (buys5 >= max(10, int(sells5 * 1.20)) and vol5_usd >= 2500.0) else (35.0 if buys5 >= max(8, int(sells5 * 1.05)) and vol5_usd >= 1200.0 else 25.0)
    )
    if m1 > _max_m1_dyn:
        return False, f"m1-vertical-top {m1:+.1f}% > +{_max_m1_dyn:.0f}% (buying candle top, wait for reset)"
    if m5 > _max_m5_dyn:
        return False, f"m5-parabolic-top {m5:+.1f}% > +{_max_m5_dyn:.0f}% (buying exhausted peak, enter at ignition only)"

    # Защита от 1м отскока дохлой кошки внутри 5м слива (кейсы Makenzi m5=+4.98%, m1=+13.74% -> -26.4%; CC m5=+2.85%, m1=+17.86% -> -26.1%):
    # если 1м свеча значительно выше 5м изменения (значит предшествующие 4 минуты токен летел вниз), не покупаем вершину отскока!
    if m1 > 4.0 and m5 < 15.0 and m1 > (m5 + 3.0):
        return False, f"dead-cat-1m-spike m1 {m1:+.1f}% >> m5 {m5:+.1f}% (4m dump before 1m bounce)"

    # Защита от кровоточащего часового тренда без мощного объёма (кейс LASTBOOK age 11.8m, h1 -10.0%, vol5 $7,087 -> -16.4%; GENTLEMEN vol5 $34.3k проходит):
    if h1 < -3.0 and (age_min >= 8.0 or vol5_usd < 20000.0):
        return False, f"bleeding-h1-downtrend h1 {h1:+.1f}% at age {age_min:.1f}m vol5 ${vol5_usd:,.0f}"

    # 1. Свежая миграция / накопление покупателей:
    _fma_vol_req = 700.0 if age_min <= 3.0 else 1000.0
    is_fresh_migration_accumulation = (
        age_min <= 30.0
        and (fdv_usd <= 0.0 or fdv_usd <= 250000.0)
        and liq_usd >= 2500.0
        and buys5 >= max(6, int(sells5 * 1.05))
        and vol5_usd >= _fma_vol_req
        and m5 >= 2.0
        and m1 >= -2.5
    )

    # 1.1 Свежий запуск / реактивный разгон молодых токенов (<15м):
    _uf_vol_req = 650.0 if age_min <= 3.0 else 1000.0
    _uf_min_tx = 7 if age_min <= 3.0 else 10
    is_ultra_fresh_launch = (
        age_min <= 15.0
        and m5 >= 2.0
        and m1 >= -2.5
        and buys5 >= max(5, int(sells5 * 1.10))
        and vol5_usd >= _uf_vol_req
        and tot_tx >= _uf_min_tx
    )

    # 1.2 Вход на откате молодой ракеты (1м микро-пуллбэк на разгоняющемся токене):
    is_fresh_pullback_entry = (
        age_min <= 35.0
        and m5 >= 4.0
        and -3.5 <= m1 <= 2.5
        and buys5 >= max(10, int(sells5 * 1.10))
        and vol5_usd >= 1800.0
    )

    # 1.3 Первичное зажигание ракеты (свежий импульс или подтверждённый живым $ потоком выкуп мелких продаж)
    is_fresh_ignition = (
        age_min <= 35.0
        and m5 >= 2.5
        and m1 >= -2.5
        and (
            buys5 >= max(6, int(sells5 * 1.05))
            or (allow_absorbed_buyers and buys5 >= int(sells5 * 0.85) and m5 >= 8.0 and vol5_usd >= 6000.0)
        )
        and vol5_usd >= (700.0 if age_min <= 3.0 else 1000.0)
        and tot_tx >= (7 if age_min <= 3.0 else 10)
    )

    # 1.4 Гипер-зажигание (сильный пробой)
    is_hyper_ignition = (
        age_min <= SOLANA_MAX_AGE_MIN
        and m5 >= 5.0
        and m1 >= -3.0
        and buys5 >= max(7, int(sells5 * 1.05))
        and vol5_usd >= 1000.0
    )

    # 1.5 Здоровый второй импульс / реклейм уровня
    is_turned_green_dip = (
        age_min <= SOLANA_MAX_AGE_MIN
        and (fdv_usd <= 0.0 or fdv_usd <= float(globals().get("JEV_MAX_MCAP_USD", 1500000.0)))
        and buys5 >= max(8, int(sells5 * 1.00))
        and vol5_usd >= 1200.0
        and liq_usd >= 2500.0
        and m5 >= 2.5
        and m1 >= -3.0
        and (h1 >= -5.0 or vol5_usd >= 3000.0)
    )

    # 1.6 V-образный разворот покупателями
    is_fresh_v_reversal = (
        age_min <= SOLANA_MAX_AGE_MIN
        and m5 >= 3.5
        and m1 >= -2.5
        and buys5 >= max(8, int(sells5 * 1.05))
        and vol5_usd >= 1200.0
        and liq_usd >= 2500.0
        and h1 >= -25.0
    )

    # 1.7 Зрелый пробой (свежие капы с импульсом)
    is_mature_breakout = (
        age_min <= SOLANA_MAX_AGE_MIN
        and m5 >= 2.5
        and h1 >= -20.0
        and m1 >= -2.5
        and buys5 >= max(8, int(sells5 * 1.05))
        and vol5_usd >= 1200.0
        and (vel_ratio >= 0.08 or vol5_usd >= 2000.0)
    )

    # 1.8 Импульсный прорыв консолидации / повторный разгон
    _cb_max_mcap = float(globals().get("JEV_MAX_MCAP_USD", 1500000.0))
    is_consolidation_breakout = (
        age_min <= SOLANA_MAX_AGE_MIN
        and 2.5 <= m5 <= _max_m5_dyn
        and m1 >= -2.0
        and buys5 >= max(7, int(sells5 * 1.05))
        and vol5_usd >= 1000.0
        and (fdv_usd <= 0.0 or fdv_usd <= _cb_max_mcap)
    )

    # 1.9 Взрывной старт 1-минутной свечи (момент зажигания до того, как m5 успела раздуться):
    is_m1_rocket_blast = (
        age_min <= SOLANA_MAX_AGE_MIN
        and m1 >= 1.5
        and m5 >= 0.5
        and buys5 >= max(7, int(sells5 * 1.15))
        and vol5_usd >= 900.0
        and (fdv_usd <= 0.0 or fdv_usd <= _cb_max_mcap)
    )

    if not (
        is_ultra_fresh_launch
        or is_fresh_migration_accumulation
        or is_fresh_pullback_entry
        or is_fresh_ignition
        or is_hyper_ignition
        or is_turned_green_dip
        or is_fresh_v_reversal
        or is_mature_breakout
        or is_consolidation_breakout
        or is_m1_rocket_blast
    ):
        return (
            False,
            f"no-rocket-ignition age {age_min:.1f}m m5 {m5:+.1f}% m1 {m1:+.1f}% h1 {h1:+.1f}% "
            f"b/s {buys5}/{sells5} vol5 ${vol5_usd:,.0f} v/l {vel_ratio:.2f}x",
        )

    if h1 < min_h1 and not is_fresh_v_reversal:
        return False, f"hourly-downtrend h1 {h1:+.1f}% < {min_h1:+.1f}% (choppy downtrend)"

    if age_min >= 45.0 and h6 is not None and float(h6 or 0.0) < min_h6:
        return False, f"h6-downtrend {float(h6 or 0.0):+.1f}% < {min_h6:+.1f}% (coin is bleeding)"

    return True, "ok"

STREAK_COOLDOWN_ENABLED = False
STREAK_MAX_CONSECUTIVE_LOSSES = 5
STREAK_COOLDOWN_MINUTES = 5.0

ROLLING_BREAKER_ENABLED = False
ROLLING_BREAKER_WINDOW = 8
ROLLING_BREAKER_LOSS_USD = 8.0
ROLLING_BREAKER_PAUSE_MIN = 5.0
DD_THROTTLE_ENABLED = False
DD_THROTTLE_START_USD = 15.0

# === ВХОД В ЗРЕЛЫЕ ВЫПУСКНИКИ RAYDIUM / PUMPSWAP И РАННИЕ КРИВЫЕ PUMP.FUN ===
SNIPER_ENTRIES_ENABLED = True             # ВКЛЮЧЕНО: мгновенный вход в свежие кривые/миграции со стрима
ALLOW_UNGRADUATED_PUMPFUN = True          # ВКЛЮЧЕНО: ловим ракеты на кривой Pump.fun до миграции (открывает 大A +1559%, ROCKSTAR +163%, NEIL +52%, BLUE +36%)
MIN_PUMP_CURVE_MCAP_USD = 3200.0          # Порог кривой $3.2k (пропускает ранних лидеров NEIL $3.3k +52%, RAUL $4.1k +38%, но отсекает 0-1 SOL мусор; реальный резерв >=2.2 SOL проверяется ончейн!)
MAX_PUMP_CURVE_MCAP_USD = 25000.0         # Потолок сырой кривой $25k (захватывает растущие кривые $12k-$25k на пути к миграции ~$29k-$30k)
MIN_PUMP_CURVE_MCAP_SOL = 44.0            # Нижняя граница золотой зоны кривой (~44 SOL ≈ $3.2k, ~14 real SOL внутри кривой)
MAX_PUMP_CURVE_MCAP_SOL = 340.0           # Верхняя граница золотой зоны сырой кривой (~340 SOL ≈ $25k при миграции 410 SOL)
PUMP_MIGRATION_MCAP_SOL = 410.0           # FDV миграции pump.fun
PUMP_DUMP_FLOOR_SOL = 50.0                # Порог post-mig-dumped (~50 SOL, разрешает быстрые реклеймы)
JEV_MAX_TICKET_USD = 0.0
MIN_GRADUATED_LIQ_USD = 3000.0            # Минимум $3k ликвидности на DEX
AUDITOR_STAIRCASE_ENABLED = False
AUDITOR_MAX_DEV_PCT = 8.0                 # Макс 8% у дева (защита от моментальных дампов разработчика в GROK-JEV)
AUDITOR_MAX_WHALE_PCT = 14.0              # Макс 14% в одном кошельке (защита от 1-клик слива кита как FOMOCOIN/$CHOPS)
AUDITOR_MAX_TOP10_PCT = 48.0              # Макс 48% у топ-10 холдеров (пропускает ракеты уровня FiveM с 40.9%)
STREAM_WINDOW_SEC = 60
STREAM_MIN_UNIQUE_BUYERS = 3
STREAM_MIN_BUY_SOL = 0.6
STREAM_MAX_TOP_BUYER_RATIO = 0.50
STREAM_MIN_BS_RATIO = 1.2
STREAM_MAX_CURVE_PROGRESS = 85.0
STREAM_ENTRY_SIZE_USD = 5.0
STREAM_SLIPPAGE = 0.08
STREAM_MAX_AGE_SEC = 900
STREAM_GC_MIN_PROGRESS = 4.0
STREAM_GC_IDLE_SEC = 180
STREAM_DEV_DUMP_RATIO = 0.25
STREAM_MAX_DEV_BUY_TOKENS = 100_000_000
STREAM_MIN_ENTRY_PROGRESS = 3.0
STREAM_MOMENTUM_MIN_RATIO = 0.35

VIP_ENTRIES_ENABLED = False

# === VIP ALPHA TRADERS & KOL WALLETS (Прямой снайпинг топ-трейдеров Pump.fun) ===
# При покупке этими кошельками бот моментально копирует сделку с обходом фильтров (объём, возраст, свечи, ML)
VIP_KOL_WALLETS = {
    "GV6UUmNxz2RpKxmNAPadYKb7uQpszwqQAu3qLJxVdC52": "@Ansem (Top KOL / Caller #1, +$1.37M)",
    "9tH2zRYdS3X7PEcsPno3GST1EHe8GX8CSNih1pKpamAp": "@faceofsol (Top Trader #2, +$728k)",
    "2EJPiGHWCddwUnoWoRHD3dfycjQwDV9iQceshn2HDpht": "@bronbroncore (Top Trader #3, +$575k)",
    "AvGiSdrkwtGYhrrFXZPtdhNhV4pASUR67TmRjfaKGdMP": "@wirelyss (Top Caller / KOL #4, +$449k)",
    "Be24Gbf5KisDk1LcWWZsBn8dvB816By7YzYF5zWZnRR6": "@chairman (Top KOL Household Name, 140k followers)",
}
TIME_EXIT_MINUTES = 40
TIME_EXIT_PROFIT_REQ = 0.015

# === ПРОСТАЯ СИСТЕМА ВЫХОДА (МУНБЭГ ОТ +110%) ===
PARTIAL_TP_ENABLED = True
PARTIAL_TP_MIN_TICKET_USD = 0.50
TP1_TRIGGER_PCT = 1.10               # До +110% держим все 100% позиции! На +110% продаём 50% (окупает ставку и оставляет чистый Moonbag)
TP1_SELL_FRAC = 0.50                 # 50% позиции фиксируем на +110%, оставшиеся 50% — Risk-Free Moonbag!
TP2_TRIGGER_PCT = 2.50               # Следующий тейк на +250% (фиксируем половину мунбэга)
TP2_SELL_FRAC = 0.50                 # 50% остатка продаём на +250%, остальное тащим по Trailing Stop 20%
TP3_TRIGGER_PCT = 5.00
TP3_SELL_FRAC = 0.00
TP4_TRIGGER_PCT = 5.00
TP4_SELL_FRAC = 0.00
TP5_TRIGGER_PCT = 10.00
TP5_SELL_FRAC = 0.00
MOONBAG_TRIGGER_PCT = 1.10           # Мунбэг активируется строго на +110%
MOONBAG_SELL_FRAC = 0.50

# Трейлинг для мунбэга (защищает отката: если дошло до +75%, не даёт слить в 0)
MOONBAG_TRAIL_ACTIVATION_PCT = 0.40  # Трейлинг включается при пике от +40%
MOONBAG_TRAIL_DISTANCE_PCT = 0.20    # Плотный трейл 20% от ATH (держит прибыль)
MOONBAG_MAX_HOLD_MIN = 55.0          # Даём ракетам до 55 минут на 5x-20x памп

# (STOP_LOSS_PCT, STOP_LOSS_UNLOCKED_PCT, EMERGENCY_CAP_PCT заданы единым блоком в начале файла)

PUMP_XGB_ENABLED = False
RAY_XGB_ENABLED = False
ROCKET_XGB_ENABLED = True
ROCKET_XGB_STAGE1_THRESHOLD = 14.0   # Порог 14% для DEX-раннеров (пропускает HOGE 15% +158%, GRANDPUMP 25% +214%, DACT 47% +103%; для сырых кривых применяется 20%)
ROCKET_XGB_FINAL_THRESHOLD = 14.0     # Финальный порог 14%
ROCKET_XGB_THRESHOLD = 14.0
ROCKET_XGB_UNLOCKED_CUSTOM_MIN = 14.0  # Для проверенных высокооборотных DEX-раннеров
JEV_MAX_AGE_MIN = 240.0              # Пробои и консолидации до 240м (4ч): позволяет ловить мощные вторые волны и раннеры

STALL_TIMER_SEC = 420.0   # 7 минут на консолидацию до среза по стагнации
STALL_MAX_LOSS_PCT = -0.055

# === ПРОДАЖА НА ФЛЕТЕ В ПЛЮС (фиксируем профит даже если монета стоит на месте) ===
FLAT_PROFIT_ENABLED = True
FLAT_PROFIT_MIN_PNL_PCT = 0.08       # Минимум +8% профита чтобы продать на флете
FLAT_PROFIT_MIN_HOLD_MIN = 5.0       # Держать минимум 5 минут перед продажей на флете
FLAT_PROFIT_PEAK_AGE_MIN = 8.0       # Пик был не менее 8 минут назад (нет новых максимумов)
FLAT_PROFIT_PRICE_DRIFT_PCT = 0.03   # Цена не должна отклоняться от пика более чем на 3% (флет)

TRAILING_ACTIVATION_PCT = 0.400   # Прогрессивный трейлинг включается с +40%
TRAILING_DISTANCE_PCT = 0.300     # Дистанция от пика 30% (не душить нормальные откаты)
TRAILING_RUNNER_DISTANCE_PCT = 0.35
SCALP_PROFIT_ENABLED = False

WHALE_DUMP_GUARD_PCT = 0.22
EVM_RUNNER_CRASH = 0.25
EVM_NORMAL_CRASH = 0.22

BREAKEVEN_PCT = 0.15       # Breakeven Shield: пик >= +15% -> перенос в безубыток (не выбивает YUKON на +10% шуме)


def get_sol_tp_ladder(is_unlocked: bool) -> tuple:
    """Return (tp1_t, tp1_f, tp2_t, tp2_f, tp3_t, tp3_f, tp4_t, tp4_f, tp5_t, tp5_f).
    TP1 +110% (50% позиции -> Risk-Free Moonbag), TP2 +250% (50% остатка)."""
    return (
        float(globals().get("TP1_TRIGGER_PCT", 1.10)),
        float(globals().get("TP1_SELL_FRAC", 0.50)),
        float(globals().get("TP2_TRIGGER_PCT", 2.50)),
        float(globals().get("TP2_SELL_FRAC", 0.50)),
        float(globals().get("TP3_TRIGGER_PCT", 5.00)),
        float(globals().get("TP3_SELL_FRAC", 0.00)),
        float(globals().get("TP4_TRIGGER_PCT", 5.00)),
        float(globals().get("TP4_SELL_FRAC", 0.00)),
        float(globals().get("TP5_TRIGGER_PCT", 10.00)),
        float(globals().get("TP5_SELL_FRAC", 0.00)),
    )

# === ЗАЩИТА ОТ СНОСА ЛИКВИДНОСТИ И СТАГНАЦИИ ПОСЛЕ ЗАПУСКА ===
UNLOCKED_STALL_PROFIT_AGE_SEC = 180.0    # 3 минуты без нового пика на +45%+ незалоченном пуле -> плановая фиксация
UNLOCKED_STALL_MID_AGE_SEC = 180.0       # Синхронизированный таймер стагнации
UNLOCKED_ENTRY_SCRATCH_SEC = 180.0       # Не режем +0.0% на 35с: даём 3 минуты на отрыв импульса (раги уже режет SL -7.5%)
UNLOCKED_FAST_STOP_PCT = -0.075          # Быстрый стоп-лосс -7.5% (1 тик)

PREFETCH_TTL_SEC = 12
SOLANA_MIN_AGE_MIN = 0.05        # 3 секунды: мгновенный старт на самой ранней волне
SOLANA_MAX_AGE_HOURS = 4.0         # До 4 часов
SOLANA_MAX_AGE_MIN = 240.0         # Потолок 240м: позволяет ловить консолидации и мощные выходы в раннеры
JEV_MAX_AGE_MIN = 240.0            # В GROK-JEV фокус на пробоях до 240м (4ч)
JEV_MAX_M1_ENTRY_PCT = 35.0

SCALP_TIMEOUT_MIN = 10.0
STAGNANT_LOSS_MIN = 15.0
STAGNANT_LOSS_MIN_FAST = 12.0
STAGNANT_HOLD_MIN = 20.0

AI_MODE = "degen"
MIN_LIQUIDITY = 2500.0          # Минимум $2,500 реальной ликвидности AMM — не отсекаем молодые ракеты
MAX_LIQUIDITY = 50000000

GROQ_API_KEY = (os.getenv("GROQ_API_KEY") or os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY") or "").strip().strip('"').strip("'")
XAI_API_KEY = (os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY") or "").strip().strip('"').strip("'")
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()

# === GROK BOT x JEV ENGINE ===
JEV_ENABLED = True
JEV_WICK_STOP_ENABLED = False
JEV_EARLY_MIN_AGE_MIN = 0.05   # Ультра-ранний вход от 3с
JEV_MIN_AGE_MIN = 0.08         # Мгновенный вход от 5 секунд (до разгона ракеты)
JEV_MAX_AGE_MIN = 240.0        # В GROK-JEV фокус на пробоях до 240м (4ч)
JEV_MIN_LIQUIDITY_USD = 2500.0   # Минимум $2.5k ликвидности AMM — не отсекаем молодые ракеты с petites пулами
JEV_MAX_LIQUIDITY_USD = 500000.0   # Расширено до $500k: не блокирует ракеты с высокой ликвидностью (фикс GRAM +391%)
JEV_MIN_VOLUME_H1 = 1500.0       # Минимум $1.5k реального оборота вместо $350 шума
JEV_MIN_MCAP_USD = 4500.0      # Минимум $4.5k MCap для ранних ракет
JEV_MAX_MCAP_USD = 5000000.0   # Верхняя граница $5M FDV: пропускает мощные раннеры уровня UHI (+792%) и BJB (+308%)
JEV_MIN_VOLUME_H24 = 1500.0
JEV_MIN_TRADES_H24 = 6
JEV_MIN_TRADES_H1 = 5
JEV_MIN_HOLDERS = 8             # Минимум 8 холдеров — не блокируем только что запущенные токены
JEV_MAX_TOP_WALLET = 0.14        # Макс 14% на один кошелёк (вместо 30%)
JEV_MAX_TOP_10 = 0.48            # Макс 48% у топ-10 холдеров (пропускает ракеты уровня FiveM)
# UNLOCKED_CUSTOM_MIN_* заданы единым блоком вверху (lines 104-108)
JEV_REQUIRE_HOLDER_DATA = False  # Не ждём индексации холдеров в RugCheck/GoPlus на свежих пулах
MAX_POOL_LIQ_FRAC = 0.02
AUDITOR_MAX_DELEGATED_ACCOUNTS = 3
AUDITOR_MAX_DELEGATED_PCT = 5.0
AUDITOR_MAX_BUNDLE_WALLETS = 5
AUDITOR_MAX_BUNDLE_PCT = 12.0
JEV_LLM_ENFORCE = False         # ОТКЛЮЧЕНО: судья пишет вердикт в лог, но шум/галлюцинации LLM не блокируют вход!

JEV_MIN_ACCOUNT_IS_PROJECT = 0.65
JEV_MAX_RECYCLED_ACCOUNT = 0.35
JEV_MAX_CONCENTRATION_RISK = 0.45
JEV_MAX_MOMENTUM_SPENT = 0.65
JEV_MIN_LIQUIDITY_FITS_TICKET = 0.60
JEV_MAX_DEV_STILL_LOADED = 0.35

JEV_BID_THE_FLUSH_ENABLED = True
JEV_STOP_UNDER_WICK_ENABLED = True
JEV_MAX_M1_ENTRY_PCT = 35.0       # Вход в импульс m1: разрешаем до +35% (не блокируем быстрые ракеты)
JEV_DEFAULT_WICK_STOP_PCT = -0.100

BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")
BIRDEYE_CHAINS = ["solana"]

SOLANA_ONLY = True
BASE_ENABLED = False
EVM_WSS_ENABLED = False
ROBINHOOD_ENABLED = False
BSC_ENABLED = False
ETHEREUM_ENABLED = False
USE_JITO_EXECUTION = True
JITO_ENGINE_URL = "https://mainnet.block-engine.jito.wtf/api/v1/bundles"
JITO_TIP_AMOUNT_SOL = 0.0005
JITO_TIP_ACCOUNT = "96gYZGLnJYVFmbjzopPSU6QiCRK4rPdTuQ8hB1aP442b"
LUNARCRUSH_API_KEY = (os.getenv("LUNARCRUSH_API_KEY") or "").strip().strip('"').strip("'")

ROBINHOOD_ENTRIES_ENABLED = False
ROBINHOOD_CHAIN_ID = 4663
ROBINHOOD_DS_SLUG = "robinhood"
ROBINHOOD_GT_NETWORK = "robinhood"
ROBINHOOD_RPC_URL = "https://rpc.mainnet.chain.robinhood.com"
ROBINHOOD_EXPLORER = "https://robinhoodchain.blockscout.com"
ROBINHOOD_MIN_LIQUIDITY = 10000.0
ROBINHOOD_MIN_AGE_MIN = 1.0
ROBINHOOD_MAX_CREATOR_PCT = 0.20
ROBINHOOD_MAX_OWNER_PCT = 0.20
ROBINHOOD_SCAN_INTERVAL = 20
EVM_MIN_M5_PCT = 0.8
EVM_ACCUM_ENABLED = True
EVM_NEW_POOL_PAGES = 5
EVM_RESCAN_COOLDOWN = 60
EVM_MAX_MINTS = 120

BASE_MIN_LIQUIDITY = 4000.0
BASE_SCAN_INTERVAL = 15
BASE_MAX_SIZE_USD = 25.0

BSC_MIN_LIQUIDITY = 4000.0
BSC_SCAN_INTERVAL = 20
BSC_MIN_LP_LOCKED_PCT = 0.70

ETHEREUM_ENTRIES_ENABLED = False

EVM_CHAIN_FEES = {
    "base": (0.015, 0.025, 0.04, 0.005),
    "robinhood": (0.01, 0.02, 0.03, 0.005),
    "bsc": (0.10, 0.15, 0.25, 0.02),
    "solana": (0.02, 0.04, 0.08, 0.01),
    "ethereum": (2.0, 5.0, 12.0, 0.20),
}

CONVICTION_MULT = 2.0
CONVICTION_MIN_M5_PCT = 0.15
CONVICTION_MAX_M5_PCT = 0.55
CONVICTION_MIN_BUYSELL = 1.5
CONVICTION_MIN_LIQ = 20000
MAX_DEPLOYED_PCT = 0.85
ROCKET_VETO_ENABLED = False
ROCKET_VETO_MIN = 0.20

GROWTH_ENABLED = False
GROWTH_WATCHLIST = [
    "So11111111111111111111111111111111111111112",
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
    "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",
]
GROWTH_MIN_LIQ = 200000
GROWTH_MIN_H24_PCT = 8.0
GROWTH_MIN_H1_PCT = 3.0
GROWTH_MAX_H1_PCT = 15.0
GROWTH_MAX_H6_PCT = 80.0
GROWTH_MIN_BUYSELL = 1.2
GROWTH_STOP_PCT = -0.10
GROWTH_TRAIL_ACT = 0.08
GROWTH_TRAIL_DIST = 0.12
GROWTH_STAGNANT_MIN = 120
GROWTH_MAX_POS = 3
GROWTH_SIZE_USD = 5.0
GROWTH_INTERVAL = 75

SNIPER_MIN_UNIQUE_BUYERS = 8
SNIPER_BUYERS_WINDOW_MIN = 5
VIP_MAX_M5_PCT = 0.95
PULLBACK_MIN_M5_PCT = 0.025
PULLBACK_M1_MIN_PCT = -0.08
PULLBACK_M1_MAX_PCT = 0.20
PULLBACK_MAX_H1_PCT = 320.0
PULLBACK_MAX_M5_PCT = 0.95
MIN_VOLUME_M5 = 450            # Минимум $450 за 5м — мягкий порог для ранних ракет
MIN_TXNS_M5 = 10                # Минимум 10 сделок за 5м — подтверждённый активный спрос
MIN_BUY_SELL_RATIO = 1.05       # Покупатели обязаны преобладать над продавцами (мягкий порог для свежих запусков)
LOTTERY_ENABLED = False
LOTTERY_MIN_M5_PCT = 0.60
LOTTERY_SIZE_MULT = 0.30
WHALE_CONSENSUS = 1
WHALE_MAX_RUNUP = 1.35

FAST_STREAM_ENABLED = True
FAST_WINDOW_SEC = 120
FAST_MIN_BUYERS = 3
FAST_MIN_TRADES = 5
FAST_EARLY_MAX_AGE_SEC = 180

JUP_API_KEY = (os.getenv("JUPITER_API") or os.getenv("JUP_API_KEY") or os.getenv("JUPITER_API_KEY") or "").strip().strip('"').strip("'")
JUP_INTERVAL = 25

TG_PREVIEW_ENABLED = False
TG_PREVIEW_CHANNELS = ["pumpfunmemecalls", "pumpfunearlytrending", "cherrytrending"]
TG_PREVIEW_INTERVAL = 90

FOMO_API_KEY = os.getenv("FOMO_API_KEY", "")
FOMO_WS_ENABLED = False
FOMO_FOLLOW_TRADERS = []
FOMO_MIN_USD = 100
FOMO_BOARDS_INTERVAL = 7200
USE_FOMO_SIGNALS = False
MAX_FOMO_BUYS_PER_PASS = 3
TG_MAX_SIZE_USD = 5.0
TG_VIP_SIZE_USD = 5.0
ROB_MAX_SIZE_USD = 10.0
BSC_MAX_SIZE_USD = 10.0
BSC_WEAK_SIZE_USD = 5.0

EVM_TRACK_INTERVAL = 2.5
EVM_IDLE_TRACK_INTERVAL = 6.0

COINGECKO_ENABLED = True

SCOUT_ENABLED = True
SCOUT_INTERVAL = 10
SCOUT_MIN_AGE_SEC = 10
SCOUT_MAX_AGE_SEC = 300
SCOUT_MIN_BUYERS = 3
SCOUT_MIN_TRADES = 5
SCOUT_MIN_LIQ = 2500
SCOUT_MAX_POS = 3
SCOUT_RECOOLDOWN = 60
SCOUT_BIRTH_FALLBACK = False

GOPLUS_ENABLED = True
GOPLUS_MAX_SELL_TAX = 0.10
GOPLUS_CACHE_SEC = 3600

SCAN_CONCURRENCY = 24
SCAN_LOOP_SLEEP_SEC = 1
SCAN_AFTER_ENTRY_SLEEP_SEC = 1
SCAN_LATEST_TTL_SEC = 5
SOLANA_RESCAN_COOLDOWN = 8
SOLANA_FRESH_RESCAN_COOLDOWN = 2

JEV_TREND_CONFIRM_ENABLED = True
JEV_FRESH_RECHECK_ENABLED = True   # Включена финальная проверка цены перед покупкой (защищает от покупки на падающей свече!)
JEV_MIN_M1_ENTRY_FLOOR_PCT = 0.0    # Вход только на растущей 1м свече (m1 >= 0.0%), покупка красного ножа запрещена
JEV_MIN_H1_PCT = -25.0
JEV_MIN_H6_PCT = -35.0
SYMBOL_REBUY_COOLDOWN_SEC = 1800
MINT_LOSS_COOLDOWN_SEC = 1800

USE_COPYTRADE = False
COPY_MIN_TIER = 3
COPY_MAX_TICKET_USD = 5.0
COPY_MAX_CONCURRENT_POSITIONS = 3  # До 3 слотов под копитрейд/VIP
COPY_USE_ANTISCAM = True
COPY_MAX_ACTIVE_WHALES = 80
COPY_BAN_ON_FIRST_LOSS = False  # Не банить проверенных китов и KOL на первом же стопе!
COPY_PREFILTER = True
COPY_MAX_TX_PER_MIN = 40
COPY_MIN_PNL = 250.0

# === АНАЛИЗ КОШЕЛЬКОВ И ЗАЩИТА ОТ МГНОВЕННЫХ РАГОВ (предвходовой аудит) ===
RUG_WALLET_AUDIT_ENABLED = False          # Отключено по режиму быстрого входа 0d2b1ae: не задерживаем вход долгим RPC-аудитом кошельков
RUG_MAX_DEV_PCT = 0.15                    # Дев до 15%
RUG_MAX_TOP10_PCT = 0.55                  # Топ-10 холдеров до 55%
RUG_REQUIRE_MINT_AUTH_DISABLED = True     # Требуем, чтобы mint authority был выключен (иначе дев может напечатать новых токенов)
RUG_REQUIRE_FREEZE_AUTH_DISABLED = True   # Требуем, чтобы freeze authority был выключен (иначе дев может заморозить продажу)
RUG_REQUIRE_LP_LOCKED_OR_BURNED = False   # Режим 0d2b1ae: лок/сжигание LP не требуется
RUG_BUNDLE_WINDOW_SEC = 30                # Окно для обнаружения бандла покупок (кошельки покупают в эту секунду)
RUG_MAX_BUNDLED_WALLETS = 3               # Максимум связанных кошельков, купивших в бандле (больше = подозрительно)
RUG_FUNDING_CHAIN_DEPTH = 3               # Глубина отслеживания источника финансирования кошельков (сколько переходов SOL)
RUG_MAX_WALLETS_WITH_SAME_FUNDER = 2      # Максимум кошельков с одним источником финансирования
RUG_EARLY_SELL_WINDOW_SEC = 60            # Окно после входа для отслеживания ранних продаж дева/покупателей
RUG_EARLY_SELL_MAX_PCT = 0.15             # Если дев или ранний покупатель продал более 15% за RUG_EARLY_SELL_WINDOW_SEC — паник-сейл
RUG_WALLET_RPC_TIMEOUT = 5.0              # Таймаут RPC-запросов при анализе кошельков

# === ДЕТЕКТОР МГНОВЕННОГО РАГА ПЕРВОЙ СВЕЧОЙ (First Candle Rug Guard) ===
# Анализ ДО входа: когорта кошельков -> источник финансирования -> кластеры «одного
# источника» (сколько кошельков, сколько % эмиссии держат) + микроструктура рынка.
FCR_ENABLED = True                      # ВКЛЮЧЕНО: защита от мгновенного рага первой свечой и кластеров дева
FCR_BLOCK_SCORE = 0.58                 # score >= этого значения -> вход блокируется (сохраняем блок при 30% дев-холде hard=0.60, убираем ложные срабатывания)
FCR_WARN_SCORE = 0.32                  # score >= этого значения -> предупреждение (лог, не блок)
FCR_COHORT_SIZE = 8                    # Сколько кошельков когорты анализировать максимум
FCR_HOLDER_COUNT = 6                   # Сколько топ-холдеров брать в когорту
FCR_BUYER_COUNT = 4                    # Сколько ранних покупателей пула добавить в когорту
FCR_COHORT_CACHE_TTL = 45.0            # Кеш когорты холдеров по минту
FCR_AUDIT_CACHE_TTL = 45.0             # Кеш вердикта по минту
FCR_FUNDER_CACHE_TTL = 21600           # 6ч: источник финансирования кошелька не меняется
FCR_MIN_CLUSTER_WALLETS = 3            # Кластер «одного источника»: столько кошельков = подозрительно
FCR_MIN_CLUSTER_PCT = 8.0              # ...и держат столько % эмиссии (BLOCK)
FCR_MIN_CLUSTER_PCT_WARN = 4.0         # порог предупреждения по кластеру
FCR_CLUSTER_BURST_SEC = 3600           # Окно, за которое фермер раздаёт SOL суб-кошелькам
FCR_FUNDING_MIN_AMOUNT_SOL = 0.05      # Переводы меньше — пыль, не считаем финансированием
FCR_FRESH_TX_MAX = 8                   # Кошелёк с <= N транзакциями = одноразовый (бот)
FCR_DEEP_FIRST_ACTION = True           # Проверять, что первым действием кошелька была покупка токена
FCR_MAX_TX_BATCH = 30                  # Ограничение батча getTransaction за раз
FCR_MIN_LIQ_USD = 10000.0             # Ниже — тонкий пул, раг стоит дёшево
FCR_MAX_VOL_TO_LIQ = 8.0               # v/l выше — шторм сделок при малой глубине
FCR_MIN_AGE_MIN = 0.6                  # Моложе 36с — зона мгновенного бандл-рага первой свечи
FCR_SPIKE_M5 = 90.0                    # Памп за 5м...
FCR_STALL_M1 = 3.0                     # ...при мёртвом m1 = дев разгружает стек в толпу
FCR_SELL_DOMINANCE = 0.85              # sells/buys за 5м для сигнала "продавцы давят"
FCR_MIN_SELL_COUNT = 120               # Абсолютный минимум продаж, чтобы сигнал был значимым
FCR_TRUSTED_FUNDERS = []              # Адреса-источники, которым доверяем (свои кошельки, биржи) — их кластеры игнорируются
FCR_MAX_DURATION_SEC = 10.0 if HELIUS_API_KEY else 5.0  # Без приватного Helius не держим вход 13с на публичных RPC (иначе покупаем опоздавший дамп)
FCR_GRAPH_MIN_PCT = 0.05              # Узел сети RugCheck с долей < 0.05% — пыль, не участник фермы
FCR_GRAPH_MAX_WALLETS = 15           # Сеть крупнее — это граф переводов толпы, а не ферма (вес режется)
FCR_SIG_PAGE_LIMIT = 200              # Сколько подписей тянуть за раз (лёгкий payload для публичного RPC)
FCR_GENESIS_MIN_PCT = 1.0            # Догружать точную genesis-транзакцию только для холдеров с долей >= 1%

# === ПОСТВХОДОВОЙ МОНИТОРИНГ КОШЕЛЬКОВ (после входа в позицию) ===
WALLET_MONITOR_ENABLED = False            # Отключено: tx_per_min резал ракеты на 0-1м из-за высокой активности торговли!
WALLET_MONITOR_INTERVAL_SEC = 10          # Каждые 10 секунд проверять новые транзакции по минту
WALLET_NEW_TX_LIMIT = 50                  # Сколько последних подписей проверять за один заход
WALLET_MAX_NEW_BUYERS_PER_MIN = 20        # Максимум новых уникальных покупателей в минуту (больше = боты/сибил)
WALLET_MIN_TX_FOR_REAL_WALLET = 5         # Минимум транзакций в истории кошелька, чтобы считать его "реальным"
WALLET_MAX_AGE_MIN_FOR_NEW = 60           # Кошельки моложе 60 минут считаются "свежими" (подозрительными)
WALLET_SAME_FUNDER_THRESHOLD = 3          # Если более 3 новых кошельков профинансированы с одного адреса — сиビル
WALLET_SELL_BUY_RATIO_ALERT = 3.0         # Если продаж в 3 раза больше покупок от новых кошельков — тревога
WALLET_FLIP_WINDOW_SEC = 120              # Кошелек считается флиппером, если продал в течение этого времени после покупки
WALLET_MAX_FLIPPERS_PCT = 0.40            # Если более 40% новых покупателей — флипперы — паник-сейл
WALLET_DEV_WALLET_ADDR = ""               # Кошелек дева (заполняется при входе из pair_data, если известно)
# ============================================================================
# ==================== ОБНОВЛЕННЫЕ НАСТРОЙКИ СКАНЕРА И GROK-JEV ====================

# 1. Отключаем самостоятельные покупки GROK-JEV в обход пайплайна ROCKET-XGB
GROK_JEV_STANDALONE_BUY = False          # GROK-JEV работает только как фильтр/бустер поверх ROCKET-XGB
GROK_JEV_MIN_XGB_SCORE = 4.0             # Минимальный скор ROCKET-XGB для входа по сигналу GROK-JEV (мягкий порог)
ROCKET_XGB_STAGE1_MIN_SCORE = 4.0       # Снижено до 4.0% (пропускает ракеты вроде GTAVI 12.7% -> +248%)

# 2. Мягкие гейты на вход — не отсекаем свежие ракеты (единые для SCANNER, JUP и GROK-JEV)
MIN_LIQUIDITY_USD = 2500.0               # Допускает кривые pump.fun от $2.5k
MIN_FDV_TO_LIQ_RATIO = 1.15              # Защита от кастомных scam-пулов со 100% сапплая
MAX_AGE_MINUTES_FOR_ENTRY = 240.0        # Покупаем токены до 4 часов (не только 45 мин)
MIN_VOLUME_M5_USD = 450.0                # Минимальный 5м объем (мягкий порог для ранних ракет)
MIN_VOL_TO_LIQ_M5 = 0.10                 # Минимальная оборачиваемость ликвидности за 5м
MAX_H1_PUMP_PCT = 600.0                  # Не блокируем токены после роста за 1ч
MIN_M5_PRICE_CHANGE_PCT = 2.0            # Не входим в слабые/паузу 5м свечи (минимум +2%)
MIN_M1_PRICE_CHANGE_PCT = 0.0            # Запрет входа на красной 1м свече (m1 < 0 = блок)

# 3. Адаптивный фильтр покупателей/продавцов (мягкий для свежих запусков)
DEFAULT_MIN_BUY_SELL_RATIO = 1.05        # Для обычных пулов
HIGH_VOL_M5_THRESHOLD = 30000.0          # Если объем за 5м > $30k...
HIGH_VOL_MIN_BUY_SELL_RATIO = 1.05       # ...достаточно перевеса 1.05x

# ==================== ОБНОВЛЕННЫЕ НАСТРОЙКИ ВЫХОДОВ (exit_manager.py) ====================

# 1. Защита от ложных срабатываний Crash Guard (анти-спайк фильтр)
CRASH_GUARD_DROP_PCT = -28.0             # Порог резкого падения
CRASH_GUARD_MIN_HOLD_SEC = 20.0          # Не триггерить в первые 20 сек после покупки (спасет CHAR 6с)
CRASH_GUARD_CONFIRM_TICKS = 2            # Требовать 2 подряд тика падения с интервалом >= 1.5 сек (убирает "за 0 сек")
CRASH_GUARD_IGNORE_IF_PNL_ABOVE = 15.0   # Если мы в плюсе > +15%, работает ступенчатый Trailing Stop, а не паник-селл 100%!

# 2. Исправление Mature Moonbag Floor и Trailing Stop (активный мунбэг с +110%)
MOONBAG_FLOOR_PCT = 5.0                  # Уровень пола безубытка для мунбэга -> +5%
PARTIAL_TP1_PCT = 110.0                  # На +110% забираем 50% позиции (окупаем тело + профит)
PARTIAL_TP1_SELL_FRACTION = 0.50         # 50% оставляем в Moonbag!

# 3. Исправление жесткого Stop Loss и Early Momentum Cut
FAST_STOP_LOSS_PCT = -18.0               # Жесткий стоп -18% (не ждать -31%..-33% как на KLAUDIA и BIGP)
EARLY_MOMENTUM_CUT_SEC = 90.0            # Проверка импульса через 90 сек
EARLY_MOMENTUM_MIN_PEAK_PCT = 3.0        # Если за 90 сек пик был меньше +3%
EARLY_MOMENTUM_MAX_LOSS_PCT = -10.0      # Резать только в диапазоне от -3% до -10% (если ниже -18% — уже сработает обычный SL)