import os
import time
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
MAX_CONCURRENT_POSITIONS = 4

# === ЖЕСТКОЕ ОГРАНИЧЕНИЕ УБЫТКА (РИСК-МЕНЕДЖМЕНТ) ===
MAX_DAILY_LOSS_USD = 15.0
KILL_SWITCH_ENABLED = True
STOP_LOSS_PCT = -0.20          # Единый стоп -20%: переживает стартовые фитили ракет (VICE -16.2% -> +82.8%), но чётко режет дампы
STOP_LOSS_UNLOCKED_PCT = -0.15 # Строгий стоп -15% для незалоченных пулов
EMERGENCY_CAP_PCT = -0.28      # Аварийный потолок -28%: срез мгновенного краша
INFANT_WINDOW_MIN = 0.5
INFANT_DUMP_PCT = -0.220       # Срез мгновенного рага в первые 30с (<= -22.0%)

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
MAX_ENTRY_M5_PCT = 175.0        # Потолок m5 +175% (для сырой кривой +165%: отсекает PPV +279% / AFTERGTA6 +205%, пропускает DCTC +132%)
MAX_ENTRY_M1_PCT = 22.0         # Потолок 1м свечи +22% (отсекает покупку вертикального хая типа MAGA m1 +37.5%; все ракеты <= +9.4%)
# MAX_ENTRY_M5_SPIKE_PCT убран — не блокируем вход по m5, пусть ракеты летят
REQUIRE_SOCIALS_MIN_AGE_MIN = 9999.0  # Соцсети не обязательны (отключено)

# Строгий белый список котируемых активов Solana (исключаем xStocks TTWO/QQQx/SPCX — кейсы JETSKI -42.7%, FOILED -34.0%):
ALLOWED_SOL_QUOTE_ADDRS = frozenset({
    "so11111111111111111111111111111111111111112",  # WSOL/SOL
    "epjfwdd5aufqssqem2qn1xzybapc8g4weggkzwytdt1v",  # USDC
    "es9vmfrzacermjfrf4h2fyd4kconky11mcce8benwnyb",  # USDT
    "usd1ttgy1n17szxhfd31eyqso32682556356108733",   # USD1
    "usd1ttgy1n17neehlmeloaybftrbuserhqyiqzvemub",  # USD1 canonical
})
ALLOWED_SOL_QUOTE_SYMBOLS = frozenset({
    "SOL", "WSOL", "USDC", "USDT", "USD1", "USDS", "PYUSD",
})


def is_allowed_solana_quote(pair_data: Optional[dict]) -> bool:
    """Return True ONLY for SOL / canonical stablecoin quote pools (rejects TTWO/xStocks/meme-quoted pairs)."""
    if not isinstance(pair_data, dict):
        return True
    qt = pair_data.get("quoteToken")
    if not isinstance(qt, dict) or not qt:
        return True
    q_addr = str(qt.get("address") or "").strip().lower()
    q_sym = str(qt.get("symbol") or "").strip().upper()
    if not q_addr and not q_sym:
        return True
    if q_addr and q_addr in ALLOWED_SOL_QUOTE_ADDRS:
        return True
    if q_sym and q_sym in ALLOWED_SOL_QUOTE_SYMBOLS and (not q_addr or q_addr in ALLOWED_SOL_QUOTE_ADDRS):
        return True
    # Если передан только символ без адреса (синтетика) — проверяем по белому списку символов
    if not q_addr and q_sym in ALLOWED_SOL_QUOTE_SYMBOLS:
        return True
    return False


def get_max_entry_h1_pct(
    age_min: float,
    vol_m5: float = 0.0,
    buys_m5: int = 0,
    sells_m5: int = 0,
    liq_usd: float = 0.0,
    m5_pct: float = 1.5,
    fdv_usd: float = 0.0,
    is_curve: bool = False,
) -> float:
    """Return dynamic max h1% entry cap: allow genuine fresh launches while rejecting vertical tops (DATITTY/BNC/RUGPULL +426%)."""
    _m5_ok = True if m5_pct is None else (float(m5_pct) >= 1.0)
    _flow_ok = vol_m5 >= 2500.0 and buys_m5 >= int(sells_m5 * 1.05) and _m5_ok
    # Свежий выпускник Pump.fun/DEX (<=8м) ещё у пола миграции (FDV <= $65k):
    # рост с бондинг-кривой $5k -> $35k даёт h1 +350%..+700% ДО начала пампа на PumpSwap!
    # Не убиваем его как «поздний хай», пока FDV <= $65k (скамы вроде APPAD отсекаются RugCheck/L7a/cabal-churn).
    if not is_curve and age_min <= 8.0 and 0.0 < float(fdv_usd or 0.0) <= 65000.0 and _flow_ok and liq_usd >= 7500.0:
        return 750.0
    if age_min <= 15.0:
        if _flow_ok and liq_usd >= 15000.0:
            return 380.0
        return 280.0 if _flow_ok else 220.0
    if age_min <= 30.0:
        return 320.0 if _flow_ok else 220.0
    if age_min <= 60.0:
        return 280.0 if _flow_ok else 180.0
    return 240.0 if _flow_ok else 150.0


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

    # СТРОГИЙ БЛОК Meteora DLMM / незалоченных пулов Meteora:
    # В незалоченных пулах Meteora дев в любой момент забирает quote SOL в 1 клик (кейс Cyberleek -$3.02).
    # Никакой режим (включая Rug-Hunter) не должен входить в незалоченную Meteora DLMM!
    if isinstance(pair_data, dict):
        _dex_u = str(pair_data.get("dexId") or "").lower()
        _labels_u = [str(l).upper() for l in (pair_data.get("labels") or [])]
        if ("meteora" in _dex_u or "DLMM" in _labels_u) and _dex_u != "meteoradbc" and not bool(pair_data.get("_lp_verified")):
            return False
        if is_pumpswap_liq_fdv_trap(pair_data):
            return False

    # Допуск для проверенных кандидатов стратегии Rug-Hunter (SEEDED-LP / UNLOCKED-LP / CABAL-SURF):
    # Они уже прошли полный аудит rug_hunter.qualify_unlocked_lp_hunter (liq>=$7.5k, age>=0.8m,
    # buys>=20, buys>=sells*1.15, dev balance & dump monitor) и защищены тайм-кэпом 5м и автовыходом при сливе LP
    if bool((pair_data or {}).get("_rug_hunter_mode") or kwargs.get("is_rug_hunter")):
        return True

    # ЖЁСТКИЙ ЗАПРЕТ незалоченных пулов при ALLOW_UNLOCKED_LP_POOLS = False:
    # Не позволяем кастомным незалоченным пулам (Meteora DYN2, PumpSwap CreatePool без burn,
    # кастомный Raydium с LP lock 0%) обходить запрет и выносить ликвидность в $0 (AudaCity, MUFFINS, CLIVE, VICECITY).
    if not bool(globals().get("ALLOW_UNLOCKED_LP_POOLS", False)):
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
    """Exempt ONLY genuinely mature, deep, established tokens from soft sybil/graph false positives.
    NEVER exempt fresh PumpSwap launches (<45m or liq <$60k) — slow-cook cabal bundle rugs like FIONA (-96.3%)
    deliberately keep top1/top10 low across 50 sub-wallets and fake $200k wash volume to bypass sybil/L7/L9/FCR!"""
    if not is_lp_safe:
        return False
    if age_min < 45.0 or liq_usd < 60000.0 or vol5_usd < 25000.0:
        return False
    if buys5 < 120 or (sells5 > 0 and buys5 < int(sells5 * 1.15)):
        return False
    if top1_pct > 8.0 or top10_pct > 28.0 or dev_pct > 3.0:
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

    # Органический старт с живыми покупателями: если пул молодой (<=2.5м) и уже есть покупатели (>=6b),
    # это живой импульс ракеты (кейсы USDC +1403%, DCTY +441%, TRADER +203%, NEX AI +224%, MEDTECH +367%, SHELF +668%)!
    # Проверяем В ПЕРВУЮ ОЧЕРЕДЬ, чтобы не душить CPMM-пулы, где liq/fdv математически равен 1.5x-2.0x!
    _txns = pair_data.get("txns", {}).get("m5", {}) if isinstance(pair_data.get("txns"), dict) else {}
    _b5 = int(_txns.get("buys", 0) or 0)
    _s5 = int(_txns.get("sells", 0) or 0)
    _ca_tr = float((pair_data or {}).get("pairCreatedAt") or 0)
    _age_tr = ((time.time() * 1000.0 - _ca_tr) / 60000.0) if _ca_tr > 0 else 0.2
    if (_age_tr <= 2.5 and _b5 >= 6 and (_s5 == 0 or _b5 >= _s5 * 1.05)) or (_b5 >= 18 and _b5 >= _s5 * 1.25):
        return False

    liq_usd = float((pair_data.get("liquidity") or {}).get("usd") or 0.0)
    fdv_usd = float(pair_data.get("fdv") or pair_data.get("marketCap") or 0.0)

    # ЖЁСТКИЙ ТРАП (для неактивных или старых пулов без органики):
    if liq_usd >= 3000.0 and fdv_usd > 0 and _b5 < 6:
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
SOLANA_MAX_AGE_MIN = 240.0         # Базовый потолок 240 минут (4 часа) для обычных монет
JEV_MAX_AGE_MIN = 240.0            # До 240 минут
ESTABLISHED_BREAKOUT_MAX_AGE_MIN = 4320.0  # До 72 часов: пробои проверенных монет с сожжённым LP (нулевой риск дев-рага!)


