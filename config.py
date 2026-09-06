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

# Paper Trading Config
PAPER_PORTFOLIO_FILE = "portfolio.json"
INITIAL_BALANCE_USD = 20.0    # Стартовый капитал
REINVEST_PERCENT = 10.0       # Процент от капитала на одну сделку
VIRTUAL_POSITION_SIZE_USD = 4.0 # (Устарело) базовый размер сделки
MAX_CONCURRENT_POSITIONS = 15 # Увеличили макс. количество одновременных сделок (было 5)

# Risk Management
STOP_LOSS_PCT = -0.15   # Жесткий стоп на -15% (чтобы с учетом проскальзывания было не больше -20%)
TIME_EXIT_MINUTES = 30  # Если за 30 минут нет пампа - выходим
TIME_EXIT_PROFIT_REQ = 0.0

# Trailing Stop Config (Защита прибыли)
TRAILING_ACTIVATION_PCT = 0.15 # Включаем трейлинг уже при +15% профита!
TRAILING_DISTANCE_PCT = 0.05   # Держим стоп на 5% ниже пика. Если выросли на 15%, стоп сдвигается в +10% (Безубыток)

# Filtering
AI_MODE = "sniper" # "sniper" (очень редко, только 90% уверенности) или "degen" (частые сделки, от 60% уверенности)
MIN_LIQUIDITY = 15000  # Увеличили до 15k! При ликвидности 3k любой чих обваливает цену на 30%, пробивая наш стоп-лосс.
MAX_LIQUIDITY = 50000000 
MIN_AGE_MINUTES = 5   # Ищем монеты от 5 минут (раньше было 40 минут, бот пропускал весь рост!)
MAX_AGE_MINUTES = 129600

# AI Аналитика
GEMINI_API_KEY = "AQ.Ab8RN6Ju77t6DI8AYru7TGxuPuG_0WOcqHZqq1OBsDAwHtoJxg" # Получить бесплатно на https://aistudio.google.com/
