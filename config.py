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
STOP_LOSS_PCT = -0.12   # Затянули стоп-лосс до 12%, чтобы жестко резать минуса
TIME_EXIT_MINUTES = 60  # Снизили до 1 часа. Если не выстрелила - выходим.
TIME_EXIT_PROFIT_REQ = 0.0

# Trailing Stop Config
TRAILING_ACTIVATION_PCT = 0.12 # Включаем трейлинг уже при +12% профита
TRAILING_DISTANCE_PCT = 0.08   # Трейлим близко (8%), чтобы не отдавать прибыль обратно рынку.

# Filtering
MIN_LIQUIDITY = 2000  # Снизили до $2k, чтобы ловить микрокапы (<10k), которые дают 1000x
MAX_LIQUIDITY = 50000000 
MIN_AGE_MINUTES = 40  # Ищем только зрелые монеты, которые пережили первый дамп
MAX_AGE_MINUTES = 129600 # До 90 дней (129600 минут).
