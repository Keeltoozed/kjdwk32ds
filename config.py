import os
from dotenv import load_dotenv

load_dotenv()

# Dexscreener API (Только топ-бусты и профили, без мусорных 1-минутных скамов)
DEXSCREENER_LATEST = "https://api.dexscreener.com/token-boosts/latest/v1" 
DEXSCREENER_PROFILES = "https://api.dexscreener.com/token-profiles/latest/v1" 
DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/tokens/"

# Helius / PumpPortal WSS API
HELIUS_API_KEY = (os.getenv("HELIUS_API_KEY") or "").strip().strip('"').strip("'")
HELIUS_RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}" if HELIUS_API_KEY else "https://api.mainnet-beta.solana.com"
PUMPPORTAL_API_KEY = (os.getenv("PUMPPORTAL_API_KEY") or "").strip().strip('"').strip("'")
PUMPPORTAL_WSS = f"wss://pumpportal.fun/api/data?api-key={PUMPPORTAL_API_KEY}" if PUMPPORTAL_API_KEY else "wss://pumpportal.fun/api/data"

# RugCheck API
RUGCHECK_API = "https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"

# Supabase DB Config
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

# Paper Trading Config
PAPER_PORTFOLIO_FILE = "portfolio.json"
PORTFOLIO_SNAPSHOT_MINT = (os.getenv("PORTFOLIO_SNAPSHOT_MINT", "") or "").strip().strip('"').strip("'") or "PORTFOLIO_SOLANA_V2"
INITIAL_BALANCE_USD = 20.0
REINVEST_PERCENT = 22.0
VIRTUAL_POSITION_SIZE_USD = 4.40
TRADE_AMOUNT_USD = 4.40

# === ЭКСПОНЕНЦИАЛЬНЫЙ РАЗГОН ДЕПОЗИТА ===
COMPOUNDING_ENABLED = True
COMPOUNDING_PCT = 0.10
MAX_COMPOUNDING_SIZE_USD = 2500.0
DYNAMIC_REINVEST_ENABLED = True
BASE_TRADE_AMOUNT_USD = 2.0
MAX_POSITION_SIZE_USD = 2500.0
REINVEST_MIN_POOL_LIQ_USD = 6000.0
SCOUT_SIZE_USD = 4.0
LOTTERY_SIZE_USD = 4.0
MAX_CONCURRENT_POSITIONS = 6

# === RISK MANAGEMENT (ОПТИМИЗИРОВАНО ПРОТИВ МЕЛКИХ МИНУСОВ) ===
MAX_DAILY_LOSS_USD = 1000.0
KILL_SWITCH_ENABLED = False
STOP_LOSS_PCT = -0.14          # Четкий стоп -14% (вместо -25%)
EMERGENCY_CAP_PCT = -0.22      # Аварийный стоп -22% (вместо -38%)
INFANT_WINDOW_MIN = 1.0
INFANT_DUMP_PCT = -0.42

# === АНТИ-ФОМО И АНТИ-РАГ ФИЛЬТРЫ ВХОДА (РАЗБЛОКИРОВАНЫ МИГРАЦИИ И ИМПУЛЬСЫ) ===
MAX_ENTRY_H1_PCT = 380.0       # Было 120.0 (блокировало все мигрировавшие с Pump.fun ракеты!)
MAX_ENTRY_M5_PCT = 95.0        # Было 45.0 (блокировало живые прорывы)
MAX_ENTRY_M1_PCT = 25.0
REQUIRE_SOCIALS_MIN_AGE_MIN = 6.0  # Было 20.0 (не блокируем свежие миграции до индексации соцсетей DS)

# === ЗАЩИТА ОТ СЕРИИ МИНУСОВ ===
STREAK_COOLDOWN_ENABLED = False    # Отключено, чтобы бот не замирал на паузах
STREAK_MAX_CONSECUTIVE_LOSSES = 5
STREAK_COOLDOWN_MINUTES = 5.0

ROLLING_BREAKER_ENABLED = False
ROLLING_BREAKER_WINDOW = 8
ROLLING_BREAKER_LOSS_USD = 8.0
ROLLING_BREAKER_PAUSE_MIN = 5.0
DD_THROTTLE_ENABLED = False
DD_THROTTLE_START_USD = 15.0