def is_established_breakout_runner(
    age_min: float,
    liq_usd: float,
    vol5_usd: float,
    m5_pct: float,
    buys5: int,
    sells5: int,
    fdv_usd: float = 0.0,
    is_curve: bool = False,
) -> bool:
    """Единая проверка пробоя проверенного зрелого раннера (4ч–72ч: HNYDRIP +765%, GIGAPIG +161%, LOOSY +72%, ENU +70%).
    У таких монет LP сожжён и дев давно вышел — минимальный риск 1-секундного Jito-рага."""
    if is_curve:
        return False
    if age_min <= 0.0 or age_min > ESTABLISHED_BREAKOUT_MAX_AGE_MIN:
        return False
    if liq_usd < 12000.0 or vol5_usd < 10000.0:
        return False
    if fdv_usd > 0.0 and fdv_usd > 2500000.0:
        return False
    if buys5 < max(40, int(sells5 * 1.20)):
        return False
    if m5_pct >= 10.0:
        return True
    # Сильный высокообъёмный тренд (кейс LOOSY: liq $105k, vol5 $34k, 672b/321s, m5 +7.2%)
    if m5_pct >= 5.0 and vol5_usd >= 18000.0 and buys5 >= max(80, int(sells5 * 1.25)):
        return True
    return False


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
    is_curve: bool = False,
    buys_h1: Optional[int] = None,
    sells_h1: Optional[int] = None,
) -> Tuple[bool, str]:
    """Strict green-momentum rocket classifier: captures explosive runs & healthy reclaims, strictly eliminates dead flat noise and dumping coins."""
    tot_tx = buys5 + sells5
    vel_ratio = vol5_usd / max(liq_usd, 1.0)

    # 0. АНТИ-BUMP-БОТ / WASH-TRADE ФИЛЬТР:
    # Если на монете >= 10 сделок, но средний чек < $4.00 — это накрутка микро-ботом (кейс Tagoh: 489 сделок по $0.35)!
    if tot_tx >= 10 and (vol5_usd / max(1, tot_tx)) < 4.0:
        return False, f"fake-bump-wash-bot avg ${vol5_usd/max(1, tot_tx):.2f} < $4.00 across {tot_tx} txns"

    # 0.1 АНТИ-КАБАЛ STAIRCASE WASH-CHURN ФИЛЬТР (кейсы 3B -100%, BERNIE -95%, WIFGTA6 -75%, CI -68%):
    # Когда кабал-ферма крутит 1000-2500 ботовых сделок за 5м (v/l >= 2.0x) в первые 20м, а цена еле ползёт (m5 < +38%, h1 < +60%, <14% на 1x оборота пула) —
    # это накрутка объёма/лесенки перед 1-секундным Jito Sell-All (104-183 кошелька в 1 блок).
    # Условие h1 < 60% гарантирует, что живые пробои на консолидации (MI h1=+109%, m5=+10.4%) НЕ режутся!
    # Если включён RUG_HUNTER_CABAL_SURF_ENABLED и токен ещё в раннем окне (<=8м, живой спрос) — переводим в ветку RUG-HUNTER:CABAL-SURF с жёстким ранним выходом!
    _rh_cabal_flagged = False
    if (
        not is_curve
        and age_min <= 20.0
        and tot_tx >= 1000
        and vel_ratio >= 2.0
        and 0.0 <= m5 < 38.0
        and h1 < 60.0
        and (m5 / max(vel_ratio, 0.1)) < 14.0
    ):
        _rh_cabal_ok = (
            bool(globals().get("RUG_HUNTER_ENABLED", False))
            and bool(globals().get("RUG_HUNTER_CABAL_SURF_ENABLED", False))
            and age_min <= float(globals().get("RUG_HUNTER_CABAL_MAX_AGE_MIN", 8.0))
            and liq_usd >= float(globals().get("RUG_HUNTER_MIN_LIQ_USD", 8000.0))
            and vol5_usd >= float(globals().get("RUG_HUNTER_MIN_VOL5_USD", 6000.0))
            and sells5 >= 8
            and buys5 >= max(35, int(sells5 * 1.15))
            and m5 >= 8.0
            and m1 >= -1.0
            and (fdv_usd <= 0.0 or fdv_usd <= float(globals().get("RUG_HUNTER_CABAL_MAX_FDV_USD", 180000.0)))
        )
        if _rh_cabal_ok:
            _rh_cabal_flagged = True
        else:
            return (
                False,
                f"cabal-staircase-churn tx5={tot_tx} v/l={vel_ratio:.1f}x m5={m5:+.1f}% h1={h1:+.1f}% eff={m5/max(vel_ratio, 0.1):.1f} (<14% per 1x liq)",
            )

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
        # Для свежих пулов (<=3.5м) не душим старт: от 5 покупок и объём от $250 подтверждают старт ракеты (кейсы BWA +710%, AMC +318%, SHELF +668%)!
        if (age_min <= 3.5 and buys5 >= 5 and vol5_usd >= 250.0 and (sells5 == 0 or buys5 >= sells5 * 1.05)) or (age_min <= 5.0 and buys5 >= 6 and vol5_usd >= 350.0 and (sells5 == 0 or buys5 >= sells5 * 1.10)):
            pass
        else:
            return False, f"dead-turnover v/l {vel_ratio:.2f}x < 0.08x (vol5 ${vol5_usd:,.0f} on liq ${liq_usd:,.0f})"

    # 0.6 ДИНАМИЧЕСКИЙ ПОРОГ ОБОРОТА (мягкий для крупных кап):
    if fdv_usd >= 250000.0:
        _req_fdv_vol = min(15000.0, max(3000.0, fdv_usd * 0.015))
        if vol5_usd < _req_fdv_vol:
            return False, f"low-fdv-turnover ${vol5_usd:,.0f} < ${_req_fdv_vol:,.0f} on FDV ${fdv_usd:,.0f}"
    if liq_usd >= 15000.0 and age_min >= 20.0 and vel_ratio < 0.10 and vol5_usd < 4000.0:
        return False, f"low-liq-turnover v/l {vel_ratio:.2f}x < 0.10x on liq ${liq_usd:,.0f}"

    # 1. ЖЁСТКИЙ ОТСЕВ ЕЛЕ ПОЛЗУЩИХ ТОКЕНОВ (убираем +2%..+5% флет, который скатывается в 0 — кейсы Makenzi +4.98%, CC +2.85%, EDDIE, PUSSY):
    # Исключения: бычий откат на огромном спросе (GTE7 +496%: m5 -14%, vol5 $118k, 1147b/899s),
    # свежий выпускник PumpSwap/DEX (<=8м, liq>=$10k, vol5>=$8k, 45+ покупок >=1.25x продаж, m1>=+0.5% — вход до +80% прострела),
    # мощная органическая аккумуляция (PASSTHEHAT 445b/100s: liq>=$10k, vol5>=$10k, b>=1.60s, avg>=$12, h1>=+15%),
    # или мега-объём >= $18k с 100+ покупками (FREEDOM +8.1%, GENTLEMEN +7.3%).
    _is_heavy_vol_launch = (vol5_usd >= 18000.0 and buys5 >= max(100, int(sells5 * 1.15)) and vel_ratio >= 1.20)
    _is_fresh_mig_early_launch = (
        not is_curve
        and age_min <= 10.0
        and liq_usd >= 7500.0
        and vol5_usd >= 3500.0
        and buys5 >= max(25, int(sells5 * 1.20))
        and m1 >= -1.0
        and h1 >= -5.0
    )
    _is_high_conviction_accum = (
        not is_curve
        and liq_usd >= 7500.0
        and vol5_usd >= 5000.0
        and buys5 >= max(60, int(sells5 * 1.50))
        and (vol5_usd / max(1, tot_tx)) >= 10.0
        and h1 >= 10.0
        and m1 >= -0.5
    )
    _is_clean_grad_flow = (
        not is_curve
        and liq_usd >= 7500.0
        and vol5_usd >= 5000.0
        and buys5 >= max(30, int(sells5 * 1.15))
        and m1 >= -1.0
    )
    _is_rh_early_seed_flow = (
        not is_curve
        and age_min <= 5.0
        and liq_usd >= 7500.0
        and vol5_usd >= 2000.0
        and sells5 >= 10
        and buys5 >= max(35, int(sells5 * 1.20))
        and m1 >= -1.0
        and h1 >= 0.0
    )
    _is_strong_buyer_surge = (
        not is_curve
        and age_min <= 8.0
        and liq_usd >= 7500.0
        and vol5_usd >= 1500.0
        and buys5 >= max(18, int(sells5 * 1.25))
        and m1 >= -1.0
    )
    _is_early_m5_runner = bool(age_min <= 3.5 and (buys5 >= max(8, int(sells5 * 1.05)) or sells5 == 0) and vol5_usd >= 600.0)
    _m5_min_req = 1.5 if _is_early_m5_runner else (3.0 if (_is_high_conviction_accum or _is_strong_buyer_surge) else (3.5 if _is_fresh_mig_early_launch else (5.0 if _is_clean_grad_flow else (6.0 if (_is_heavy_vol_launch or _is_rh_early_seed_flow) else 8.0))))
    _is_bullish_pullback_dip = (
        -18.0 <= m5 < _m5_min_req
        and h1 >= 15.0
        and m1 >= -1.0
        and vol5_usd >= 20000.0
        and buys5 >= max(50, int(sells5 * 1.15))
        and liq_usd >= 10000.0
    )
    if m5 < _m5_min_req and not _is_bullish_pullback_dip:
        return False, f"m5-too-slow {m5:+.1f}% < +{_m5_min_req:.1f}% (slow crawling token, not a rocket)"
    if m1 < float(globals().get("MIN_M1_PRICE_CHANGE_PCT", -2.5)):
        return False, f"m1-falling-knife {m1:+.1f}% < {float(globals().get('MIN_M1_PRICE_CHANGE_PCT', -2.5)):+.1f}% (1m candle is dumping)"

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
    _is_established_brk = is_established_breakout_runner(
        age_min=age_min, liq_usd=liq_usd, vol5_usd=vol5_usd, m5_pct=m5,
        buys5=buys5, sells5=sells5, fdv_usd=fdv_usd, is_curve=is_curve,
    )
    if age_min > _sol_max_age:
        if not _is_established_brk:
            return False, f"too-old age {age_min:.0f}m > {_sol_max_age:.0f}m (stale runner)"
    if age_min >= 45.0 and h6 is not None and float(h6 or 0.0) > MAX_ENTRY_H6_PCT:
        return False, f"h6-exhausted {float(h6 or 0.0):+.1f}% > +{MAX_ENTRY_H6_PCT:.0f}% at {age_min:.0f}m (spent top)"

    _max_h1_allowed = get_max_entry_h1_pct(
        age_min, vol5_usd, buys5, sells5, liq_usd, m5, fdv_usd=fdv_usd, is_curve=is_curve
    )
    if h1 > _max_h1_allowed:
        return False, f"h1-exhausted {h1:+.0f}% > +{_max_h1_allowed:.0f}% (buying late parabolic top, dump risk)"

    # Потолок m5 для свежих запусков и кривых:
    # На сырой кривой Pump.fun базовый потолок +165% (отсекает дистрибуционные хаи AFTERGTA6 +205% m1=0 140s, PPV +279% m1=0 186s).
    # Если сырая кривая активно ускоряется (m1 >= 2%, sells < 80, b/s >= 1.8x — кейс VICESTREAM +280%) — допускаем до +300%.
    # На свежей высокообъёмной миграции (GTASZN +216%, TDC +399% vol5 $80k) и быстром выходе с кривой (age<=5m, FDV<=$55k) — до +450%.
    _is_active_curve_blast = bool(
        is_curve
        and m1 >= 2.0
        and sells5 < 80
        and buys5 >= max(30, int(sells5 * 1.80))
    )
    _is_fresh_curve_grad_climb = bool(
        not is_curve
        and age_min <= 5.0
        and 0.0 < fdv_usd <= 55000.0
        and liq_usd >= 7500.0
        and vol5_usd >= 5000.0
        and buys5 >= max(25, int(sells5 * 1.15))
    )
    _is_mega_mig_blast = bool(
        not is_curve
        and age_min <= 4.0
        and vol5_usd >= 30000.0
        and liq_usd >= 12000.0
        and buys5 >= max(80, int(sells5 * 1.15))
        and m1 >= 2.0
    )
    _is_fresh_grad_blast = bool(
        not is_curve
        and age_min <= 5.0
        and vol5_usd >= 20000.0
        and liq_usd >= 12000.0
        and buys5 >= max(30, int(sells5 * 1.15))
    )
    _is_dex_migration_surge = bool(
        not is_curve
        and age_min <= 10.0
        and vol5_usd >= 15000.0
        and buys5 >= max(15, int(sells5 * 1.10))
        and liq_usd >= 12000.0
    )
    if is_curve:
        _max_m5_dyn = 300.0 if _is_active_curve_blast else 165.0
    elif _is_mega_mig_blast or _is_fresh_curve_grad_climb:
        _max_m5_dyn = 450.0
    elif _is_fresh_grad_blast:
        _max_m5_dyn = 300.0
    elif _is_dex_migration_surge:
        _max_m5_dyn = 240.0
    elif not is_curve and age_min > 10.0 and m1 <= 1.0:
        # Зрелый токен (>10м) со вставшей 1м свечой (m1<=1%): не покупаем выжатую 5м свечу >+120% (кейс ANTCAP m5=+166% m1=0 -> стоп -29% перед +198%)
        _max_m5_dyn = min(float(globals().get("MAX_ENTRY_M5_PCT", 175.0)), 120.0)
    else:
        _max_m5_dyn = float(globals().get("MAX_ENTRY_M5_PCT", 175.0))
    _max_m1_dyn = float(globals().get("MAX_ENTRY_M1_PCT", 22.0))
    if not is_curve and liq_usd >= 10000.0 and buys5 >= max(40, int(sells5 * 1.20)) and vol5_usd >= 12000.0 and m5 >= 25.0:
        _max_m1_dyn = max(_max_m1_dyn, 28.0)
    if m1 > _max_m1_dyn:
        return False, f"m1-vertical-top {m1:+.1f}% > +{_max_m1_dyn:.0f}% (buying candle top, wait for reset)"
    if m5 > _max_m5_dyn:
        return False, f"m5-parabolic-top {m5:+.1f}% > +{_max_m5_dyn:.0f}% (buying exhausted peak, enter at ignition only)"

    # Защита от 1м отскока дохлой кошки внутри 5м слива (кейсы Makenzi m5=+4.98%, m1=+13.74% -> -26.4%; CC m5=+2.85%, m1=+17.86% -> -26.1%):
    # если 1м свеча значительно выше 5м изменения (значит предшествующие 4 минуты токен летел вниз), не покупаем вершину отскока!
    # Исключение (кейсы CORNER +122%, RUDY +30% пробой базы): мощный органический пробой на глубоком пуле и перевесе покупателей.
    _is_organic_v_breakout = (
        not is_curve
        and liq_usd >= 10000.0
        and (
            (m5 >= 5.0 and buys5 >= max(80, int(sells5 * 1.10)) and vol5_usd >= 10000.0)
            or (m5 >= 6.0 and buys5 >= max(50, int(sells5 * 1.25)) and vol5_usd >= 6000.0)
        )
    )
    if m1 > 4.0 and m5 < 15.0 and m1 > (m5 + 3.0) and not _is_organic_v_breakout:
        return False, f"dead-cat-1m-spike m1 {m1:+.1f}% >> m5 {m5:+.1f}% (4m dump before 1m bounce)"

    # Защита от кровоточащего часового тренда (отсекает дохлые отскоки DACI h1=-32% 206b/80s, CASHPARROT h1=-37% 163b/83s),
    # но пропускает экстремальный V-разворот покупателями (кейс DCTYCAT +97%: m5 +64%, 360b/56s = 6.4x, vol5 $5.2k!):
    _h1_flow_not_bleeding = True
    if buys_h1 is not None and sells_h1 is not None and int(sells_h1) > 20:
        _h1_flow_not_bleeding = (int(buys_h1) >= int(int(sells_h1) * 0.98)) or (buys5 >= max(150, int(sells5 * 3.50)))
    _is_strong_v_reversal = (
        m5 >= 20.0
        and m1 >= 0.0
        and buys5 >= max(100, int(sells5 * 2.80))
        and vol5_usd >= 4000.0
        and h1 >= -50.0
        and _h1_flow_not_bleeding
    )
    if h1 < -12.0 and not _is_strong_v_reversal and (age_min >= 8.0 or vol5_usd < 20000.0):
        return False, f"bleeding-h1-downtrend h1 {h1:+.1f}% at age {age_min:.1f}m vol5 ${vol5_usd:,.0f}"

    # 1. Реальный взрывной старт ультра-свежего токена (<=3м): ADPAID (+105%: 16b/4s, m5 +29.8%), WANTED (+55%: 37b/13s, m5 +26%), TDC (+399%)
    # + Свежий выпускник PumpSwap/DEX (<=6м, liq >=$7.5k): в первые минуты на полном пуле $25-35k v/l равен 0.10-0.20x даже при 5x перевесе покупателей (кейс PIPI 119b/23s vol5 $4.1k)!
    _is_explosive_micro_start = (
        age_min <= 2.5
        and m5 >= 20.0
        and m1 >= -1.5
        and buys5 >= max(15, int(sells5 * 2.20))
        and vol5_usd >= 800.0
    )
    _is_fresh_grad_ignition = (
        not is_curve
        and age_min <= 10.0
        and liq_usd >= 7500.0
        and (m5 >= 8.0 or (_is_fresh_mig_early_launch and m5 >= 3.5) or (age_min <= 5.0 and m5 >= 6.0 and buys5 >= max(35, int(sells5 * 1.15)) and sells5 >= 10))
        and m1 >= -2.0
        and vol5_usd >= (2000.0 if (age_min <= 5.0 and buys5 >= 35 and sells5 >= 10) else 2500.0)
        and buys5 >= max(25, int(sells5 * 1.20))
        and (vel_ratio >= 0.07 or vol5_usd >= 2000.0)
    )
    _is_early_ignited_rocket = (
        age_min <= 3.5
        and m5 >= 2.0
        and m1 >= -2.0
        and (buys5 >= max(8, int(sells5 * 1.05)) or (sells5 == 0 and buys5 >= 6))
        and vol5_usd >= 600.0
        and liq_usd >= 6000.0
    )
    is_ultra_fresh_launch = (
        _is_early_ignited_rocket
        or _is_explosive_micro_start
        or _is_fresh_grad_ignition
        or (
            age_min <= 5.0
            and m5 >= 12.0
            and m1 >= -2.5
            and buys5 >= max(20, int(sells5 * 1.20))
            and vol5_usd >= 2500.0
            and (vel_ratio >= 0.40 or vol5_usd >= 5000.0)
        )
    )

    # 1.1 Высокооборотный пробой свежей миграции / раннего раннера (<=25м): GTA6, LGTA6, Billy, RAUL, FREEDOM, GENTLEMEN
    is_fresh_ignition = (
        age_min <= 25.0
        and m1 >= -2.5
        and (
            (
                m5 >= 10.0
                and vol5_usd >= 5000.0
                and (vel_ratio >= 0.35 or vol5_usd >= 10000.0)
                and (
                    buys5 >= max(30, int(sells5 * 1.15))
                    or (allow_absorbed_buyers and buys5 >= max(30, int(sells5 * 0.90)) and m5 >= 15.0 and vol5_usd >= 8000.0)
                )
            )
            or (
                m5 >= 6.0
                and vol5_usd >= 18000.0
                and vel_ratio >= 1.20
                and buys5 >= max(100, int(sells5 * 1.15))
            )
        )
    )

    # 1.2 Сильная вторая волна зрелого раннера (25м..72ч): CDOG (+95%), AudaCity ERD (+53%), DCTYCAT (+97%), Buncoin (+310%),
    # VINTEL (+188%), APOCRYCOIN (+56%), HNYDRIP (+765%), GIGAPIG (+161%), LOOSY (+72%), ENU (+70%), PASSTHEHAT (+61%)
    _mature_min_m5 = 12.0 if _is_established_brk else (35.0 if age_min > 120.0 else (10.0 if (liq_usd >= 20000.0 and buys5 >= max(100, int(sells5 * 1.25)) and vol5_usd >= 10000.0) else 15.0))
    _mature_min_vol5 = 12000.0 if _is_established_brk else (20000.0 if age_min > 120.0 else 8000.0)
    _mature_max_age = ESTABLISHED_BREAKOUT_MAX_AGE_MIN if _is_established_brk else SOLANA_MAX_AGE_MIN
    _is_sharp_mid_breakout = (
        not is_curve
        and 25.0 < age_min <= 120.0
        and m5 >= 25.0
        and m1 >= -2.0
        and h1 >= -10.0
        and vol5_usd >= 3500.0
        and vel_ratio >= 0.35
        and buys5 >= max(25, int(sells5 * 1.25))
        and liq_usd >= 7500.0
    )
    _is_clean_grad_ignition = (
        not is_curve
        and age_min <= SOLANA_MAX_AGE_MIN
        and liq_usd >= 7500.0
        and vol5_usd >= 5000.0
        and m5 >= 5.0
        and m1 >= -1.5
        and h1 >= -12.0
        and buys5 >= max(30, int(sells5 * 1.20))
    )
    is_mature_breakout = (
        _is_established_brk
        or _is_sharp_mid_breakout
        or _is_clean_grad_ignition
        or (_is_high_conviction_accum and age_min <= SOLANA_MAX_AGE_MIN and m5 >= 3.0)
        or (
            age_min <= _mature_max_age
            and (fdv_usd <= 0.0 or fdv_usd <= float(globals().get("JEV_MAX_MCAP_USD", 1500000.0)))
            and m5 >= _mature_min_m5
            and m1 >= -2.0
            and h1 >= -15.0
            and buys5 >= max(40, int(sells5 * 1.20))
            and vol5_usd >= _mature_min_vol5
            and (vel_ratio >= 0.25 or vol5_usd >= 12000.0)
            and liq_usd >= 6000.0
        )
    )

    # 1.2b Пробуждение сырой кривой Pump.fun (25м..45м, кейс SHERRICK +42.7%: 40b/15s, vol5 $4.5k, m1 +5%):
    is_curve_wakeup = (
        is_curve
        and age_min <= 45.0
        and m5 >= 20.0
        and m1 >= 0.0
        and buys5 >= max(25, int(sells5 * 1.80))
        and vol5_usd >= 3000.0
    )

    if not (
        is_ultra_fresh_launch
        or is_fresh_ignition
        or is_mature_breakout
        or is_curve_wakeup
        or _is_bullish_pullback_dip
        or _is_strong_v_reversal
    ):
        return (
            False,
            f"no-rocket-ignition age {age_min:.1f}m m5 {m5:+.1f}% m1 {m1:+.1f}% h1 {h1:+.1f}% "
            f"b/s {buys5}/{sells5} vol5 ${vol5_usd:,.0f} v/l {vel_ratio:.2f}x",
        )

    if h1 < min_h1 and not _is_strong_v_reversal:
        return False, f"hourly-downtrend h1 {h1:+.1f}% < {min_h1:+.1f}% (choppy downtrend)"

    if age_min >= 45.0 and h6 is not None and float(h6 or 0.0) < min_h6:
        return False, f"h6-downtrend {float(h6 or 0.0):+.1f}% < {min_h6:+.1f}% (coin is bleeding)"

    if _rh_cabal_flagged:
        return True, "ok-rug-hunter-cabal"

    return True, "ok"

