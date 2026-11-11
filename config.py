import os
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
STOP_LOSS_PCT = -0.180        # Стоп -18.0% (дает дышать волатильности Solana и не выбивает ракеты вроде CULT -12.9%)
EMERGENCY_CAP_PCT = -0.250     # Аварийный потолок -25.0% (мгновенный срез реального глубокого дампа)
INFANT_WINDOW_MIN = 0.5
INFANT_DUMP_PCT = -0.180       # Срез рага на 1-й минуте при провале <= -18.0%

# === ФИЛЬТРЫ ВХОДА (РАЗРЕШАЕМ ВХОД В БЫСТРЫЕ РАКЕТЫ) ===
MAX_ENTRY_H1_PCT = 1500.0       # Разрешаем сильные пампы до +1500% за 1ч (не срезаем взлетающие ракеты!)
MAX_ENTRY_M5_PCT = 120.0        # Потолок m5: до +120% (ловим взлетающие ракеты, а не только стоячие монеты!)
MAX_ENTRY_M1_PCT = 28.0         # Вход в импульс: до +28.0% (вместо 12%)
REQUIRE_SOCIALS_MIN_AGE_MIN = 8.0

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
SNIPER_ENTRIES_ENABLED = False
ALLOW_UNGRADUATED_PUMPFUN = False         # ОТКЛЮЧЕНО: запрет сырых bonding curves (сливы $KCAT -58%, REVERSE -34%). Ждём миграции на DEX с сожжённым LP!
MIN_PUMP_CURVE_MCAP_USD = 18000.0         # Fallback в USD (если sol_price недоступен)
MAX_PUMP_CURVE_MCAP_USD = 44000.0         # Fallback в USD
MIN_PUMP_CURVE_MCAP_SOL = 80.0            # Порог кривой pump.fun в SOL (~$6.6k при SOL $83, ~$12k при SOL $150)
MAX_PUMP_CURVE_MCAP_SOL = 350.0           # Верх кривой перед миграцией (~350 SOL)
PUMP_MIGRATION_MCAP_SOL = 410.0           # FDV миграции pump.fun (~410 SOL = ~$34k при SOL $83, ~$61.5k при SOL $150)
PUMP_DUMP_FLOOR_SOL = 160.0               # Порог post-mig-dumped (~40% миграции = 160 SOL, разрешает Dip Reclaim)
JEV_MAX_TICKET_USD = 0.0
MIN_GRADUATED_LIQ_USD = 5000.0           # На DEX минимум $5k реальной ликвидности (пускает свежие миграции PumpSwap и Raydium $5k+, не режет ранние ракеты)
AUDITOR_STAIRCASE_ENABLED = True
AUDITOR_MAX_DEV_PCT = 6.0
AUDITOR_MAX_TOP10_PCT = 32.0
STREAM_WINDOW_SEC = 60
STREAM_MIN_UNIQUE_BUYERS = 6
STREAM_MIN_BUY_SOL = 1.5
STREAM_MAX_TOP_BUYER_RATIO = 0.50
STREAM_MIN_BS_RATIO = 1.6
STREAM_MAX_CURVE_PROGRESS = 85.0
STREAM_ENTRY_SIZE_USD = 5.0
STREAM_SLIPPAGE = 0.08
STREAM_MAX_AGE_SEC = 900
STREAM_GC_MIN_PROGRESS = 4.0
STREAM_GC_IDLE_SEC = 180
STREAM_DEV_DUMP_RATIO = 0.25
STREAM_MAX_DEV_BUY_TOKENS = 100_000_000
STREAM_MIN_ENTRY_PROGRESS = 15.0
STREAM_MOMENTUM_MIN_RATIO = 0.35

VIP_ENTRIES_ENABLED = True

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

# === ЛЕСТНИЦА ТЕЙК-ПРОФИТОВ (ЗАЩИТА ОТ РАГОВ + ПРОСТОР ДЛЯ РАКЕТ) ===
PARTIAL_TP_ENABLED = True
PARTIAL_TP_MIN_TICKET_USD = 4.0      # Включено для всех $5 билетов! Забираем вложенное до того, как дев рагнет!
TP1_TRIGGER_PCT = 0.180              # На +18% фиксируем 45% позы (возврат $2.65 в кэш — пол-депозита уже спасено!)
TP1_SELL_FRAC = 0.45
TP2_TRIGGER_PCT = 0.550              # На +55% фиксируем 35% от остатка (возврат еще $1.85 — сделка 100% БЕЗРИСКОВАЯ!)
TP2_SELL_FRAC = 0.35
TP3_TRIGGER_PCT = 1.60               # На +160% фиксируем 35% от остатка
TP3_SELL_FRAC = 0.35
TP4_TRIGGER_PCT = 3.50               # На +350% фиксируем 40% от остатка
TP4_SELL_FRAC = 0.40
TP5_TRIGGER_PCT = 8.00               # На +800% (9x) фиксируем 50%
TP5_SELL_FRAC = 0.50
MOONBAG_TRIGGER_PCT = 999.0
MOONBAG_SELL_FRAC = 0.0
# === МУНБЭГ-ТРЕЙЛИНГ (остаток после TP1 едет до +200%) ===
# После TP1 (+18%, продано 45%) сделка уже в плюсе. Остаток НЕ режется трейлингом на мелких откатах:
# держим до пика +200%, и только оттуда включается трейлинг. Ниже — лишь страховочные полы.
MOONBAG_TRAIL_ACTIVATION_PCT = 2.00  # Трейлинг мунбэга включается только от пика +200%
MOONBAG_TRAIL_DISTANCE_PCT = 0.25    # Дистанция трейлинга 25% от пика (на +200% выход не ниже ~+125%)
MOONBAG_MAX_HOLD_MIN = 90.0          # Мунбэг может ехать до 90 минут (вместо 45)

