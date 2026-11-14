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
STOP_LOSS_PCT = -0.130        # Базовый стоп -13.0% (дает запас для спреда DEX 4-8%, не выбивает ракеты на старте)
EMERGENCY_CAP_PCT = -0.180     # Аварийный потолок -18.0% (жесткий барьер от дампа, макс loss -$0.90 на $5)
INFANT_WINDOW_MIN = 0.5
INFANT_DUMP_PCT = -0.180       # Срез рага на 1-й минуте только при глубоком краше <= -18.0% (не режет спред)

# === ЗАЩИТА ОТ МГНОВЕННОГО ВЫВОДА ЛИКВИДНОСТИ (девы выдергивают пул в 0) ===
LIQ_DRAIN_EXIT_PCT = 0.40      # Выход если ликвидность упала на 40% от пика (режем до полного обнуления)
LIQ_DRAIN_FAST_PCT = 0.30      # Если за один тик (<2с) ликва просела на 30% — мгновенный паник-сейл
LIQ_MIN_STABLE_PCT = 0.80      # Перед входом: ликва не должна просесть более чем на 20% за последние 10 мин
DEV_MAX_TOKEN_PCT = 0.10       # Дев не должен держать более 10% от供给 (иначе риск дампа)
LIQ_LOCKED_BURNED_MIN_PCT = 0.50  # Минимум 50% ликвидности залокировано или сожжено (LP токены дева)

# === ФИЛЬТРЫ ВХОДА (РАЗРЕШАЕМ ВХОД В БЫСТРЫЕ РАКЕТЫ) ===
MAX_ENTRY_H1_PCT = 1500.0       # Разрешаем сильные пампы до +1500% за 1ч (не срезаем взлетающие ракеты!)
MAX_ENTRY_M5_PCT = 120.0        # Потолок m5: до +120% (ловим взлетающие ракеты, а не только стоячие монеты!)
MAX_ENTRY_M1_PCT = 28.0         # Вход в импульс: до +28.0% (вместо 12%)
REQUIRE_SOCIALS_MIN_AGE_MIN = 25.0  # Требуем соцсети ТОЛЬКО после 25 минут (на старте DexScreener не успевает индексировать)

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
PUMP_DUMP_FLOOR_SOL = 160.0               # Порог post-mig-dumped (~40% миграции = 160 SOL, разрешает Dip Reclaim)
JEV_MAX_TICKET_USD = 0.0
MIN_GRADUATED_LIQ_USD = 10000.0          # На DEX минимум $10k реальной ликвидности — пускает свежие миграции $11k-$18k (AI, AMD, HOUSE, SCAT, BELIEVE) и отсекает микро-пулы <$10k
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

# === ЛЕСТНИЦА ТЕЙК-ПРОФИТОВ (МУНБЭГ СТРОГО ОТ +110%) ===
PARTIAL_TP_ENABLED = True
PARTIAL_TP_MIN_TICKET_USD = 4.0      # Включено для всех $5 билетов!
TP1_TRIGGER_PCT = 1.10               # ТЕЙК-ПРОФИТ 1 И МУНБЭГ: ТОЛЬКО НА +110% (2.1x)! До +110% позиция едет целиком!
TP1_SELL_FRAC = 0.50                 # На +110% фиксируем 50% ($5.25 кэша) — тело $5 уже возвращено с прибылью!
TP2_TRIGGER_PCT = 2.50               # На +250% (3.5x) фиксируем 35% от остатка
TP2_SELL_FRAC = 0.35
TP3_TRIGGER_PCT = 5.00               # На +500% (6x) фиксируем 40% от остатка
TP3_SELL_FRAC = 0.40
TP4_TRIGGER_PCT = 10.00              # На +1000% (11x) фиксируем 50% от остатка
TP4_SELL_FRAC = 0.50
TP5_TRIGGER_PCT = 20.00              # На +2000% (21x) закрываем остаток
TP5_SELL_FRAC = 1.00
MOONBAG_TRIGGER_PCT = 1.10           # Статус Moonbag начинается СТРОГО от +110%
MOONBAG_SELL_FRAC = 0.0
# === МУНБЭГ-ТРЕЙЛИНГ (остаток после TP1 +110% едет до 10x-20x) ===
MOONBAG_TRAIL_ACTIVATION_PCT = 1.10  # Трейлинг мунбэга включается только от пика +110%
MOONBAG_TRAIL_DISTANCE_PCT = 0.25    # Дистанция трейлинга 25% от пика (на +110% выход не ниже ~+66%)
MOONBAG_MAX_HOLD_MIN = 90.0          # Мунбэг может ехать до 90 минут (вместо 45)

PUMP_XGB_ENABLED = False
RAY_XGB_ENABLED = False
ROCKET_XGB_ENABLED = True
ROCKET_XGB_STAGE1_THRESHOLD = 10.0   # Первичный отсев 10%: пускает BELIEVE (+555%), FLC (+736%), REVIVE (+264%)
ROCKET_XGB_FINAL_THRESHOLD = 14.0    # Финальный порог 14%: отсекает откровенный мусор, но ловит ракеты (GTA 19%, SWEATER)
ROCKET_XGB_THRESHOLD = 14.0

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
SOLANA_MIN_AGE_MIN = 1.0        # 60 секунд: созревание после блока 0 (свободный вход в ракеты со 2-й свечи)
SOLANA_MAX_AGE_HOURS = 12.0      # До 12 часов (не срезаем развивающиеся ракеты вроде ASTRALIS/Sakura)
SOLANA_MAX_AGE_MIN = 720.0       # Торгуем активные монеты до 12 часов
JEV_MAX_AGE_MIN = 720.0          # В GROK-JEV активные монеты до 12 часов
JEV_MAX_M1_ENTRY_PCT = 25.0

SCALP_TIMEOUT_MIN = 10.0
STAGNANT_LOSS_MIN = 9.0
STAGNANT_LOSS_MIN_FAST = 7.0
STAGNANT_HOLD_MIN = 12.0

AI_MODE = "degen"
MIN_LIQUIDITY = 10000.0         # Минимум $10,000 ликвидности — пускает ранние ракеты $11k-$18k и отсекает микро-пулы
MAX_LIQUIDITY = 50000000

GROQ_API_KEY = (os.getenv("GROQ_API_KEY") or os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY") or "").strip().strip('"').strip("'")
XAI_API_KEY = (os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY") or "").strip().strip('"').strip("'")
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()

# === GROK BOT x JEV ENGINE ===
JEV_ENABLED = True
JEV_WICK_STOP_ENABLED = False
JEV_EARLY_MIN_AGE_MIN = 1.0    # Ранний вход от 60 секунд (не пропускаем ракеты!)
JEV_MIN_AGE_MIN = 1.2          # Базовый карантин 72 секунды
JEV_MAX_AGE_MIN = 720.0          # В GROK-JEV активные монеты до 12 часов (не срезаем зрелые ракеты вроде ASTRALIS/Sakura)
JEV_MIN_LIQUIDITY_USD = 10000.0  # Минимум $10k ликвидности — пускает ранние ракеты (AI, AMD, HOUSE, SCAT)
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
JEV_MIN_M1_ENTRY_FLOOR_PCT = -4.0
JEV_MIN_H1_PCT = -8.0              # Разрешаем плоскую консолидацию и разворот от -8% (не режет MEME +3% и GORP +3.8%)
JEV_MIN_H6_PCT = -15.0

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
FCR_BLOCK_SCORE = 0.55                 # score >= этого значения -> вход блокируется
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
FCR_MIN_AGE_MIN = 1.5                  # Моложе 1.5м — зона 1-минутного бандл-рага дева
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
