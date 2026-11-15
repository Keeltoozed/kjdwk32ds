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
    if not u or "kpyiiwouhsuaohqtcrfy" in u:
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
PORTFOLIO_SNAPSHOT_MINT = (os.getenv("PORTFOLIO_SNAPSHOT_MINT", "") or "").strip().strip('"').strip("'") or "PORTFOLIO_SOLANA_V2"
INITIAL_BALANCE_USD = 20.0
REINVEST_PERCENT = 22.0
VIRTUAL_POSITION_SIZE_USD = 5.0
TRADE_AMOUNT_USD = 5.0

COMPOUNDING_ENABLED = False
COMPOUNDING_PCT = 0.20
MAX_COMPOUNDING_SIZE_USD = 5.0
DYNAMIC_REINVEST_ENABLED = False
BASE_TRADE_AMOUNT_USD = 5.0
MAX_POSITION_SIZE_USD = 5.0
REINVEST_MIN_POOL_LIQ_USD = 7500.0
SCOUT_SIZE_USD = 5.0
LOTTERY_SIZE_USD = 5.0
MAX_CONCURRENT_POSITIONS = 5

# === ЖЕСТКОЕ ОГРАНИЧЕНИЕ УБЫТКА (РИСК-МЕНЕДЖМЕНТ) ===
MAX_DAILY_LOSS_USD = 1000.0
KILL_SWITCH_ENABLED = False
STOP_LOSS_PCT = -0.095        # Базовый стоп -9.5% как в 0d2b1ae: быстро режем слабые входы без -15%..-24%
EMERGENCY_CAP_PCT = -0.115     # Аварийный потолок -11.5%: жёсткий барьер от сквизов вроде BILL (-23%)
INFANT_WINDOW_MIN = 0.5
INFANT_DUMP_PCT = -0.100       # Срез мгновенного рага в первые секунды при обвале <= -10.0%

# === ЗАЩИТА ОТ МГНОВЕННОГО ВЫВОДА ЛИКВИДНОСТИ (девы выдергивают пул в 0) ===
LIQ_DRAIN_EXIT_PCT = 0.40      # Выход если ликвидность упала на 40% от пика (режем до полного обнуления)
LIQ_DRAIN_FAST_PCT = 0.30      # Если за один тик (<2с) ликва просела на 30% — мгновенный паник-сейл
LIQ_MIN_STABLE_PCT = 0.80      # Перед входом: ликва не должна просесть более чем на 20% за последние 10 мин
DEV_MAX_TOKEN_PCT = 0.10       # Дев не должен держать более 10% от供给 (иначе риск дампа)
LIQ_LOCKED_BURNED_MIN_PCT = 0.50  # Минимум 50% ликвидности залокировано или сожжено (LP токены дева)

# === ФИЛЬТРЫ ВХОДА (ОХОТА ЗА СВЕЖИМИ 2X-10X РАКЕТАМИ) ===
MAX_ENTRY_H1_PCT = 1800.0      # Высокий потолок h1: мигрировавшие Pump.fun ракеты ($5k->$200k) всегда имеют +400%..+1500%
MAX_ENTRY_H6_PCT = 2500.0      # Потолок 6h: не душим вторую волну 2x-10x ракет из-за роста от дна бондинга
MAX_ENTRY_M5_PCT = 120.0       # Потолок m5: ловим волну разгона и ранние миграции (до +350% при сильном потоке покупателей)
MAX_ENTRY_M1_PCT = 22.0        # Потолок m1 +22% (и до +30% при пробое с перевесом покупателей, чтобы не резать живой памп)
REQUIRE_SOCIALS_MIN_AGE_MIN = 25.0  # Требуем соцсети ТОЛЬКО после 25 минут (на старте DexScreener не успевает индексировать)


