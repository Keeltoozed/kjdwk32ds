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
VIP_SIZE_MULT = 1.5  # Ракетам с VIP-объёмом - полуторный сайз (до $15, режет кэп пула)
MAX_CONCURRENT_POSITIONS = 10  # концентрация капитала: максимум 10 ракет  # Режим Снайпера: максимум 5 сделок одновременно

# Risk Management
MAX_DAILY_LOSS_USD = 18.0
KILL_SWITCH_ENABLED = True
MAX_DAILY_LOSS_PCT = 0.25
STOP_LOSS_PCT = -0.20  # Вернули по просьбе: стоп -20% чтобы ракеты дышали
SNIPER_ENTRIES_ENABLED = False
TIME_EXIT_MINUTES = 60  # Если за 30 минут нет пампа - выходим
TIME_EXIT_PROFIT_REQ = 0.0

# Trailing Stop Config (Защита прибыли)
TRAILING_ACTIVATION_PCT = 0.15  # Активируем трейлинг уже при +15% (было +30% — слишком поздно)
TRAILING_DISTANCE_PCT = 0.08  # Свип 45 комбо x 10 сценариев: 0.08 лучший и на гладких, и на шумных (+2.92 vs +1.64 у 0.10)

# Moonbag: частичная фиксация 50% позиции на этом профите (свип: 0.60 лучше 0.50 - ранняя фикса режет раннеры)
MOONBAG_TRIGGER_PCT = 0.60
# Stagnant: минус режем на N-й минуте, мелкий плюс держим до M-й (бектест-свип)
STAGNANT_LOSS_MIN = 7
STAGNANT_HOLD_MIN = 25

# Filtering
AI_MODE = "degen" # "sniper" (строго 80-90% уверенности) или "degen"
MIN_LIQUIDITY = 20000  # Снижено для скальпинга обычных монет
MAX_LIQUIDITY = 50000000

# AI Аналитика
GEMINI_API_KEY = "AQ.Ab8RN6Ju77t6DI8AYru7TGxuPuG_0WOcqHZqq1OBsDAwHtoJxg" # Получить бесплатно на https://aistudio.google.com/

# Birdeye API Key
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")


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
ROBINHOOD_SCAN_INTERVAL = 45  # секунд между опросами boosts/profiles

# --- Сайзинг: база $6, conviction x2 ---
# Тир решает ПОДТВЕРЖДЁННЫЙ импульс рынка (m5 + вести), а не скор модели
# (модель всем ставит 100%, а катастрофы были именно VIP-100%)
CONVICTION_MULT = 2.0  # conviction-вход едет удвоенным
CONVICTION_MIN_M5_PCT = 0.20  # m5 от +20%
CONVICTION_MAX_M5_PCT = 0.60  # m5 до +60% (выше - вершина)
CONVICTION_MIN_BUYSELL = 2.0  # покупки >= продаж x2
CONVICTION_MIN_LIQ = 30000  # пул от $30к
MAX_DEPLOYED_PCT = 0.60  # суммарно в рынке не больше 60% капитала (сдерживает тиринг)

SNIPER_MIN_UNIQUE_BUYERS = 10
SNIPER_BUYERS_WINDOW_MIN = 5
VIP_MAX_M5_PCT = 0.60  # Было 1.00 - брали вершину +100%. 60% - компромисс: ракеты пропускаем, вертикали нет
PULLBACK_MIN_M5_PCT = 0.07  # Было 0.10: чуть шире импульс = больше кандидатов в ракеты
PULLBACK_M1_MIN_PCT = -0.15  # Но и не летим в падающий нож (максимум -15% за минуту)
PULLBACK_M1_MAX_PCT = 0.00  # Было 0.08 - покупали зеленую свечу = вершину. Вернули 0.00: ждем откат
PULLBACK_MAX_H1_PCT = 3.00  # Было 10.00 (1000%) - брали вершины типа +3152% Drip / +33009% SI. Теперь >+300% за час = поздно
VIP_MAX_M5_PCT = 0.60  # дубль ниже - держим 60%
LOTTERY_MIN_M5_PCT = 0.80  # Лотерея только для мощных вертикалей от 80%
LOTTERY_SIZE_MULT = 0.25
WHALE_CONSENSUS = 2  # вход на 2-м ките (3-й = уже поздно)
WHALE_MAX_RUNUP = 1.35  # цена не должна вырасти >35% с момента покупки первого кита
