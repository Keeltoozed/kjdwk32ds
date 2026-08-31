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
TIME_EXIT_MINUTES = 45
TIME_EXIT_PROFIT_REQ = 0.05 # +5%

# Trailing Stop Config
TRAILING_ACTIVATION_PCT = 0.40 # Включаем трейлинг, когда прибыль достигает +40%
TRAILING_DISTANCE_PCT = 0.15   # Откатываемся не более чем на 15% от максимальной цены

# Filtering
MIN_LIQUIDITY = 3500
MAX_LIQUIDITY = 80000
MIN_AGE_MINUTES = 5
MAX_AGE_MINUTES = 360