STREAK_COOLDOWN_ENABLED = False  # Отключено: на $2 ставках серия мелких скретчей не должна замораживать торговлю ракетами на 15-30 мин!
STREAK_MAX_CONSECUTIVE_LOSSES = 5
STREAK_COOLDOWN_MINUTES = 3.0

ROLLING_BREAKER_ENABLED = False
ROLLING_BREAKER_WINDOW = 8
ROLLING_BREAKER_LOSS_USD = 8.0
ROLLING_BREAKER_PAUSE_MIN = 5.0
DD_THROTTLE_ENABLED = False
DD_THROTTLE_START_USD = 15.0

# === ВХОД В ЗРЕЛЫЕ ВЫПУСКНИКИ RAYDIUM / PUMPSWAP (БЕЗ СЫРЫХ КРИВЫХ PUMP.FUN) ===
# По статистике shadow_trades.db (1785 токенов) и живым сделкам 19.11.2026:
# сырая кривая давала 0 побед / 7 убытков (-340%), раг-рейт 8-14% при 0.5-0.9% ракет;
# мигрировавшие .pump на PumpSwap (liq >= $7.5k, LP сожжён протоколом) — раг-рейт 3.3% и все реальные ракеты (ANT +231%, CD, CORNER, TESLA).
SNIPER_ENTRIES_ENABLED = False            # Выключен вход в сырую кривую (WSS subscribeMigration продолжает мгновенно подавать миграции в BirthTracker!)
ALLOW_UNGRADUATED_PUMPFUN = False         # ТОЛЬКО мигрировавшие DEX-пулы (PumpSwap/Raydium с сожжённым LP) — убираем слив на кривой
MIN_PUMP_CURVE_MCAP_USD = 2750.0          # Порог кривой $2.75k (используется только если ALLOW_UNGRADUATED_PUMPFUN=True)
MAX_PUMP_CURVE_MCAP_USD = 25000.0         # Потолок сырой кривой $25k
MIN_PUMP_CURVE_MCAP_SOL = 38.0            # Нижняя граница зоны кривой
MAX_PUMP_CURVE_MCAP_SOL = 340.0           # Верхняя граница зоны кривой
CURVE_MIN_LIQ_USD = 5000.0
SHADOW_WATCH_LIMIT = 400                  # Сколько последних записей shadow_tracker перепроверяет за цикл (было 60/150 -> слежение обрывалось через ~6 мин)
PUMP_MIGRATION_MCAP_SOL = 410.0           # FDV миграции pump.fun
PUMP_DUMP_FLOOR_SOL = 50.0                # Порог post-mig-dumped (~50 SOL, разрешает быстрые реклеймы)
JEV_MAX_TICKET_USD = 0.0
MIN_GRADUATED_LIQ_USD = 7500.0            # Минимум $7.5k ликвидности на DEX (отсекает слитые микро-пулы и высокое проскальзывание на стопах)
AUDITOR_STAIRCASE_ENABLED = True          # ВКЛЮЧЕНО: Layer 9/10 детектор лесенки-ботофермы и газовой пыли (защита от slow-cook cabal рагов типа FIONA -96.3%)
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

