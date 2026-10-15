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
def _clean_supabase_url(raw: str) -> str:
    u = (raw or "").strip().strip('"').strip("'").rstrip("/")
    # Частая ошибка: вставляют URL с суффиксом /rest/v1, /auth/v1 или dashboard-ссылку.
    # PostgREST тогда отвечает PGRST125 Invalid path. Отрезаем до https://xxx.supabase.co
    for suffix in ("/rest/v1", "/auth/v1", "/realtime/v1", "/storage/v1"):
        if u.endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
    # dashboard-ссылка вида https://supabase.com/dashboard/project/<ref> -> чиним
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

# Paper Trading Config
PAPER_PORTFOLIO_FILE = "portfolio.json"
# Ключ слепка в Supabase. ВАЖНО: у каждого бота свой, иначе два бота на одной
# базе перезаписывают друг друга (сделки появляются и пропадают).
# Задай в Render → Environment: первый бот PF_IDEBOT, второй PF_QYO7.
PORTFOLIO_SNAPSHOT_MINT = (os.getenv("PORTFOLIO_SNAPSHOT_MINT", "") or "").strip().strip('"').strip("'") or "PORTFOLIO_STATE_V4"
INITIAL_BALANCE_USD = 120.0  # торговый пул ($150 депо - $30 газ)    # Стартовый капитал
REINVEST_PERCENT = 5.0  # Было 3%: цель $10/день требует сайз. 5% пула на сделку
VIRTUAL_POSITION_SIZE_USD = 15.0
TRADE_AMOUNT_USD = 15.0  # Базовый ордер $15 (по запросу пользователя)
MAX_CONCURRENT_POSITIONS = 10  # концентрация капитала: максимум 10 ракет  # Режим Снайпера: максимум 5 сделок одновременно

# Risk Management
MAX_DAILY_LOSS_USD = 18.0
KILL_SWITCH_ENABLED = True
MAX_DAILY_LOSS_PCT = 0.25
STOP_LOSS_PCT = -0.14  # Стоп -14% (было -20%, что при слипе давало -31% убытка)
SNIPER_ENTRIES_ENABLED = False
VIP_ENTRIES_ENABLED = False  # ОТКЛЮЧЕНО: защита от фейковых шипов и wash-trading раздач (UNPEG/FIBONACCI)
TIME_EXIT_MINUTES = 60  # Если за 30 минут нет пампа - выходим
TIME_EXIT_PROFIT_REQ = 0.0

# Take-Profit Ladder (Лестница фиксации прибыли — защита от гэп-дампов китов)
TP1_TRIGGER_PCT = 0.25   # +25%: фиксируем 20% позиции (подушка безопасности)
TP2_TRIGGER_PCT = 1.00   # +100% (2x удвоение!): фиксируем 35% позиции — полный возврат тела входа ($15 в карман!)
MOONBAG_TRIGGER_PCT = 2.00 # +200% (3x утроение!): фиксируем 50% оставшейся позиции

# Trailing Stop & Profit Protection (Идеальный баланс: дышит при шуме 8-10%, но не отдает пик)
TRAILING_ACTIVATION_PCT = 0.15  # Начинаем трейлинг от +15%
TRAILING_DISTANCE_PCT = 0.12    # 12% воздуха (держит микро-тени свечей 8-10%, не выбивая ракету)
TRAILING_RUNNER_DISTANCE_PCT = 0.15 # 15% воздуха на раннерах (+50%..+100%)
SCALP_PROFIT_ENABLED = False    # Скальп-срез +5% выключен

# Whale Dump Guard & Crash Protection
WHALE_DUMP_GUARD_PCT = 0.15     # Слив на 15% за <15с при наличии прибыли — моментальный выход
EVM_RUNNER_CRASH = 0.20         # Сброс на 20% за 60с режем сразу (не ждём дальнейшего дампа)
EVM_NORMAL_CRASH = 0.20         # Сброс на 20%

# Breakeven: пик был +15%+, а цена вернулась ко входу — импульс угас, выходим в безубыток
BREAKEVEN_PCT = 0.15

