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
VIRTUAL_POSITION_SIZE_USD = 4.0
MAX_CONCURRENT_POSITIONS = 5

# Risk Management
STOP_LOSS_PCT = -0.20   # Жесткий стоп-лосс -20% (раньше было -50%)
TIME_EXIT_MINUTES = 120 # Увеличили время жизни до 2 часов
TIME_EXIT_PROFIT_REQ = 0.0 # Выходим по времени, только если позиция вообще не в плюсе

# Trailing Stop Config
TRAILING_ACTIVATION_PCT = 0.20 # Включаем трейлинг при +20% профита
TRAILING_DISTANCE_PCT = 0.20   # Даем монете "подышать". Ракеты часто падают на 15-20% перед новым рывком на 500%.

# Filtering
MIN_LIQUIDITY = 2000  # Снизили до $2k, чтобы ловить микрокапы (<10k), которые дают 1000x
MAX_LIQUIDITY = 50000000 
MIN_AGE_MINUTES = 2  # От 2 минут. Смотрим даже на самые свежие токены.
MAX_AGE_MINUTES = 129600 # До 90 дней (129600 минут).