# === STREAMING SNIPER & GRADUATED DEX SHIELD ===
SNIPER_ENTRIES_ENABLED = False
ALLOW_UNGRADUATED_PUMPFUN = False
MIN_PUMP_CURVE_MCAP_USD = 12000.0
MAX_PUMP_CURVE_MCAP_USD = 50000.0
JEV_MAX_TICKET_USD = 0.0
MIN_GRADUATED_LIQ_USD = 6500.0     # Снижено с 10000 до 6500 для раннего входа после миграции
AUDITOR_STAIRCASE_ENABLED = True
AUDITOR_MAX_DEV_PCT = 7.0
AUDITOR_MAX_TOP10_PCT = 34.0
STREAM_WINDOW_SEC = 60
STREAM_MIN_UNIQUE_BUYERS = 6
STREAM_MIN_BUY_SOL = 1.5
STREAM_MAX_TOP_BUYER_RATIO = 0.50
STREAM_MIN_BS_RATIO = 1.6
STREAM_MAX_CURVE_PROGRESS = 85.0
STREAM_ENTRY_SIZE_USD = 4.0
STREAM_SLIPPAGE = 0.08
STREAM_MAX_AGE_SEC = 900
STREAM_GC_MIN_PROGRESS = 4.0
STREAM_GC_IDLE_SEC = 180
STREAM_DEV_DUMP_RATIO = 0.25
STREAM_MAX_DEV_BUY_TOKENS = 100_000_000
STREAM_MIN_ENTRY_PROGRESS = 15.0
STREAM_MOMENTUM_MIN_RATIO = 0.35

VIP_ENTRIES_ENABLED = False
TIME_EXIT_MINUTES = 45
TIME_EXIT_PROFIT_REQ = 0.01

# === TAKE-PROFIT И РАННИЙ ЗАХВАТ ПЛЮСОВ (ГЛАВНОЕ ИСПРАВЛЕНИЕ) ===
PARTIAL_TP_ENABLED = True
TP1_TRIGGER_PCT = 0.08          # Было 0.15! На +8% сразу забираем 50% позиции в плюс и ставим БУ!
TP1_SELL_FRAC = 0.50
TP2_TRIGGER_PCT = 0.18          # Было 0.35! На +18% забираем еще 25% позиции
TP2_SELL_FRAC = 0.25
TP3_TRIGGER_PCT = 0.45          # На +45% забираем еще 15%
TP3_SELL_FRAC = 0.15
TP4_TRIGGER_PCT = 1.20          # На +120% забираем 5%
TP4_SELL_FRAC = 0.05
TP5_TRIGGER_PCT = 3.50          # На +350% забираем 5%
TP5_SELL_FRAC = 0.05
MOONBAG_TRIGGER_PCT = 999.0
MOONBAG_SELL_FRAC = 0.0

PUMP_XGB_ENABLED = False
RAY_XGB_ENABLED = False
ROCKET_XGB_ENABLED = True
ROCKET_XGB_STAGE1_THRESHOLD = 5.0   # Снижено с 8.0 до 5.0 (не режем ранние ракеты)
ROCKET_XGB_FINAL_THRESHOLD = 9.0    # Снижено с 14.0 до 9.0
ROCKET_XGB_THRESHOLD = 7.0

# === ТАЙМЕРЫ УДЕРЖАНИЯ (НЕ РЕЖЕМ КОНСОЛИДАЦИЮ В МЕЛКИЙ МИНУС!) ===
STALL_TIMER_SEC = 720.0         # 12 минут (было 7м)
STALL_MAX_LOSS_PCT = -0.14

TRAILING_ACTIVATION_PCT = 0.12  # Было 0.40! Трейлинг включается уже с +12%
TRAILING_DISTANCE_PCT = 0.05    # Дистанция отката 5% на младших уровнях
TRAILING_RUNNER_DISTANCE_PCT = 0.14
SCALP_PROFIT_ENABLED = True     # ВКЛЮЧЕНО: защищает импульсы +6%..+12% от превращения в минус

WHALE_DUMP_GUARD_PCT = 0.22
EVM_RUNNER_CRASH = 0.25
EVM_NORMAL_CRASH = 0.22

# Ранний безубыток с +6% пика (было 0.14)
BREAKEVEN_PCT = 0.06

PREFETCH_TTL_SEC = 45
SOLANA_MIN_AGE_MIN = 0.8
SOLANA_MAX_AGE_HOURS = 24.0
SOLANA_MAX_AGE_MIN = 360.0      # Было 120.0 (расширено до 6 часов для живых трендов)

# Увеличено с 4-8 минут до 14-20 минут, чтобы бот не закрывал консолидации в мелкий минус!
SCALP_TIMEOUT_MIN = 14.0
STAGNANT_LOSS_MIN = 14.0
STAGNANT_LOSS_MIN_FAST = 10.0
STAGNANT_HOLD_MIN = 20.0

AI_MODE = "degen"
MIN_LIQUIDITY = 6500.0          # Было 10000.0
MAX_LIQUIDITY = 50000000