# Infant Dump: защита от дев-дампа в первые минуты
PREFETCH_TTL_SEC = 10  # Пакетный кэш DexScreener (до 30 токенов за запрос): сек жизни данных для анализа
SOLANA_MIN_AGE_MIN = 2.5  # Не входить в пулы Solana моложе 2.5 мин (flash-rug зона дева)
INFANT_WINDOW_MIN = 2.0
INFANT_DUMP_PCT = -0.15  # Резать сразу при -15% в первые 2 мин (было -22%, давало -28% на Taku)

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
ROBINHOOD_MIN_LIQUIDITY = 3000.0  # Порог $3.0k: пулы GIGALAUDIA ($3.3k) и NIHILISM ($3.4k) забирают ракеты x2.5
ROBINHOOD_MIN_AGE_MIN = 2.5  # 2.5 мин (как Solana, не пропускает ранние импульсы)
ROBINHOOD_SCAN_INTERVAL = 25  # 25с: быстрый опрос (BSC отключен, лимиты не превышаются)
EVM_MIN_M5_PCT = 2.0  # Ранние импульсы от +2% пускаем (не отсекать ранние старты вроде binancians +2.6%, 旺柴 +2.8%)
EVM_RESCAN_COOLDOWN = 180  # Перепроверка через 3 мин (волна может прийти позже)
EVM_MAX_MINTS = 120  # Лимит активных кандидатов за цикл скана


# === BASE (EVM L2, там сидят мемы с fomo.family: musebook, DELTA...) ===
# Тот же EVM-движок, slug DexScreener "base".
BASE_ENABLED = True
BASE_MIN_LIQUIDITY = 3000.0  # Порог $3.0k: стандартные Clanker/Uniswap v3 мемы на Base стартуют с ~$3k
BASE_SCAN_INTERVAL = 25  # 25с: быстрый поиск ранних ракет Base

# === BSC (ОТКЛЮЧЕНО: 100% сделок на BSC обернулись дев-рагпуллами с выводом пула) ===
BSC_ENABLED = False
BSC_MIN_LIQUIDITY = 6000.0

# --- Сайзинг: база $6, conviction x2 ---
# Тир решает ПОДТВЕРЖДЁННЫЙ импульс рынка (m5 + вести), а не скор модели
# (модель всем ставит 100%, а катастрофы были именно VIP-100%)
CONVICTION_MULT = 2.0  # conviction-вход едет удвоенным
CONVICTION_MIN_M5_PCT = 0.20  # m5 от +20%
CONVICTION_MAX_M5_PCT = 0.60  # m5 до +60% (выше - вершина)
CONVICTION_MIN_BUYSELL = 2.0  # покупки >= продаж x2
CONVICTION_MIN_LIQ = 30000  # пул от $30к
MAX_DEPLOYED_PCT = 0.60  # суммарно в рынке не больше 60% капитала (сдерживает тиринг)

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
GROWTH_SIZE_USD = 15.0  # Ордер $15 (по запросу пользователя, было 6.0)
GROWTH_INTERVAL = 300  # опрос вотчлиста каждые 5 мин

SNIPER_MIN_UNIQUE_BUYERS = 10
SNIPER_BUYERS_WINDOW_MIN = 5
VIP_MAX_M5_PCT = 0.60  # Было 1.00 - брали вершину +100%. 60% - компромисс: ракеты пропускаем, вертикали нет
PULLBACK_MIN_M5_PCT = 0.025  # Было 0.05: не отсекаем зарождающийся импульс (+2.5%..+4.9%) как флет
PULLBACK_M1_MIN_PCT = -0.15  # Но и не летим в падающий нож (максимум -15% за минуту)
PULLBACK_M1_MAX_PCT = 0.20  # Поднято до 20%: позволяет заходить в реальный пробойный импульс (breakout), а не ждать затухания
PULLBACK_MAX_H1_PCT = 3.00  # До +300% за час
LOTTERY_MIN_M5_PCT = 0.60  # Было 0.80: закрываем мертвую зону m5 60-80% (VIP_MAX 60% + лотерея от 60%)
LOTTERY_SIZE_MULT = 0.30  # $10 * 0.30 = $3.0 билет (было 0.25 = $2.5)
WHALE_CONSENSUS = 2  # вход на 2-м ките (3-й = уже поздно)
WHALE_MAX_RUNUP = 1.35  # цена не должна вырасти >35% с момента покупки первого кита

# === FAST STREAM (0-сек discovery, идея DoradoDevs/solana-pumpfun-sniper-bot) ===
FAST_STREAM_ENABLED = True
FAST_WINDOW_SEC = 120   # окно ранней тяги: уникальные buyers за первые 2 мин
FAST_MIN_BUYERS = 6     # >=6 уникальных покупателей за 2 мин = горячий
FAST_MIN_TRADES = 10    # или >=10 трейдов за окно
FAST_EARLY_MAX_AGE_SEC = 180  # early-микро-вход только если токену <3 мин (DS ещё пуст)

