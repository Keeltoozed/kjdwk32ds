import os
from dotenv import load_dotenv

load_dotenv()

# Dexscreener API (Оставляем для фоновых проверок портфеля, если нужно)
DEXSCREENER_LATEST = "https://api.dexscreener.com/token-boosts/top/v1" 
DEXSCREENER_PROFILES = "https://api.dexscreener.com/token-profiles/latest/v1" 
DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/tokens/"

# Helius / PumpPortal WSS API
HELIUS_API_KEY = "9efda6f4-fddb-42d3-a2b1-098bbbecd299"
HELIUS_RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
PUMPPORTAL_WSS = "wss://pumpportal.fun/api/data"

# RugCheck API
RUGCHECK_API = "https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"

# Supabase DB Config
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Paper Trading Config
PAPER_PORTFOLIO_FILE = "portfolio.json"
INITIAL_BALANCE_USD = 120.0  # торговый пул ($150 депо - $30 газ)    # Стартовый капитал
REINVEST_PERCENT = 5.0  # Было 3%: цель $10/день требует сайз. 5% пула на сделку
VIRTUAL_POSITION_SIZE_USD = 4.0
TRADE_AMOUNT_USD = 10.0  # Было $6: базовый ордер $10 (комиссия $0.20 = всего 2% вместо 3.3%)
MAX_CONCURRENT_POSITIONS = 10  # концентрация капитала: максимум 10 ракет  # Режим Снайпера: максимум 5 сделок одновременно

# Risk Management
MAX_DAILY_LOSS_USD = 18.0
KILL_SWITCH_ENABLED = False  # ВЫКЛ по требованию: торговать и в красный день (риск: без стопа минус не ограничен)
KILL_SWITCH_PAUSE = 300  # Было 3600: пауза 5 мин вместо часа (блокирует условие дневного минуса, а не сон)
MAX_DAILY_LOSS_PCT = 0.25
STOP_LOSS_PCT = -0.20  # Вернули по просьбе: стоп -20% чтобы ракеты дышали
SNIPER_ENTRIES_ENABLED = False
TIME_EXIT_MINUTES = 60  # Если за 30 минут нет пампа - выходим
TIME_EXIT_PROFIT_REQ = 0.0

# Trailing Stop Config (Защита прибыли)
TRAILING_ACTIVATION_PCT = 0.25  # Активируем трейлинг уже при +25% (было +15% — слишком рано, резало профит)
TRAILING_DISTANCE_PCT = 0.08  # базовый для скальпа; раннеры после мунбэга едут шире (см. EVM_RUNNER_TRAIL)
# === RUNNER MODE (SHCAT +1126%/+627%, 中国人能飞 +152% держались ~5ч, пик==сейчас) ===
# При каких параметрах они выжили и фильтры их НЕ выбили — фиксируем как норму:
# pnl>+5%, maxp>+5%, без -20% краша за 60с, без -8% от пика. Новое: в прибыли время не режем,
# трейлинг расширяем, стопы от входа отключаем (только от пика).
EVM_RUNNER_MAXP = 0.50  # +50% пик = раннер: Dead/Stagnant/Stop от входа OFF, только трейлинг от пика
EVM_RUNNER_TRAIL = 0.25  # трейлинг раннера 25% от пика (было 8% — резало CORGI +294%→+153%, $THRONE +121%→+10%)
EVM_MOONBAG_TRAIL = 0.20  # после мунбэга (+60%, деньги в кармане) даём дышать 20% вместо 8%
EVM_RUNNER_CRASH = 0.30  # Crash Guard для раннера: -30% за 60с вместо -20% (шум вертикали)
EVM_DEAD_MIN = 60  # было 30: флет-раннеры консолидируются часами перед выстрелом
EVM_STAGNANT_MIN = 45  # было 15: SHCAT-подобные сидели бы в ~0% первые 15-30 мин и их бы выбило

# Moonbag: частичная фиксация 50% позиции на этом профите (свип: 0.60 лучше 0.50 - ранняя фикса режет раннеры)
MOONBAG_TRIGGER_PCT = 0.60
# Stagnant: минус режем на N-й минуте, мелкий плюс держим до M-й (бектест-свип)
STAGNANT_LOSS_MIN = 15
STAGNANT_HOLD_MIN = 25