# === СИСТЕМА ВЫХОДА: МУНБЭГ-ЛЕСЕНКА ДЛЯ ЛОВЛИ ИКСОВ (2x - 11x) ===
PARTIAL_TP_ENABLED = True
PARTIAL_TP_MIN_TICKET_USD = 0.50
TP1_TRIGGER_PCT = 0.35               # Tier 1 (+35%): фиксируем 35% позиции -> 100% Risk-Free Moonbag + возврат тела
TP1_SELL_FRAC = 0.35                 # Продаём 35%
TP2_TRIGGER_PCT = 1.10               # Tier 2 (+110% / 2.1x): фиксируем 30% позиции (иксы!)
TP2_SELL_FRAC = 0.30                 # Продаём 30%
TP3_TRIGGER_PCT = 2.50               # Tier 3 (+250% / 3.5x): фиксируем 15% позиции
TP3_SELL_FRAC = 0.15                 # Продаём 15%
TP4_TRIGGER_PCT = 5.00               # Tier 4 (+500% / 6.0x): фиксируем 10% позиции
TP4_SELL_FRAC = 0.10                 # Продаём 10%
TP5_TRIGGER_PCT = 10.00              # Tier 5 (+1000% / 11.0x): фиксируем всё остальное
TP5_SELL_FRAC = 1.00                 # 100% остатка позиции
MOONBAG_TRIGGER_PCT = 0.35           # Мунбэг активируется уже с +35% (после первого тейка)
MOONBAG_SELL_FRAC = 0.35
SLOW_COOK_PROFIT_EXIT_MIN = 12.0     # 12м таймер: работает только для застрявших во флете слабых монет (< +20%) без TP1
SLOW_COOK_HARD_EXIT_MIN = 15.0       # 15м тайм-аут

