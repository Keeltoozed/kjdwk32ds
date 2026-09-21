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
REINVEST_PERCENT = 3.0  # СРОЧНО УРЕЗАНО: 5% -> 3% пока бот в минусе (меньше размер = дольше живем)
VIRTUAL_POSITION_SIZE_USD = 4.0
TRADE_AMOUNT_USD = 4.0  # СРОЧНО УРЕЗАНО: было $6, стало $4
MAX_CONCURRENT_POSITIONS = 10  # Вернули по просьбе: было 3 -> снова 10

# Risk Management
MAX_DAILY_LOSS_USD = 18.0  # Вернули по просьбе: было 10 -> снова 18
KILL_SWITCH_ENABLED = True
MAX_DAILY_LOSS_PCT = 0.25
STOP_LOSS_PCT = -0.20  # Вернули по просьбе: было -12% -> снова -20%
SNIPER_ENTRIES_ENABLED = False
TIME_EXIT_MINUTES = 60  # Если за 30 минут нет пампа - выходим
TIME_EXIT_PROFIT_REQ = 0.0

# Trailing Stop Config (Защита прибыли)
TRAILING_ACTIVATION_PCT = 0.20  # Было 0.15 - слишком рано дергало, но сейчас главное резать убытки
TRAILING_DISTANCE_PCT = 0.20  # Было 0.30 - слишком широко, отдавали весь профит обратно

# Filtering
AI_MODE = "degen" # "sniper" (строго 80-90% уверенности) или "degen"
MIN_LIQUIDITY = 25000  # СРОЧНО ПОДНЯТО: было 10000 - микро-пулы давали 100% проскальзывание и Crash Guard -50%
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

SNIPER_MIN_UNIQUE_BUYERS = 15
SNIPER_BUYERS_WINDOW_MIN = 5
VIP_MAX_M5_PCT = 0.40  # СРОЧНО: было 1.00 (100%) - покупали вершину вертикали. Теперь >40% за 5мин = перегрев, пропуск
PULLBACK_MIN_M5_PCT = 0.20  # Было 0.10 - брали вялые +10% без импульса. Теперь нужен реальный импульс от +20%
PULLBACK_M1_MIN_PCT = -0.08  # Было -0.15 - позволяли брать падающий нож. Теперь откат не глубже -8%
PULLBACK_M1_MAX_PCT = -0.01  # Было 0.00 - покупали зеленую свечу (вершину). Теперь m1 ДОЛЖНА быть красной минимум -1%
PULLBACK_MAX_H1_PCT = 10.00
LOTTERY_MIN_M5_PCT = 2.00  # СРОЧНО: было 0.80 - лотерея на +80% это покупка вершины. Отключаем лотерею (x200% нереально)
LOTTERY_SIZE_MULT = 0.25
WHALE_CONSENSUS = 2  # вход на 2-м ките (3-й = уже поздно)
WHALE_MAX_RUNUP = 1.35  # цена не должна вырасти >35% с момента покупки первого кита