# Filtering
AI_MODE = "degen" # "sniper" (строго 80-90% уверенности) или "degen"
MIN_LIQUIDITY = 20000  # Снижено для скальпинга обычных монет
MAX_LIQUIDITY = 50000000

# AI Аналитика
GEMINI_API_KEY = "AQ.Ab8RN6Ju77t6DI8AYru7TGxuPuG_0WOcqHZqq1OBsDAwHtoJxg" # Получить бесплатно на https://aistudio.google.com/

# Birdeye API Key (free: bds.birdeye.so, 30K CU/мес; token_trending = 50 CU/запрос)
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")
BIRDEYE_INTERVAL = 1800  # Ротация 4 сетей по 1 запросу: ~48 запросов/сутки. На free (20/сутки) хватит частично - ключ спит без CU
BIRDEYE_CHAINS = ["robinhood", "solana", "robinhood", "base", "robinhood", "bsc"]  # ROB каждый второй тик (50% квоты)

# Jupiter Tokens API v2 (free key: portal.jup.ag): recent + toptrending/5m для Solana
JUP_API_KEY = os.getenv("JUP_API_KEY", "")
JUP_INTERVAL = 120  # 2 запроса за цикл - безопасно для любого тира


# === JITO BLOCK ENGINE (MEV Protection) ===
USE_JITO_EXECUTION = True # Поставь True, когда будешь готов торговать на реальные деньги
JITO_ENGINE_URL = "https://mainnet.block-engine.jito.wtf/api/v1/bundles"
JITO_TIP_AMOUNT_SOL = 0.0005 # Чаевые валидатору (минимум 0.0001)
JITO_TIP_ACCOUNT = "96gYZGLnJYVFmbjzopPSU6QiCRK4rPdTuQ8hB1aP442b" # Официальный Jito Tip Account
LUNARCRUSH_API_KEY = "syvh43mkvrzrw54sor6kc5r6dmtyj5fhi4jd38ht"

# === ROBINHOOD CHAIN (EVM мемы, Arbitrum Orbit L2) ===
# Chain ID 4663, slug DexScreener/GeckoTerminal: "robinhood". Проверено живьём 2026-09-21:
# boosts/profiles DexScreener отдают chainId=robinhood, RPC отвечает 0x1237.
ROBINHOOD_ENABLED = True
ROBINHOOD_CHAIN_ID = 4663
ROBINHOOD_DS_SLUG = "robinhood"  # slug DexScreener (НЕ 4663 - тот вернёт пусто!)
ROBINHOOD_GT_NETWORK = "robinhood"  # slug GeckoTerminal
ROBINHOOD_RPC_URL = "https://rpc.mainnet.chain.robinhood.com"
ROBINHOOD_EXPLORER = "https://robinhoodchain.blockscout.com"
ROBINHOOD_MIN_LIQUIDITY = 8000  # EVM-пулы Uniswap тоньше Solana - порог ниже
ROBINHOOD_SCAN_INTERVAL = 15  # Было 25: чаще опрос = раньше вход на ракету
EVM_MIN_M5_PCT = 5.0  # Было 7.0: шире окно + вето 0.58 держит раги. Больше кандидатов в ракеты
EVM_RESCAN_COOLDOWN = 90  # Было 300: ракеты живут минуты, повторная проверка через 90 сек
EVM_NEW_POOL_PAGES = 8  # Было 4: глубже свежие пулы GT = больше ранних ракет Robinhood
EVM_MAX_MINTS = 100  # Было 60: хвост выдачи больше не отрезается
# EVM-копитрейдинг: кошельки китов по сетям (0x...). Их входящие Transfer = покупки:
# токен летит в скан первым с меткой COPY. Пусто = выключено (нужны адреса!).
EVM_COPY_WALLETS = {"base": [], "bsc": [], "robinhood": [], "ethereum": []}
# fomoapi.io — независимое API данных fomo.family (топ-трейдеры, доски, WS-алерты).
# Бесплатный ключ: fomoapi.io/dashboard (250k кредитов/мес). Без ключа — демо WS с задержкой 60с.
FOMO_API_KEY = ""
FOMO_WS_ENABLED = True
FOMO_FOLLOW_TRADERS = []  # ники топов, напр. ["whatever_fomo", "pricedin"] — их покупки в приоритет
FOMO_MIN_USD = 100  # игнорить покупки меньше $100 (пыль)
FOMO_BOARDS_INTERVAL = 7200  # доски trending/graduated раз в 2ч (экономия кредитов)
# GMGN живой мост (headless-Chromium ловит create-сигналы trenches).
# ТЯЖЁЛЫЙ: +200-400MB RAM, на бесплатном Render не влезет. Включать на тарифе 2GB+
# (и в build добавить: playwright install chromium) либо локально.
GMGN_ENABLED = False
GMGN_CHAINS = ["sol", "base", "bsc", "robinhood"]