# Трейлинг для мунбэга (защищает отката: если дошло до +75%, не даёт слить в 0)
MOONBAG_TRAIL_ACTIVATION_PCT = 0.40  # Трейлинг включается при пике от +40%
MOONBAG_TRAIL_DISTANCE_PCT = 0.20    # Плотный трейл 20% от ATH (держит прибыль)
MOONBAG_MAX_HOLD_MIN = 55.0          # Даём ракетам до 55 минут на 5x-20x памп

# (STOP_LOSS_PCT, STOP_LOSS_UNLOCKED_PCT, EMERGENCY_CAP_PCT заданы единым блоком в начале файла)

PUMP_XGB_ENABLED = False
RAY_XGB_ENABLED = False
ROCKET_XGB_ENABLED = True
ROCKET_XGB_STAGE1_THRESHOLD = 4.5    # Смягчённый порог 4.5% (пускает ракеты JOLLYGUY 5.8%, HOGE 5.2%, MEMSYNC 5.9%, DOOM 4.9%; все 8 слоёв ончейн-аудита остаются на 100%)
ROCKET_XGB_FINAL_THRESHOLD = 8.0      # Финальный порог 8% после прохождения всех ончейн-аудитов безопасности
ROCKET_XGB_THRESHOLD = 10.0
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

