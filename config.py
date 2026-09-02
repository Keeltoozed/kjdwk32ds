import os
from dotenv import load_dotenv

load_dotenv()

# Dexscreener API
DEXSCREENER_LATEST = "https://api.dexscreener.com/token-boosts/top/v1" # Сканируем топ активных (Trending) монет, а не просто новинки
DEXSCREENER_PROFILES = "https://api.dexscreener.com/token-profiles/latest/v1" # Сканируем свежие обновления профилей
DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/tokens/"

# RugCheck API
RUGCHECK_API = "https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"

# Paper Trading Config
PAPER_PORTFOLIO_FILE = "portfolio.json"
VIRTUAL_POSITION_SIZE_USD = 4.0
MAX_CONCURRENT_POSITIONS = 5

# Risk Management
STOP_LOSS_PCT = -0.20   # Жесткий стоп-лосс -20% (раньше было -50%)
TIME_EXIT_MINUTES = 120 # Увеличили время жизни до 2 часов
TIME_EXIT_PROFIT_REQ = 0.0 # Выходим по времени, только если позиция вообще не в плюсе

# Trailing Stop Config
TRAILING_ACTIVATION_PCT = 0.10 # Включаем трейлинг уже при +10% профита!
TRAILING_DISTANCE_PCT = 0.08   # Подтягиваем стоп на 8% от пика.

# Filtering
MIN_LIQUIDITY = 10000  # Нижний порог ликвидности
MAX_LIQUIDITY = 50000000 # Увеличили верхнюю границу до $50 млн, чтобы не отсеивать топ-токены
MIN_AGE_MINUTES = 5  # Вернули на 5 минут, чтобы ловить новые щитки как на скрине
MAX_AGE_MINUTES = 2880 # Максимум 2 дня