# === BASE (EVM L2, там сидят мемы с fomo.family: musebook, DELTA...) ===
# Тот же EVM-движок, slug DexScreener "base". Пулы глубже - порог $15к.
BASE_ENABLED = True
BASE_SCAN_INTERVAL = 15  # было 25: чаще опрос = раньше вход

# === EVM WSS factory-listener (новые пулы в реальном времени, Base+BSC) ===
# Без ключей (PublicNode). Robinhood публичного WSS не даёт - только опрос.
EVM_WSS_ENABLED = True

# === BSC (GSTOCK и co с fomo.family сидят там) ===
# Тот же движок, slug "bsc".
BSC_ENABLED = True
BSC_SCAN_INTERVAL = 15  # было 25

# === ETHEREUM (KLIK: TG-SIGNAL:ETHEREUM висел на $0.00 — ни один луп его не вёл,
# chain=ethereum не входил в CHAINS. Тот же EVM-движок, slug DexScreener "ethereum".)
ETHEREUM_ENABLED = True  # луп трекинга: ведём существующие (KLIK), иначе висят на $0.00
ETHEREUM_ENTRIES_ENABLED = False  # НОВЫЕ входы в L1 выкл: газ $2-6 съедает скальп, ловим только дешёвые L2
ETHEREUM_SCAN_INTERVAL = 15
ETHEREUM_MIN_LIQUIDITY = 15000.0  # пулы глубокие, как Base/BSC
# === КОМИССИИ ПО СЕТЯМ (paper-честность: L1-газ на порядок дороже L2/Solana) ===
# (fee_small <$10, fee_big, fee_emergency, cap_frac от позиции)
# Было везде одинаково $0.075/$0.45/$0.75 cap 5% — для Ethereum это враньё:
# реальный своп L1 $2-5, аварийный с приоритетом $6+.
EVM_CHAIN_FEES = {
    "solana": (0.075, 0.45, 0.75, 0.05),
    "robinhood": (0.075, 0.45, 0.75, 0.05),
    "base": (0.075, 0.45, 0.75, 0.05),
    "bsc": (0.075, 0.45, 0.75, 0.05),
    "ethereum": (2.0, 3.5, 6.0, 0.20),
}
ETHEREUM_MIN_SIZE_USD = 15.0  # меньше $15 в L1 не входим: газ съест любой скальп

# --- Сайзинг: база $6, conviction x2 ---
# Тир решает ПОДТВЕРЖДЁННЫЙ импульс рынка (m5 + вести), а не скор модели
# (модель всем ставит 100%, а катастрофы были именно VIP-100%)
CONVICTION_MULT = 2.0  # conviction-вход едет удвоенным
CONVICTION_MIN_M5_PCT = 0.20  # m5 от +20%
CONVICTION_MAX_M5_PCT = 0.60  # m5 до +60% (выше - вершина)
CONVICTION_MIN_BUYSELL = 2.0  # покупки >= продаж x2
CONVICTION_MIN_LIQ = 30000  # пул от $30к
MAX_DEPLOYED_PCT = 0.60  # суммарно в рынке не больше 60% капитала (сдерживает тиринг)
RUG_REBUY_MAX_LOSS = -0.15  # был лосс хуже -15% по монете (HYDX -52%) - второй раз не входим, раги не оживают