# === ДОП. ИСТОЧНИКИ (портировано из прим; фильтры режут мусор, как раньше) ===
# Jupiter Tokens API v2 (free key: portal.jup.ag). Без ключа jup_loop спит.
JUP_API_KEY = os.getenv("JUP_API_KEY", "")
JUP_INTERVAL = 120

# TG-превью коллов: ОТКЛЮЧЕНО (защита от скам-коллов и дев-дампов)
TG_PREVIEW_ENABLED = False
TG_PREVIEW_CHANNELS = ["pumpfunmemecalls", "pumpfunearlytrending", "cherrytrending"]
TG_PREVIEW_INTERVAL = 90

# fomoapi.io WS: ОТКЛЮЧЕНО
FOMO_API_KEY = os.getenv("FOMO_API_KEY", "")
FOMO_WS_ENABLED = False
FOMO_FOLLOW_TRADERS = []
FOMO_MIN_USD = 100
FOMO_BOARDS_INTERVAL = 7200
USE_FOMO_SIGNALS = False  # ОТКЛЮЧЕНО: исключает попадание любых токсичных памп-энд-дамп коллов в бота
MAX_FOMO_BUYS_PER_PASS = 3  # бёрст-контроль: не больше 3 входов за проход очереди
TG_MAX_SIZE_USD = 5.0  # кэп TG/FOMO-входа: $5 (защита от памп-энд-дампа каналов)
TG_VIP_SIZE_USD = 5.0
ROB_MAX_SIZE_USD = 15.0  # Robinhood: теперь защищён ончейн-проверкой и возрастом 3м+, сайз $15 (ставка не снижается)
BSC_MAX_SIZE_USD = 15.0
BSC_MIN_LIQUIDITY = 6000.0  # Пулы от $6к на BSC (вместо $12к)
BSC_WEAK_SIZE_USD = 15.0   # Сайз $15 по запросу пользователя (ставка не снижается)

# EVM tracking intervals (адаптивный опрос открытых позиций без 429)
EVM_TRACK_INTERVAL = 2.5  # Быстрый трекинг при открытых сделках (2.5с вместо 8с для мгновенных стопов)
EVM_IDLE_TRACK_INTERVAL = 6.0  # Опрос в режиме ожидания (нет открытых позиций)

# EVM factory WSS (новые пулы Base/BSC за секунды, без ключей, PublicNode)
EVM_WSS_ENABLED = True

# CoinGecko trending -> Solana-минты для GROWTH (без ключей, кэш 30 мин)
COINGECKO_ENABLED = True

# === SCOUT (ранний рукав: возраст 1-12 мин + velocity WSS, SPEC-кейс) ===
# Триггер по WSS-реестру (без DS): тяга есть, вертикаль m5 ещё не обязательно видна.
# Подтверждение по DS/Gecko: ликва, m1 не дамп, h24 не перегрев. Билет $3.
SCOUT_ENABLED = True
SCOUT_INTERVAL = 20  # опрос реестра каждые 20с
SCOUT_MIN_AGE_SEC = 60  # младше 1 мин — шум создания, DS пуст
SCOUT_MAX_AGE_SEC = 720  # старше 12 мин — уже сканер/lottery, не скаут
SCOUT_MIN_BUYERS = 5  # ≥5 уникальных покупателей WSS за окно
SCOUT_MIN_TRADES = 15  # или ≥15 трейдов WSS
SCOUT_MIN_LIQ = 15000  # пул от $15k (slippage $3 < 2%)
SCOUT_MAX_DUMP_M1 = -0.15  # m1 хуже -15% — дамп, не откат
SCOUT_SIZE_USD = 3.0
SCOUT_MAX_POS = 3  # не больше 3 скаутов открыто
SCOUT_RECOOLDOWN = 300  # перепроверка отклонённых через 5 мин
SCOUT_BIRTH_FALLBACK = False  # birth-добор без WSS тяги ВЫКЛ: тащил раги v0/0 (AMM/XRPN/CLTR)

# === SECURITY GATES (бесплатно, без ключей, кэш 1ч, fail-open) ===
GOPLUS_ENABLED = True
GOPLUS_MAX_SELL_TAX = 0.10
GOPLUS_CACHE_SEC = 3600