def get_max_entry_h1_pct(
    age_min: float,
    vol_m5: float = 0.0,
    buys_m5: int = 0,
    sells_m5: int = 0,
    liq_usd: float = 0.0,
    m5_pct: float = 1.5,
) -> float:
    """Return dynamic max h1% entry cap: permit curve runup only for fresh migrations (<=15m)."""
    _m5_ok = True if m5_pct is None else (float(m5_pct) >= 1.0)
    _flow_ok = vol_m5 >= 3000.0 and buys_m5 >= int(sells_m5 * 1.10) and _m5_ok
    if age_min <= 15.0:
        return 2500.0 if _flow_ok else 1500.0
    if age_min <= 35.0:
        return 450.0 if (liq_usd >= 15000.0 and vol_m5 >= 8000.0 and _flow_ok) else 300.0
    if age_min <= 60.0:
        return 220.0
    return 150.0


# `.moon` excluded: live Moonit/custom pools frequently report unlocked LP (e.g. EDIDRG..moon 0% lock).
LAUNCHPAD_MINT_SUFFIXES = ("pump", "bonk", "bags")
UNLOCKED_CUSTOM_MIN_LIQ_USD = 60000.0
UNLOCKED_CUSTOM_MIN_AGE_MIN = 45.0
UNLOCKED_CUSTOM_MIN_VOL5_USD = 15000.0
UNLOCKED_CUSTOM_MIN_BUYS5 = 50


def is_launchpad_mint(mint: str) -> bool:
    """Return True if mint belongs to a canonical Solana launchpad (pump, moon, bonk, BAGS)."""
    return str(mint or "").strip().lower().endswith(LAUNCHPAD_MINT_SUFFIXES)


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
    **kwargs,
) -> bool:
    """
    Каноничный стандарт Commit 0d2b1ae:
    Кастомные не-pump токены (Raydium CPMM, Meteora DYN2) ОБЯЗАНЫ иметь заблокированный/сожжённый LP
    (lpLockedPct / lpBurnedPct >= 80%).
    Покупка пулов без сжигания LP открывает дверь мгновенному сносу ликвидности девом (RemoveLiquidity),
    что привело к рагам AXIOMCOIN (-99.9%), MULT (-98%), SURVIVE (-96%) и сливам BUTTHOLE/SI/MENTATS.
    Только нативные токены Pump.fun (bonding curve, PumpSwap canonical, Raydium burned LP)
    не требуют стороннего сертификата лока, так как их LP аппаратно заблокирован контрактом.
    """
    return False


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
    """Exempt liquid, broadly distributed runners on safe LP from soft sybil/graph false positives."""
    if not is_lp_safe:
        return False
    if age_min < 2.0 or liq_usd < 15000.0 or vol5_usd < 15000.0:
        return False
    if buys5 < 80 or (sells5 > 0 and buys5 < int(sells5 * 0.90)):
        return False
    if top1_pct <= 0.0 or top10_pct <= 0.0:
        return False
    if top1_pct > 8.0 or top10_pct > 25.0 or dev_pct >= 8.0:
        return False
    return True


def is_permissionless_liq_fdv_trap(pair_data: Optional[dict]) -> bool:
    """
    Detect permissionless liquidity-drain rugs across Meteora, PumpSwap, Raydium:
    custom pools created with high seeded liquidity matching or near FDV (liq/fdv >= 0.70 with liq >= $18k,
    or liq/fdv >= 0.60 with liq >= $30k) and 0 socials/websites, so deployers can drain the LP minutes later
    (cases: AXIOMCOIN, WASTED, SURVIVE).
    """
    if not isinstance(pair_data, dict):
        return False
    dex = str(pair_data.get("dexId") or "").lower()
    # Pump.fun bonding curves cannot have LP drained by dev
    if dex in ("pump", "pumpfun"):
        return False
        
    liq_usd = float((pair_data.get("liquidity") or {}).get("usd") or 0.0)
    fdv_usd = float(pair_data.get("fdv") or pair_data.get("marketCap") or 0.0)
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

    # 1. Liq/FDV >= 70% with Liq >= $18k and 0 socials (AXIOMCOIN: $43.5k liq / $53.9k fdv = 80.6%)
    if fdv_usd <= 0.0 or liq_usd >= (fdv_usd * 0.70):
        return True
        
    # 2. Liq/FDV >= 60% with Liq >= $30k and 0 socials
    if liq_usd >= 30000.0 and (liq_usd >= fdv_usd * 0.60):
        return True

    return False