# === GROWTH MODE (тренд старых токенов, пока нет ракет) ===
# Логика: ракеты ловятся импульсом m5, а зрелые капы едут часами. Отдельный режим:
# вход в откат часового тренда, широкие стопы, удержание часами, мало сделок.
GROWTH_ENABLED = True
GROWTH_WATCHLIST = [  # ликвидные Solana-капы (путать не с чем, рага не будет)
    "So11111111111111111111111111111111111111112",  # SOL
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",  # JUP
    "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",  # RAY
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
    "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",  # WIF
    "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",  # ORCA
]
GROWTH_MIN_LIQ = 200000  # пул от $200к - проскальзывания нет
GROWTH_MIN_H24_PCT = 8.0  # Было 5.0: входы на +7% гнили во флете (SPX/BOME -3%). Тренд от +8%
GROWTH_MIN_H1_PCT = 3.0  # Было 1.0: брали вялые +1.0-1.9%. Вход от +3%
GROWTH_MAX_H1_PCT = 15.0  # выше +15% за час - вершина, не гонимся
GROWTH_MAX_H6_PCT = 80.0  # выше +80% за 6ч - перегрев
GROWTH_MIN_BUYSELL = 1.2  # покупки >= продаж x1.2 за h1
GROWTH_STOP_PCT = -0.10  # широкий стоп -10% (шум часовок)
GROWTH_TRAIL_ACT = 0.08  # трейлинг с +8%
GROWTH_TRAIL_DIST = 0.12  # дистанция 12%
GROWTH_STAGNANT_MIN = 120  # флет режем через 2 часа, не 7 минут
GROWTH_MAX_POS = 3  # не больше 3 трендовых позиций
GROWTH_SIZE_USD = 6.0
GROWTH_INTERVAL = 300  # опрос вотчлиста каждые 5 мин

# === TG CALLS (живые коллы из Telegram-каналов) ===
# Бесплатно: API ID+Hash с https://my.telegram.org -> env TG_API_ID/TG_API_HASH.
# Первый запуск локально (создаст .session), дальше работает везде.
TG_ENABLED = True
TG_CHANNELS = ["lxetrades"]  # добавь свои: ["lxetrades", "calls_channel", ...]
TG_SESSION = "sniper_session"
# Бесключевой сборщик коллов через t.me/s/ превью (без Telethon и API-ключей).
# Проверено: pumpfunmemecalls отдаёт контракты. solanamemeradar закрылся - не добавлять.
TG_PREVIEW_ENABLED = True
TG_PREVIEW_CHANNELS = ["pumpfunmemecalls"]
TG_PREVIEW_INTERVAL = 90  # секунд между опросами (вежливо, превью не банит)

# === USER WATCHLIST: ты видишь рано на fomo.family - кидаешь контракт сюда, бот исполняет ===
# Формат: ["mint_solana", "0xEVM..."]. Проверка теми же гейтами (скам не пройдет),
# но без ожидания бустов/трендов. Именно так ловятся AMERICA/YAP до +1000%.
USER_WATCHLIST = []

SNIPER_MIN_UNIQUE_BUYERS = 10
SNIPER_BUYERS_WINDOW_MIN = 5
VIP_MAX_M5_PCT = 1.50  # Было 0.60: резали настоящие ракеты. 150% - пропускаем только реальные пики
PULLBACK_MIN_M5_PCT = 0.03  # Было 0.05: расширяем сеть входа
PULLBACK_M1_MIN_PCT = -0.15  # Но и не летим в падающий нож (максимум -15% за минуту)
PULLBACK_M1_MAX_PCT = 0.05  # Было 0.00 - ждали идеальный откат. Ракеты летят с m1 > 0
PULLBACK_MAX_H1_PCT = 3.00  # Было 10.00 (1000%) - брали вершины типа +3152% Drip / +33009% SI. Теперь >+300% за час = поздно
LOTTERY_MIN_M5_PCT = 0.80  # Лотерея только для мощных вертикалей от 80%
LOTTERY_SIZE_MULT = 0.25
WHALE_CONSENSUS = 2  # вход на 2-м ките (3-й = уже поздно)
WHALE_MAX_RUNUP = 1.35  # цена не должна вырасти >35% с момента покупки первого кита
