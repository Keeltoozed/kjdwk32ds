import os
from dotenv import load_dotenv

load_dotenv()

# Dexscreener API
DEXSCREENER_LATEST = "https://api.dexscreener.com/token-profiles/latest/v1"
DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/tokens/"

# RugCheck API
RUGCHECK_API = "https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"

# Paper Trading Config
PAPER_PORTFOLIO_FILE = "portfolio.json"
VIRTUAL_POSITION_SIZE_USD = 4.0
MAX_CONCURRENT_POSITIONS = 5

# Risk Management
STOP_LOSS_PCT = -0.50   # Изначальный стоп-лосс -50%
TIME_EXIT_MINUTES = 120 # Увеличили время жизни до 2 часов
TIME_EXIT_PROFIT_REQ = 0.0 # Выходим по времени, только если позиция вообще не в плюсе

# Trailing Stop Config
TRAILING_ACTIVATION_PCT = 0.60 # Включаем трейлинг только после достижения +60% прибыли
TRAILING_DISTANCE_PCT = 0.30   # Даем монете пространство для коррекции до 30% от максимума

# Filtering
MIN_LIQUIDITY = 10000  # Подняли с 1000 до 10к, чтобы избежать микрокап-скамов
MAX_LIQUIDITY = 500000 # Увеличили верхнюю границу
MIN_AGE_MINUTES = 10   # Не покупаем монеты младше 10 минут (самые опасные)
MAX_AGE_MINUTES = 1440