# Backward-compatible alias for all existing imports and call sites
is_pumpswap_liq_fdv_trap = is_permissionless_liq_fdv_trap


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
) -> Tuple[bool, str]:
    """Balanced 0d2b1ae rocket classifier: catches waves and reclaims, blocks vertical tops & falling knives."""
    tot_tx = buys5 + sells5
    vel_ratio = vol5_usd / max(liq_usd, 1.0)

    if fdv_usd > 0.0 and fdv_usd > JEV_MAX_MCAP_USD:
        return False, f"mcap-too-large ${fdv_usd:,.0f} > ${JEV_MAX_MCAP_USD:,.0f} (limited 2x+ upside)"
    if age_min > SOLANA_MAX_AGE_MIN:
        return False, f"too-old age {age_min:.0f}m > {SOLANA_MAX_AGE_MIN:.0f}m (stale runner)"
    if age_min >= 45.0 and h6 is not None and float(h6 or 0.0) > MAX_ENTRY_H6_PCT:
        return False, f"h6-exhausted {float(h6 or 0.0):+.1f}% > +{MAX_ENTRY_H6_PCT:.0f}% at {age_min:.0f}m (spent top)"

    _max_h1_allowed = get_max_entry_h1_pct(age_min, vol5_usd, buys5, sells5, liq_usd, m5)
    if h1 > _max_h1_allowed:
        return False, f"h1-exhausted {h1:+.0f}% > +{_max_h1_allowed:.0f}% (buying late parabolic top, dump risk)"

    # Block vertical top-chasing and active falling knives
    _max_m1_dyn = 30.0 if (buys5 >= max(10, int(sells5 * 1.15)) and vol5_usd >= 3000.0 and m5 >= 4.0) else float(globals().get("MAX_ENTRY_M1_PCT", 22.0))
    if age_min <= 15.0 and (buys5 >= sells5 or m5 >= 0.5):
        _min_m1_dyn = -5.0
    elif (vol5_usd >= 10000.0 and buys5 >= int(sells5 * 1.15)) or (h1 >= 25.0 and buys5 >= int(sells5 * 1.25) and vol5_usd >= 4500.0):
        _min_m1_dyn = -4.5
    elif m5 >= 5.0 and buys5 >= sells5:
        _min_m1_dyn = -4.0
    else:
        _min_m1_dyn = -3.5

    _max_m5_dyn = 400.0 if (age_min <= 15.0 and buys5 >= max(12, int(sells5 * 1.12)) and vol5_usd >= 3500.0) else (
        350.0 if (buys5 >= max(10, int(sells5 * 1.10)) and vol5_usd >= 3000.0) else max(float(globals().get("MAX_ENTRY_M5_PCT", 120.0)), 135.0)
    )
    if m1 > _max_m1_dyn:
        return False, f"m1-vertical-top {m1:+.1f}% > +{_max_m1_dyn:.0f}%"
    if m1 < _min_m1_dyn:
        return False, f"m1-falling-knife {m1:+.1f}% < {_min_m1_dyn:+.1f}%"
    if m5 > _max_m5_dyn:
        return False, f"m5-parabolic-top {m5:+.1f}% > +{_max_m5_dyn:.0f}%"

    # 1. Раннее накопление / ретест свежей миграции (золотая зона входа $40k-$135k FDV):
    # Токен только вышел на DEX (<=25м), ликвидность на месте (>= $5k), объём идёт,
    # покупатели активны (buys >= sells * 0.90), цена не падает лавиной (m5 >= -6.5%, m1 >= -4.5%):
    _fma_vol_req = 800.0 if age_min <= 2.5 else 1200.0
    is_fresh_migration_accumulation = (
        age_min <= 25.0
        and (fdv_usd <= 0.0 or fdv_usd <= 135000.0)
        and liq_usd >= 5000.0
        and buys5 >= max(4, int(sells5 * 0.90))
        and vol5_usd >= _fma_vol_req
        and m5 >= -6.5
        and m1 >= -4.5
    )

    # 1.1 Свежий запуск / реактивный разгон молодых токенов (<10м):
    _uf_vol_req = 600.0 if age_min <= 2.0 else (900.0 if age_min <= 4.0 else 1200.0)
    _uf_m1_floor = -5.0 if (buys5 >= sells5 or m5 >= 1.0) else -3.0
    _uf_min_tx = 5 if age_min <= 2.0 else 7
    is_ultra_fresh_launch = (
        age_min <= 10.0
        and m5 >= 0.8
        and m1 >= _uf_m1_floor
        and buys5 >= max(4, int(sells5 * 0.95))
        and vol5_usd >= _uf_vol_req
        and tot_tx >= _uf_min_tx
    )

    # 1.2 Вход на откате молодой ракеты (1м микро-пуллбэк на разгоняющемся токене):
    is_fresh_pullback_entry = (
        age_min <= 35.0
        and m5 >= 2.5
        and -5.0 <= m1 <= -0.5
        and buys5 >= max(6, int(sells5 * 1.00))
        and vol5_usd >= 1200.0
    )

    is_fresh_ignition = (
        (
            age_min <= 45.0
            and m5 >= 0.8
            and m1 >= -3.5
            and buys5 >= max(5, int(sells5 * 0.98))
            and vol5_usd >= 1400.0
            and tot_tx >= 7
        )
        or (
            age_min <= 45.0
            and m5 >= -2.5
            and m1 >= -1.0
            and buys5 >= max(6, int(sells5 * 1.02))
            and vol5_usd >= 1000.0
            and tot_tx >= 8
        )
    )
    is_hyper_ignition = (
        m5 >= 8.0
        and m1 >= -3.5
        and buys5 >= max(6, int(sells5 * 1.02))
        and vol5_usd >= 2000.0
    )
    # Healthy dip / post-migration reclaim & active second-wave consolidation (catches Socks, HOGE, DOJE without dead bleeders)
    is_turned_green_dip = (
        age_min <= 90.0
        and (fdv_usd <= 0.0 or fdv_usd <= 220000.0)
        and buys5 >= max(8, int(sells5 * 1.05))
        and vol5_usd >= 3000.0
        and liq_usd >= 7500.0
        and (
            (m5 >= -3.5 and m1 >= -2.5 and h1 >= 5.0)
            or (h1 >= 10.0 and m5 >= -15.0 and m1 >= -4.5)
            or (m5 >= -4.5 and m1 >= -4.5 and vol5_usd >= 8000.0 and buys5 >= max(20, int(sells5 * 1.10)))
        )
    )
    is_fresh_v_reversal = (
        age_min <= 45.0
        and m5 >= 6.0
        and m1 >= -1.5
        and buys5 >= int(sells5 * 1.15)
        and vol5_usd >= 3500.0
        and liq_usd >= 7500.0
        and h1 >= -25.0
    )
    is_mature_breakout = (
        age_min <= SOLANA_MAX_AGE_MIN
        and m5 >= 1.5
        and h1 >= -12.0
        and m1 >= -3.5
        and buys5 >= max(8, int(sells5 * 1.02))
        and vol5_usd >= 2200.0
        and (vel_ratio >= 0.08 or vol5_usd >= 6000.0)
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

# === ВХОД В ЗРЕЛЫЕ КРИВЫЕ PUMP.FUN ДО МИГРАЦИИ + ЗАЩИТА DEX ===
SNIPER_ENTRIES_ENABLED = False            # ВЫКЛЮЧЕНО: защита от мгновенных дев-дампов на пустых кривых
ALLOW_UNGRADUATED_PUMPFUN = False         # ВЫКЛЮЧЕНО: запрет сырых bonding curves (блокировка рагов Bagpad -99.9%, Percolator $0 liq). Торгуем ТОЛЬКО проверенные миграции Raydium/PumpSwap с сожжённым LP!
MIN_PUMP_CURVE_MCAP_USD = 18000.0         # Fallback в USD (если sol_price недоступен)
MAX_PUMP_CURVE_MCAP_USD = 44000.0         # Fallback в USD
MIN_PUMP_CURVE_MCAP_SOL = 80.0            # Порог кривой pump.fun в SOL
MAX_PUMP_CURVE_MCAP_SOL = 350.0           # Верх кривой перед миграцией
PUMP_MIGRATION_MCAP_SOL = 410.0           # FDV миграции pump.fun (~410 SOL = ~$35k при SOL $85)
PUMP_DUMP_FLOOR_SOL = 100.0               # Порог post-mig-dumped (~100 SOL = ~$7.5k, разрешает Dip Reclaim ракеты вроде SCRAP/FOMO/ICC)
JEV_MAX_TICKET_USD = 0.0
MIN_GRADUATED_LIQ_USD = 5000.0           # На DEX минимум $5k реальной ликвидности — пускает свежие миграции PumpSwap/Raydium ($5k-$12k при SOL $75-$150) и отсекает пустые микро-пулы
AUDITOR_STAIRCASE_ENABLED = True
AUDITOR_MAX_DEV_PCT = 6.0
AUDITOR_MAX_TOP10_PCT = 32.0
STREAM_WINDOW_SEC = 60
STREAM_MIN_UNIQUE_BUYERS = 5
STREAM_MIN_BUY_SOL = 1.2
STREAM_MAX_TOP_BUYER_RATIO = 0.50
STREAM_MIN_BS_RATIO = 1.4
STREAM_MAX_CURVE_PROGRESS = 85.0
STREAM_ENTRY_SIZE_USD = 5.0
STREAM_SLIPPAGE = 0.08
STREAM_MAX_AGE_SEC = 900
STREAM_GC_MIN_PROGRESS = 4.0
STREAM_GC_IDLE_SEC = 180
STREAM_DEV_DUMP_RATIO = 0.25
STREAM_MAX_DEV_BUY_TOKENS = 100_000_000
STREAM_MIN_ENTRY_PROGRESS = 8.0
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

# === ЛЕСТНИЦА ТЕЙК-ПРОФИТОВ (ОХОТА ЗА 2x-10x РАКЕТАМИ: НЕ РЕЖЕМ РОСТ НА +25%) ===
PARTIAL_TP_ENABLED = True
PARTIAL_TP_MIN_TICKET_USD = 20.0     # Не дробим $5 билеты: $5 позиции едут целиком за 2x-10x ракетами!
TP1_TRIGGER_PCT = 0.85               # Тейк-профит на +85% только для крупных билетов >= $20
TP1_SELL_FRAC = 0.25
TP2_TRIGGER_PCT = 1.10               # На +110% (2.1x) фиксируем 40% от остатка
TP2_SELL_FRAC = 0.40
TP3_TRIGGER_PCT = 2.50               # На +250% (3.5x) фиксируем 45% от остатка
TP3_SELL_FRAC = 0.45
TP4_TRIGGER_PCT = 5.00               # На +500% (6x) фиксируем 50% от остатка
TP4_SELL_FRAC = 0.50
TP5_TRIGGER_PCT = 9.00               # На +900% (10x) закрываем остаток
TP5_SELL_FRAC = 1.00
MOONBAG_TRIGGER_PCT = 0.55           # Статус Moonbag начинается от TP1 (+55%)
MOONBAG_SELL_FRAC = 0.0
# === МУНБЭГ-ТРЕЙЛИНГ (даём ракете пробить 2x и везти до 3.5x-10x без раннего срыва) ===
MOONBAG_TRAIL_ACTIVATION_PCT = 1.00  # Трейлинг мунбэга включается только выше 2x (+100%)
MOONBAG_TRAIL_DISTANCE_PCT = 0.25    # Дистанция трейлинга 25% от пика (переживает здоровые откаты)
MOONBAG_MAX_HOLD_MIN = 55.0          # Мунбэг может ехать до 55 минут

PUMP_XGB_ENABLED = False
RAY_XGB_ENABLED = False
ROCKET_XGB_ENABLED = True
ROCKET_XGB_STAGE1_THRESHOLD = 5.0    # Первичный отсев 5%: возвращает поток 0d2b1ae/d57dc079, отсекая лишь полный мертвяк
ROCKET_XGB_FINAL_THRESHOLD = 8.0     # Финальный порог 8%: не режет ранние 2x+ ракеты (UPCAT 12%, CHICK 13.6% и т.д.)
ROCKET_XGB_THRESHOLD = 8.0

STALL_TIMER_SEC = 420.0   # 7 минут на консолидацию до среза по стагнации
STALL_MAX_LOSS_PCT = -0.055

TRAILING_ACTIVATION_PCT = 0.280   # Прогрессивный трейлинг включается с +28% (после Breakeven Shield +16%)
TRAILING_DISTANCE_PCT = 0.180     # Дистанция от пика 18% (не отдаём +88% прибыль обратно в +12%!)
TRAILING_RUNNER_DISTANCE_PCT = 0.22
SCALP_PROFIT_ENABLED = False

WHALE_DUMP_GUARD_PCT = 0.22
EVM_RUNNER_CRASH = 0.25
EVM_NORMAL_CRASH = 0.22

BREAKEVEN_PCT = 0.16       # Breakeven Shield ВКЛЮЧЕН как в 0d2b1ae: пик >= +16% -> сделка не может закрыться в минус!

# === ЗАЩИТА ОТ СНОСА ЛИКВИДНОСТИ НА НЕЗАЛОЧЕННЫХ ПУЛАХ (UNLOCKED POOL STALL GUARD) ===
UNLOCKED_STALL_PROFIT_AGE_SEC = 20.0     # 20 секунд застоя цены после пика >= +18% -> мгновенный выход с прибылью (кейс PUNCHAO +40.3% до Remove LP)
UNLOCKED_STALL_MID_AGE_SEC = 24.0        # 24 секунды застоя после пика +10%..+18% -> фиксация прибыли
UNLOCKED_ENTRY_SCRATCH_SEC = 55.0        # 55 секунд без импульса (< +4%) на старте -> срез в безубыток
UNLOCKED_FAST_STOP_PCT = -0.075          # Быстрый стоп-лосс -7.5% на незалоченном пуле (1 тик)

PREFETCH_TTL_SEC = 12
SOLANA_MIN_AGE_MIN = 0.75        # 45 секунд: отсекает 41-секундные флеш-раги первых секунд (SURVIVE/HOPE/GTACOIN), ловя все ранние ракеты (TAOTAO 49с, HOGE 51с, AXIOM 60с)
SOLANA_MAX_AGE_HOURS = 1.5        # До 1.5 часов (90м) — защита от захода в отработавшие и сливающиеся топы
SOLANA_MAX_AGE_MIN = 90.0         # Потолок 90 минут для сканера (не покупаем старые измученные монеты)
JEV_MAX_AGE_MIN = 60.0            # В GROK-JEV свежие монеты до 60м (защита от зомби-монет)
JEV_MAX_M1_ENTRY_PCT = 25.0

SCALP_TIMEOUT_MIN = 10.0
STAGNANT_LOSS_MIN = 15.0
STAGNANT_LOSS_MIN_FAST = 12.0
STAGNANT_HOLD_MIN = 20.0

AI_MODE = "degen"
MIN_LIQUIDITY = 10000.0         # Минимум $10,000 ликвидности — пускает ранние ракеты $11k-$18k и отсекает микро-пулы
MAX_LIQUIDITY = 50000000

GROQ_API_KEY = (os.getenv("GROQ_API_KEY") or os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY") or "").strip().strip('"').strip("'")
XAI_API_KEY = (os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY") or "").strip().strip('"').strip("'")
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()

# === GROK BOT x JEV ENGINE ===
JEV_ENABLED = True
JEV_WICK_STOP_ENABLED = False
JEV_EARLY_MIN_AGE_MIN = 0.35   # Быстрый ранний вход от 21с для проверенных сильных миграций
JEV_MIN_AGE_MIN = 0.50         # Базовый карантин 30 секунд для защиты от первого дампового тика
JEV_MAX_AGE_MIN = 60.0         # В GROK-JEV ловим свежие монеты до 60м
JEV_MIN_LIQUIDITY_USD = 6000.0   # Минимум $6k ликвидности — пускает ранние ракеты без мёртвых микро-пулов
JEV_MAX_LIQUIDITY_USD = 450000.0
JEV_MIN_VOLUME_H1 = 8000.0
JEV_MIN_MCAP_USD = 6000.0      # Минимум $6k MCap
JEV_MAX_MCAP_USD = 220000.0    # Верхняя граница $220k FDV: покупаем на старте/разгоне ($40k-$140k), строго блокируем покупку на вершине ($450k+)!
JEV_MIN_VOLUME_H24 = 15000.0
JEV_MIN_TRADES_H24 = 60
JEV_MIN_TRADES_H1 = 20
JEV_MIN_HOLDERS = 50
JEV_MAX_TOP_WALLET = 0.16
JEV_MAX_TOP_10 = 0.32
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
JEV_MAX_M1_ENTRY_PCT = 25.0       # Вход в импульс m1: разрешаем до +25% (не блокируем быстрые ракеты)
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
MIN_VOLUME_M5 = 2000            # Минимум $2,000 за 5м — ловит ранние ракеты (WILL, FOOD, BUFFAPE, SYRUP)
MIN_TXNS_M5 = 20                # 20 транзакций за 5м — активный интерес (меньше = мертвая монета)
MIN_BUY_SELL_RATIO = 1.05       # Покупателей на 5%+ больше продавцов (настоящий импульс вверх)
LOTTERY_ENABLED = False
LOTTERY_MIN_M5_PCT = 0.60
LOTTERY_SIZE_MULT = 0.30
WHALE_CONSENSUS = 1
WHALE_MAX_RUNUP = 1.35

FAST_STREAM_ENABLED = True
FAST_WINDOW_SEC = 120
FAST_MIN_BUYERS = 5
FAST_MIN_TRADES = 8
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
SCOUT_INTERVAL = 20
SCOUT_MIN_AGE_SEC = 60
SCOUT_MAX_AGE_SEC = 720
SCOUT_MIN_BUYERS = 5
SCOUT_MIN_TRADES = 12
SCOUT_MIN_LIQ = 12000
SCOUT_MAX_POS = 3
SCOUT_RECOOLDOWN = 180
SCOUT_BIRTH_FALLBACK = False

GOPLUS_ENABLED = True
GOPLUS_MAX_SELL_TAX = 0.10
GOPLUS_CACHE_SEC = 3600

SCAN_CONCURRENCY = 10
SCAN_LOOP_SLEEP_SEC = 3
SCAN_AFTER_ENTRY_SLEEP_SEC = 3
SCAN_LATEST_TTL_SEC = 10
SOLANA_RESCAN_COOLDOWN = 30
SOLANA_FRESH_RESCAN_COOLDOWN = 8

JEV_TREND_CONFIRM_ENABLED = False
JEV_FRESH_RECHECK_ENABLED = True   # Включена финальная проверка цены перед покупкой (защищает от покупки на падающей свече!)
JEV_MIN_M1_ENTRY_FLOOR_PCT = -3.5
JEV_MIN_H1_PCT = -15.0             # Защита от затяжных нисходящих пил без отсечения здоровых откатов
JEV_MIN_H6_PCT = -25.0             # Макс просадка на 6ч для кандидатов

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
RUG_WALLET_AUDIT_ENABLED = True           # Включить проверку кошельков дева и ранних покупателей перед входом
RUG_MAX_DEV_PCT = 0.10                    # Дев не должен держать более 10% от эмиссии (иначе риск мгновенного дампа)
RUG_MAX_TOP10_PCT = 0.35                  # Топ-10 холдеров не более 35%
RUG_REQUIRE_MINT_AUTH_DISABLED = True     # Требуем, чтобы mint authority был выключен (иначе дев может напечатать новых токенов)
RUG_REQUIRE_FREEZE_AUTH_DISABLED = True   # Требуем, чтобы freeze authority был выключен (иначе дев может заморозить продажу)
RUG_REQUIRE_LP_LOCKED_OR_BURNED = False   # Разрешаем незалоченный LP при контроле выхода по таймауту 28м
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
FCR_ENABLED = True                      # Включить предвходовой анализ мгновенного рага
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
FCR_MAX_DURATION_SEC = 14.0           # Жёсткий дедлайн анализа: не успел -> fail-open (вход разрешён)
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