GROQ_API_KEY = (os.getenv("GROQ_API_KEY") or os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY") or "").strip().strip('"').strip("'")
XAI_API_KEY = (os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY") or "").strip().strip('"').strip("'")
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()

# === GROK BOT x JEV ENGINE ===
JEV_ENABLED = True
JEV_WICK_STOP_ENABLED = False
JEV_EARLY_MIN_AGE_MIN = 0.8
JEV_MIN_AGE_MIN = 1.2            # Было 1.5
JEV_MIN_LIQUIDITY_USD = 6500.0   # Было 10000.0
JEV_MIN_VOLUME_H1 = 5000.0
JEV_MIN_MCAP_USD = 6500.0
JEV_MIN_VOLUME_H24 = 15000.0
JEV_MIN_TRADES_H24 = 60
JEV_MIN_TRADES_H1 = 20
JEV_MIN_HOLDERS = 50
JEV_MAX_TOP_WALLET = 0.18
JEV_MAX_TOP_10 = 0.34
MAX_POOL_LIQ_FRAC = 0.02
AUDITOR_MAX_DELEGATED_ACCOUNTS = 3
AUDITOR_MAX_DELEGATED_PCT = 5.0
AUDITOR_MAX_BUNDLE_WALLETS = 5
AUDITOR_MAX_BUNDLE_PCT = 12.0
JEV_LLM_ENFORCE = False          # False = ИИ работает в режиме советника и не блокирует живые прорывы при лимитах Groq

JEV_MIN_ACCOUNT_IS_PROJECT = 0.65
JEV_MAX_RECYCLED_ACCOUNT = 0.35
JEV_MAX_CONCENTRATION_RISK = 0.45
JEV_MAX_MOMENTUM_SPENT = 0.70
JEV_MIN_LIQUIDITY_FITS_TICKET = 0.60
JEV_MAX_DEV_STILL_LOADED = 0.35

JEV_BID_THE_FLUSH_ENABLED = True
JEV_STOP_UNDER_WICK_ENABLED = True
JEV_MAX_M1_ENTRY_PCT = 22.0      # Было 12.0 (разрешаем вход в живое движение)
JEV_DEFAULT_WICK_STOP_PCT = -0.14

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
CONVICTION_MAX_M5_PCT = 0.65
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
GROWTH_SIZE_USD = 4.0
GROWTH_INTERVAL = 75

SNIPER_MIN_UNIQUE_BUYERS = 8
SNIPER_BUYERS_WINDOW_MIN = 5
VIP_MAX_M5_PCT = 0.80
PULLBACK_MIN_M5_PCT = 0.018     # Снижено с 2.5% до 1.8%
PULLBACK_M1_MIN_PCT = -0.10
PULLBACK_M1_MAX_PCT = 0.22      # Было 0.12 (не режем активные свечи)
PULLBACK_MAX_H1_PCT = 380.0     # Было 85.0
PULLBACK_MAX_M5_PCT = 0.95      # Было 0.45
MIN_VOLUME_M5 = 1000            # Снижено до $1,000
MIN_TXNS_M5 = 8
MIN_BUY_SELL_RATIO = 1.02
LOTTERY_ENABLED = False
LOTTERY_MIN_M5_PCT = 0.60
LOTTERY_SIZE_MULT = 0.30
WHALE_CONSENSUS = 1
WHALE_MAX_RUNUP = 1.45

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
TG_MAX_SIZE_USD = 4.0
TG_VIP_SIZE_USD = 4.0
ROB_MAX_SIZE_USD = 10.0
BSC_MAX_SIZE_USD = 10.0
BSC_WEAK_SIZE_USD = 4.0

EVM_TRACK_INTERVAL = 2.5
EVM_IDLE_TRACK_INTERVAL = 6.0

COINGECKO_ENABLED = True

SCOUT_ENABLED = True
SCOUT_INTERVAL = 20
SCOUT_MIN_AGE_SEC = 60
SCOUT_MAX_AGE_SEC = 720
SCOUT_MIN_BUYERS = 5
SCOUT_MIN_TRADES = 12
SCOUT_MIN_LIQ = 10000
SCOUT_MAX_POS = 3
SCOUT_RECOOLDOWN = 180
SCOUT_BIRTH_FALLBACK = False

GOPLUS_ENABLED = True
GOPLUS_MAX_SELL_TAX = 0.10
GOPLUS_CACHE_SEC = 3600

SCAN_CONCURRENCY = 8
SCAN_LOOP_SLEEP_SEC = 2
SCAN_AFTER_ENTRY_SLEEP_SEC = 3
SCAN_LATEST_TTL_SEC = 12
SOLANA_RESCAN_COOLDOWN = 45       # Было 90с
SOLANA_FRESH_RESCAN_COOLDOWN = 12 # Было 15с

JEV_TREND_CONFIRM_ENABLED = False
JEV_FRESH_RECHECK_ENABLED = False # Отключено: ложно сбрасывало сделки при секундном колебании цены во время аудита
JEV_MIN_M1_ENTRY_FLOOR_PCT = -6.0
JEV_MIN_H1_PCT = -12.0
JEV_MIN_H6_PCT = -25.0

USE_COPYTRADE = True
COPY_MIN_TIER = 3
COPY_MAX_TICKET_USD = 4.0
COPY_USE_ANTISCAM = True
COPY_MAX_ACTIVE_WHALES = 80
COPY_BAN_ON_FIRST_LOSS = False    # Отключен пермабан кита за первый же флет/микро-минус
COPY_PREFILTER = True
COPY_MAX_TX_PER_MIN = 40
COPY_MIN_PNL = 250.0