BREAKEVEN_PCT = 0.25       # Breakeven Shield: пик >= +25% -> пол +3% (перекрывает комиссии и гарантирует выход без минуса до рага)


def get_sol_tp_ladder(is_unlocked: bool) -> tuple:
    """Return (tp1_t, tp1_f, tp2_t, tp2_f, tp3_t, tp3_f, tp4_t, tp4_f, tp5_t, tp5_f).
    For UNLOCKED pools (high LP rug risk):
      TP1 +35% (sell 50% -> principal 100% safe)
      TP2 +80% / ~2x (sell 100% -> FULL EXIT before dev removes LP!)
    For LOCKED / CURVE pools:
      TP1 +35% (sell 35% -> Risk-Free Moonbag + deposit protection)
      TP2 +110% (sell 30% -> 2.1x multiplier)
      TP3 +250% (sell 15% -> 3.5x multiplier)
      TP4 +500% (sell 10% -> 6.0x multiplier)
      TP5 +1000% (sell 100% of remaining -> 11.0x jackpot)
    """
    if is_unlocked:
        return (
            float(globals().get("RUG_HUNTER_UNLOCKED_TP1_PCT", 0.35)),
            float(globals().get("RUG_HUNTER_UNLOCKED_TP1_SELL_FRAC", 0.40)),
            float(globals().get("RUG_HUNTER_UNLOCKED_TP2_PCT", 0.90)),
            float(globals().get("RUG_HUNTER_UNLOCKED_TP2_SELL_FRAC", 0.30)),
            float(globals().get("RUG_HUNTER_UNLOCKED_TP3_PCT", 2.50)),
            float(globals().get("RUG_HUNTER_UNLOCKED_TP3_SELL_FRAC", 0.15)),
            float(globals().get("RUG_HUNTER_UNLOCKED_TP4_PCT", 5.00)),
            float(globals().get("RUG_HUNTER_UNLOCKED_TP4_SELL_FRAC", 0.10)),
            float(globals().get("RUG_HUNTER_UNLOCKED_TP5_PCT", 10.00)),
            float(globals().get("RUG_HUNTER_UNLOCKED_TP5_SELL_FRAC", 1.00)),
        )
    return (
        float(globals().get("TP1_TRIGGER_PCT", 0.35)),
        float(globals().get("TP1_SELL_FRAC", 0.35)),
        float(globals().get("TP2_TRIGGER_PCT", 1.10)),
        float(globals().get("TP2_SELL_FRAC", 0.30)),
        float(globals().get("TP3_TRIGGER_PCT", 2.50)),
        float(globals().get("TP3_SELL_FRAC", 0.15)),
        float(globals().get("TP4_TRIGGER_PCT", 5.00)),
        float(globals().get("TP4_SELL_FRAC", 0.10)),
        float(globals().get("TP5_TRIGGER_PCT", 10.00)),
        float(globals().get("TP5_SELL_FRAC", 1.00)),
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
JEV_MAX_M1_ENTRY_PCT = 22.0

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
JEV_MAX_M1_ENTRY_PCT = 22.0       # Вход в импульс m1: разрешаем до +35% (не блокируем быстрые ракеты)
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
JEV_MAX_DD_FROM_HIGH = 0.22        # Разрешаем нормальный откат до -22% от локального пика (вместо 12%, который резал пробои PumpSwap)
JEV_FRESH_RECHECK_ENABLED = True   # Включена финальная проверка цены перед покупкой (защищает от покупки на падающей свече!)
JEV_MIN_M1_ENTRY_FLOOR_PCT = -2.5   # Разрешаем микро-шум 1м свечи до -2.5% (не режем -0.4% тики), но блокируем 1м слив < -2.5%
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
FCR_CANONICAL_PUMPSWAP_DURATION_SEC = 6.0  # Бюджет FCR (6.0с) для каноничного PumpSwap: с кэшированной лентой /trades успевает за 1.5-2.5с + хватает на трейс кластеров без таймаутов cohort=0
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
MIN_M1_PRICE_CHANGE_PCT = -2.5           # Разрешаем микро-откат 1м до -2.5%, но режем падающие ножи (< -2.5%)

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
FAST_STOP_LOSS_PCT = -20.0               # Жесткий стоп -20% (переживает стартовые фитили ракет, чётко режет дампы)
EARLY_MOMENTUM_CUT_SEC = 60.0            # Проверка импульса через 60 сек (не выбивает VICE на 11-й секунде!)
EARLY_MOMENTUM_MIN_PEAK_PCT = 3.0        # Если за 60-150 сек пик был меньше +3%
EARLY_MOMENTUM_MAX_LOSS_PCT = -12.0      # Срез при подтверждённом уходе <= -12% без импульса

# ============================================================================
# 🎯 СТРАТЕГИЯ «ОХОТНИК НА РАГИ» (RUG-HUNTER) — ОТДЕЛЬНАЯ МАРКИРОВКА И РИСК-БЮДЖЕТ
# ============================================================================
# Все сделки этой стратегии явно подписываются в source как «🎯 RUG-HUNTER:UNLOCKED-LP»,
# «🎯 RUG-HUNTER:SEEDED-LP» или «🎯 RUG-HUNTER:CABAL-SURF» — чтобы отдельно оценивать PnL и Winrate!
RUG_HUNTER_ENABLED = True                 # Включить стратегию охоты на ранние фазы незалоченных пулов и кабалов
RUG_HUNTER_UNLOCKED_LP_ENABLED = True     # Режим A: ранний вход в незалоченные пулы (без honeypot/freeze/instant-rugger)
RUG_HUNTER_SEEDED_LP_ENABLED = True       # Режим A2: включён с защитой от фейк-пулов (buys5 >= 20, sells5 >= 3, vol5 >= $2.5k — ловит USDC +1400%, GTA, SURVIVE; отсекает фейк-ловушки вроде 愛)
RUG_HUNTER_SEEDED_SIZE_MULT = 0.40        # Доп. уменьшение размера SEEDED-LP: 40% от RH размера (итого ~26% от базового ордера). Лимит потерь при дрейне: ~$0.80 вместо $1.97
RUG_HUNTER_CABAL_SURF_ENABLED = True      # Режим B: ранний сёрф залоченных пулов с кабал/инсайдер-сеткой (<=8м)
RUG_HUNTER_MAX_SLOTS = 2                  # Макс. 2 одновременных слота под Rug-Hunter (2 слота всегда свободны под чистые ракеты)
RUG_HUNTER_SIZE_MULT = 0.65               # Уменьшенный риск-сайз (65% от обычного ордера)
RUG_HUNTER_UNLOCKED_MIN_AGE_MIN = 0.15     # Минимум 0.15м (9с): вход на самом старте разгона первой свечи
RUG_HUNTER_UNLOCKED_MAX_AGE_MIN = 3.20      # Максимум 3.20м: даём DexScreener время проиндексировать ранние ракеты (было 2.0м — 2.0м блокировало 60%+ ракет)
RUG_HUNTER_ROCKET_MAX_POOL_AGE_MIN = 12.0  # Максимальный возраст пула для летящих ракет (PnL >= +8%): даём ракете бежать до 12м, собирая TP1 (+35%), TP2 (+90%), TP3 (+250%) и трейлинг!
RUG_HUNTER_CABAL_MAX_AGE_MIN = 4.5         # Максимум 4.5м для кабал-сёрфа
RUG_HUNTER_MIN_LIQ_USD = 7500.0            # Минимум $7.5k реальной ликвидности (едино с MIN_GRADUATED_LIQ_USD)
RUG_HUNTER_MIN_VOL5_USD = 350.0            # Минимум $350 объёма за 5м на ультра-старте (ловит ракеты на 15-30 секундах, кейсы NICKEH30, USELESS, SHELF)
RUG_HUNTER_EARLY_AGE_THRESHOLD = 2.2       # Порог ультра-ранней фазы (до 2.2м)
RUG_HUNTER_EARLY_MIN_SELLS = 1             # Достаточно 1 продажи для подтверждения sellable на старте (на 0.5м допускается 0 при сильных покупках)
RUG_HUNTER_EARLY_MIN_BUYS = 6              # Достаточно 6 покупок на ультра-старте (вместо 25), если есть перевес покупателей
RUG_HUNTER_STAGE1_MIN_ML = 3.5             # Смягчённый порог Stage-1 ML для ранних ракет (unblock кейса DOOM +940% conf=4.9%)
RUG_HUNTER_SEEDED_MIN_ML = 30.0            # Смягчённый порог ML для SEEDED-LP: 30% (пропускает ракеты HENKI 36%, ELVES 34%, DOOM 49%, XMAS 71%, JEE 69%; защита от китов через rh-seeded-whale-trap)
RUG_HUNTER_SEEDED_MIN_BUYS = 15            # Реальные покупатели для посеянных пулов (15b)
RUG_HUNTER_SEEDED_MIN_SELLS = 1            # Достаточно 1 продажи для подтверждения ликвидности
RUG_HUNTER_SEEDED_MIN_VOL5 = 1500.0        # Органический объём для посеянных пулов ($1,500)
RUG_HUNTER_MAX_FDV_USD = 500000.0          # Потолок FDV $500k (пропускает ROCKSTAR $487k)
RUG_HUNTER_CABAL_MAX_FDV_USD = 180000.0   # Потолок FDV для кабал-сёрфа
RUG_HUNTER_TP1_PCT = 0.35                 # TP1 +35%: фиксируем 35% объема (возврат тела депозита, house money!)
RUG_HUNTER_TP1_SELL_FRAC = 0.35
RUG_HUNTER_TP2_PCT = 1.10                 # TP2 +110% (2.1x): фиксируем 30% объема (сбор первых иксов!)
RUG_HUNTER_TP2_SELL_FRAC = 0.30
RUG_HUNTER_TP3_PCT = 2.50                 # TP3 +250% (3.5x): фиксируем 15% объема
RUG_HUNTER_TP3_SELL_FRAC = 0.15
RUG_HUNTER_TP4_PCT = 5.00                 # TP4 +500% (6.0x): фиксируем 10% объема
RUG_HUNTER_TP4_SELL_FRAC = 0.10
RUG_HUNTER_TP5_PCT = 10.00                # TP5 +1000% (11x): полный джекпот!
RUG_HUNTER_TP5_SELL_FRAC = 1.00
RUG_HUNTER_BREAKEVEN_PCT = 0.25           # Включаем Breakeven только после пика >= +25%
RUG_HUNTER_BREAKEVEN_FLOOR = 0.05         # Пол +5% (не закрываемся в микро-плюс из-за шума)
RUG_HUNTER_TRAIL_ACT_PCT = 0.35           # Трейлинг-стоп активируется после +35% (не душим на старте!)
RUG_HUNTER_TRAIL_DROP_PCT = 0.22          # Трейл -22% от ATH (даёт ракете дышать и собирать 2x-10x)
RUG_HUNTER_SL_PCT = -0.10                 # Жёсткий Stop-Loss -10%
RUG_HUNTER_EMERG_PCT = -0.14              # Экстренный кап -14%
RUG_HUNTER_MAX_HOLD_MIN = 5.0             # Базовый тайм-кэп 5м (для плюсовых позиций расширяется до 15м)
RUG_HUNTER_LIQ_DRAIN_PCT = 0.40           # Мгновенный выход при реальном падении LP >= 40% (защита от ложных сбросов при нормальных продажах)

# === ВЫХОДЫ ДЛЯ НЕЗАЛОЧЕННЫХ ПУЛОВ RUG-HUNTER (UNLOCKED-LP) ===
# Принцип: TP1 (+35%) возвращает тело депозита (без риска), TP2 (+90%) фиксирует гарантированный профит,
# а лунная сумка (30%) летит до TP3 (+250%), TP4 (+500%), TP5 (+1000%) или трейлинг-стопа!
RUG_HUNTER_UNLOCKED_TP1_PCT = 0.35        # TP1 +35%: фиксируем 40% объема (депозит полностью в сейфе!)
RUG_HUNTER_UNLOCKED_TP1_SELL_FRAC = 0.40
RUG_HUNTER_UNLOCKED_TP2_PCT = 0.90        # TP2 +90% (2x): фиксируем 30% объема (гарантированный профит!)
RUG_HUNTER_UNLOCKED_TP2_SELL_FRAC = 0.30
RUG_HUNTER_UNLOCKED_TP3_PCT = 2.50        # TP3 +250% (3.5x): фиксируем 15% объема
RUG_HUNTER_UNLOCKED_TP3_SELL_FRAC = 0.15
RUG_HUNTER_UNLOCKED_TP4_PCT = 5.00        # TP4 +500% (6x): фиксируем 10% объема
RUG_HUNTER_UNLOCKED_TP4_SELL_FRAC = 0.10
RUG_HUNTER_UNLOCKED_TP5_PCT = 10.00       # TP5 +1000% (11x): 100% оставшегося объема
RUG_HUNTER_UNLOCKED_TP5_SELL_FRAC = 1.00
RUG_HUNTER_UNLOCKED_MAX_HOLD_MIN = 12.0   # Потолок удержания для растущих монет (до 12м для ракет)
RUG_HUNTER_UNLOCKED_SAFETY_MIN = 4.0      # Лимит 4.0м удержания только для нулевых/минусовых позиций
RUG_HUNTER_UNLOCKED_MAX_POOL_AGE_MIN = 4.5 # Потолок возраста пула (4.5м) для слабых позиций (PnL < +8%)
RUG_HUNTER_ROCKET_MAX_POOL_AGE_MIN = 12.0 # Расширенный потолок (12.0м) для растущих ракет (PnL >= +8%): даёт собрать 3x-10x!
RUG_HUNTER_UNLOCKED_STALL_SEC = 60.0      # 60с без нового ATH после пика >= +30% и откат 18% -> эвакуация
RUG_HUNTER_UNLOCKED_TRAIL_DROP_PCT = 0.22 # Трейл 22% от ATH для незалоченных пулов (даёт дышать на откатах)