PUMP_XGB_ENABLED = False
RAY_XGB_ENABLED = False
ROCKET_XGB_ENABLED = True
ROCKET_XGB_STAGE1_THRESHOLD = 18.0   # Первичный отсев в 0мс (отсекает шлак с <18%)
ROCKET_XGB_FINAL_THRESHOLD = 30.0    # Финальный порог 30%: отсекает вялые BRO (25%), пускает реальные ракеты 30%+
ROCKET_XGB_THRESHOLD = 30.0

STALL_TIMER_SEC = 240.0   # 4 минуты на консолидацию стоячего минуса
STALL_MAX_LOSS_PCT = -0.090

TRAILING_ACTIVATION_PCT = 0.500   # Трейлинг включается только от +50% пика
TRAILING_DISTANCE_PCT = 0.250     # Широкая дистанция трейлинга 25% (не режет волатильные ракеты)
TRAILING_RUNNER_DISTANCE_PCT = 0.22
SCALP_PROFIT_ENABLED = False

WHALE_DUMP_GUARD_PCT = 0.22
EVM_RUNNER_CRASH = 0.25
EVM_NORMAL_CRASH = 0.22

BREAKEVEN_PCT = 999.0      # Breakeven Shield ОТКЛЮЧЕН (убивал ракеты копеечными выходами)

PREFETCH_TTL_SEC = 35
SOLANA_MIN_AGE_MIN = 0.25       # Разрешаем молодые ракеты от 15 секунд (TWAP, LEMON, SHADOW)
SOLANA_MAX_AGE_HOURS = 6.0       # До 6 часов (не срезаем развивающиеся ракеты на пике тренда)
SOLANA_MAX_AGE_MIN = 360.0       # Торгуем активные монеты до 6 часов
JEV_MAX_AGE_MIN = 360.0          # В GROK-JEV активные монеты до 6 часов
JEV_MAX_M1_ENTRY_PCT = 25.0

SCALP_TIMEOUT_MIN = 10.0
STAGNANT_LOSS_MIN = 9.0
STAGNANT_LOSS_MIN_FAST = 7.0
STAGNANT_HOLD_MIN = 12.0

AI_MODE = "degen"
MIN_LIQUIDITY = 8000.0          # Минимум $8,000 реальной ликвидности на DEX (пускает MELT с $9,981)
MAX_LIQUIDITY = 50000000

GROQ_API_KEY = (os.getenv("GROQ_API_KEY") or os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY") or "").strip().strip('"').strip("'")
XAI_API_KEY = (os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY") or "").strip().strip('"').strip("'")
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()

# === GROK BOT x JEV ENGINE ===
JEV_ENABLED = True
JEV_WICK_STOP_ENABLED = False
JEV_EARLY_MIN_AGE_MIN = 0.25    # Сверхбыстрый вход в горячие ракеты с объемом от 15 секунд
JEV_MIN_AGE_MIN = 0.35          # Базовый карантин от 20 секунд (не срезаем TWAP 0.4m)
JEV_MAX_AGE_MIN = 60.0          # Свежие монеты до 60м (защита от зомби-монет GIGAHUG/INVEST)
JEV_MIN_LIQUIDITY_USD = 8000.0  # Минимум $8k ликвидности (пускает MELT $9,981)
JEV_MAX_LIQUIDITY_USD = 220000.0
JEV_MIN_VOLUME_H1 = 8000.0
JEV_MIN_MCAP_USD = 5000.0      # Минимум $5k MCap (ловит ранние Raydium/PumpSwap ракеты вроде FOMO $8.1k, ICC $8.1k)
JEV_MAX_MCAP_USD = 1800000.0    # Верхняя граница $1.8M MCap (ловим мем-ракеты $48k-$1.8M, а не стоячие mid-caps)
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
MIN_VOLUME_M5 = 5000            # Минимум $5,000 за 5м (отсекает вялые стоячие монеты вроде BRO, пускает живые ракеты)
MIN_TXNS_M5 = 25
MIN_BUY_SELL_RATIO = 1.08       # Покупателей больше продавцов (не душит активные ракеты вроде DUGE 776b/720s)
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
SCAN_LOOP_SLEEP_SEC = 1
SCAN_AFTER_ENTRY_SLEEP_SEC = 3
SCAN_LATEST_TTL_SEC = 10
SOLANA_RESCAN_COOLDOWN = 30
SOLANA_FRESH_RESCAN_COOLDOWN = 8

JEV_TREND_CONFIRM_ENABLED = False
JEV_FRESH_RECHECK_ENABLED = True   # Включена финальная проверка цены перед покупкой (защищает от покупки на падающей свече!)
JEV_MIN_M1_ENTRY_FLOOR_PCT = -4.0
JEV_MIN_H1_PCT = -8.0              # Разрешаем плоскую консолидацию и разворот от -8% (не режет MEME +3% и GORP +3.8%)
JEV_MIN_H6_PCT = -15.0

USE_COPYTRADE = True
COPY_MIN_TIER = 3
COPY_MAX_TICKET_USD = 5.0
COPY_MAX_CONCURRENT_POSITIONS = 3  # До 3 слотов под копитрейд/VIP
COPY_USE_ANTISCAM = True
COPY_MAX_ACTIVE_WHALES = 80
COPY_BAN_ON_FIRST_LOSS = False  # Не банить проверенных китов и KOL на первом же стопе!
COPY_PREFILTER = True
COPY_MAX_TX_PER_MIN = 40
COPY_MIN_PNL = 250.0
